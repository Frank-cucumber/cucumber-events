import os
import threading
import base64
from flask import Flask, render_template, request, redirect, url_for, abort, send_from_directory, flash, jsonify, session
from pathlib import Path
from datetime import date, datetime
import sqlite3
import sys
import re
import json
import time
import anthropic
import requests as req_lib
from concurrent.futures import ThreadPoolExecutor

ROOT      = Path(__file__).parent
_CREDS    = Path.home() / ".claude" / ".credentials.json"

# Load .env if present (local dev)
_env_file = ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# On Railway use /data (persistent volume); locally use project dir
_ON_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT"))
_DATA       = Path("/data") if _ON_RAILWAY else ROOT
PHOTO_DIR   = _DATA / "photos" / "web"

HF_URL          = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
_NB_PRO_URL     = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent"
_NB_FLASH_URL   = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"

_UNIFORM_PEOPLE = [
    "young Black female healthcare worker, short natural hair, warm smile",
    "middle-aged South Asian male healthcare worker, friendly expression",
    "young white female healthcare worker, blonde hair, bright smile",
    "older Black male healthcare worker, grey beard, confident smile",
    "young mixed-race female healthcare worker, curly hair, professional pose",
]
sys.path.insert(0, str(ROOT))
from generate_graphic import generate as gen_graphic, _parse_ics

app     = Flask(__name__)
app.secret_key = "cucumber-events-secret"
DB_PATH = _DATA / "events.db"
GFX_DIR = _DATA / "graphics" / "web"


# ── Database ─────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                event_date  TEXT,
                source      TEXT    DEFAULT 'custom',
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS variants (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id     INTEGER REFERENCES events(id) ON DELETE CASCADE,
                variant_num  INTEGER,
                headline     TEXT    NOT NULL,
                subtext      TEXT    NOT NULL,
                image_path   TEXT,
                generated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS votes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                variant_id  INTEGER REFERENCES variants(id) ON DELETE CASCADE,
                voter_name  TEXT,
                ip_address  TEXT,
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Migrate existing DBs that don't have ip_address yet
        cols = [r[1] for r in conn.execute("PRAGMA table_info(votes)").fetchall()]
        if "ip_address" not in cols:
            conn.execute("ALTER TABLE votes ADD COLUMN ip_address TEXT")


# ── AI tagline generation ─────────────────────────────────────────────

def _ai_client():
    # Prefer a proper API key (set this on Railway — never expires)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic()
    # Local fallback: use Claude Code OAuth token
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not token and _CREDS.exists():
        with open(_CREDS) as f:
            token = json.load(f)["claudeAiOauth"]["accessToken"]
    return anthropic.Anthropic(auth_token=token)

_VISUAL_APPROACHES = [
    "close-up of hands, symbolic objects only, no faces, abstract and emotive",
    "wide environmental shot, person small in frame, sense of space and context",
    "tight portrait, direct eye contact with camera, shallow depth of field, bokeh background",
    "overhead flat-lay of relevant objects arranged on a surface, graphic and clean",
    "candid side-on moment, subject unaware, documentary feel",
    "silhouette against bright background, dramatic and bold",
    "group of 2-3 people interacting, human connection, warm and natural",
    "single striking symbolic object centred in frame, minimalist",
    "action shot mid-movement, energy and dynamism",
    "split light, half shadow half warm light, moody and contemplative",
]

def ai_image_prompts(event_name, count=5, redo_indices=None):
    """Ask Claude for count complete image prompts for this event.
    redo_indices: list of 0-based variant positions being redone — forces fresh angles."""
    import random
    # Assign a distinct visual approach to each variant slot
    approaches = random.sample(_VISUAL_APPROACHES, min(count, len(_VISUAL_APPROACHES)))
    while len(approaches) < count:
        approaches.append(random.choice(_VISUAL_APPROACHES))

    redo_note = ""
    if redo_indices:
        redo_note = (
            f"\nIMPORTANT: You are regenerating variants {[i+1 for i in redo_indices]}. "
            f"These MUST look completely different from standard depictions of this event. "
            f"Be bold and unexpected."
        )

    approach_list = "\n".join(f"  Variant {i+1}: {a}" for i, a in enumerate(approaches))

    client = _ai_client()
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=900,
                messages=[{"role": "user", "content": (
                    f"You are an image prompt writer for Cucumber Recruitment, a UK healthcare staffing agency.\n"
                    f"Write {count} image generation prompts for the event: {event_name}\n"
                    f"{redo_note}\n\n"
                    f"Each variant MUST use the visual approach assigned to it — no swapping:\n"
                    f"{approach_list}\n\n"
                    f"Rules:\n"
                    f"- Each prompt must be a COMPLETE image description built around its assigned approach.\n"
                    f"- For awareness events (mental health, carers, pride, etc.) use real human scenes or powerful symbols.\n"
                    f"- For recruitment posts use a person in a dark forest green polo shirt and company lanyard.\n"
                    f"- Always end every prompt with: pure white background, studio lighting, photorealistic, sharp focus, 8k\n"
                    f"- Vary gender, age, and ethnicity when people appear.\n"
                    f"- Keep each prompt under 60 words.\n"
                    f"Return ONLY a JSON array of {count} strings:\n"
                    f'["prompt 1", "prompt 2", ...]'
                )}]
            )
            raw = msg.content[0].text.strip()
            m = re.search(r'\[.*\]', raw, re.DOTALL)
            if m:
                raw = m.group(0)
            prompts = json.loads(raw)
            return prompts[:count]
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            app.logger.warning("AI prompt generation attempt %s failed: %s", attempt+1, e)
            if attempt == 2:
                raise


# ── Nano Banana (Google Imagen 3 / Gemini image generation) ──────────

def _nano_banana_photo(prompt, out_path):
    """Generate one image via Nano Banana. Tries Pro model first, falls back to Flash."""
    key = os.environ.get("NANO_BANANA_KEY", "")
    if not key:
        return False

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "1:1"},
        },
    }

    for label, url in [("Pro", _NB_PRO_URL), ("Flash", _NB_FLASH_URL)]:
        try:
            r = req_lib.post(f"{url}?key={key}", json=body, timeout=60)
            if r.status_code == 200:
                # Iterate ALL parts — image may not be in parts[0]
                parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                for part in parts:
                    if "inlineData" in part and part["inlineData"].get("data"):
                        out_path.write_bytes(base64.b64decode(part["inlineData"]["data"]))
                        app.logger.info("Nano Banana (%s) OK → %s", label, out_path.name)
                        return True
            app.logger.warning("Nano Banana %s: %s — %s", label, r.status_code, r.text[:120])
        except Exception as e:
            app.logger.warning("Nano Banana %s error: %s", label, e)

    return False


# ── AI Horde (free, crowdsourced SDXL — used when HF credits run out) ──

_HORDE_SUBMIT = "https://stablehorde.net/api/v2/generate/async"
_HORDE_CHECK  = "https://stablehorde.net/api/v2/generate/check/{}"
_HORDE_STATUS = "https://stablehorde.net/api/v2/generate/status/{}"
_HORDE_KEY    = "0000000000"  # anonymous free key

import threading
_gen_jobs: dict = {}  # event_id -> {"total": N, "done": M, "failed": K, "complete": bool}
_jobs_lock = threading.Lock()


def _horde_one(prompt, out_path):
    """Submit one image to AI Horde, poll until complete, write to out_path. Returns True on success."""
    hdr_json = {"apikey": _HORDE_KEY, "Content-Type": "application/json"}
    hdr      = {"apikey": _HORDE_KEY}
    try:
        r = req_lib.post(_HORDE_SUBMIT, headers=hdr_json, json={
            "prompt": prompt,
            "params": {"width": 1024, "height": 1024, "steps": 20, "n": 1,
                       "sampler_name": "k_euler_a"},
            "models": ["SDXL 1.0"],
            "r2": False,
        }, timeout=30)
        if r.status_code != 202:
            app.logger.error("Horde submit %s: %s", r.status_code, r.text[:120])
            return False
        job_id = r.json()["id"]
        app.logger.info("Horde job %s submitted", job_id)
    except Exception as e:
        app.logger.error("Horde submit error: %s", e)
        return False

    for _ in range(180):  # poll every 10s, up to 30 min
        time.sleep(10)
        try:
            c = req_lib.get(_HORDE_CHECK.format(job_id), headers=hdr, timeout=15)
            if c.status_code == 200:
                d = c.json()
                app.logger.info("Horde %s: wait=%ss done=%s", job_id, d.get("wait_time"), d.get("done"))
                if d.get("done"):
                    break
        except Exception:
            pass
    else:
        app.logger.error("Horde job %s timed out", job_id)
        return False

    try:
        st   = req_lib.get(_HORDE_STATUS.format(job_id), headers=hdr, timeout=30)
        gens = st.json().get("generations", [])
        if not gens:
            app.logger.error("Horde: no generations for job %s", job_id)
            return False
        img = gens[0]["img"]
        if img.startswith("http"):
            out_path.write_bytes(req_lib.get(img, timeout=30).content)
        else:
            out_path.write_bytes(base64.b64decode(img))
        app.logger.info("Horde job %s saved → %s", job_id, out_path)
        return True
    except Exception as e:
        app.logger.error("Horde result error: %s", e)
        return False


def _horde_background(event_id, variant_dicts, prompts):
    """Background thread: generate each image via AI Horde and save to DB as it completes."""
    todo    = [v for v in variant_dicts if not v["image_path"]]
    out_dir = GFX_DIR / str(event_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    with _jobs_lock:
        _gen_jobs[event_id] = {"total": len(todo), "done": 0, "failed": 0, "complete": False}
    all_nums = [v["variant_num"] for v in variant_dicts]

    for v in todo:
        idx    = all_nums.index(v["variant_num"])
        prompt = prompts[idx] if idx < len(prompts) else prompts[-1]
        photo  = PHOTO_DIR / f"ev{event_id}_v{v['variant_num']}.jpg"
        out    = out_dir   / f"v{v['variant_num']}.png"

        if _horde_one(prompt, photo):
            try:
                gen_graphic(v["headline"], v["subtext"], "cucumber-recruitment.co.uk",
                            str(out), photo_path=str(photo))
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute(
                        "UPDATE variants SET image_path=?, generated_at=? WHERE id=?",
                        (str(out), datetime.utcnow().isoformat(), v["id"])
                    )
                with _jobs_lock:
                    _gen_jobs[event_id]["done"] += 1
                app.logger.info("Horde variant %s done (%s/%s)",
                                v["variant_num"], _gen_jobs[event_id]["done"],
                                _gen_jobs[event_id]["total"])
            except Exception as e:
                app.logger.error("Horde render v%s: %s", v["variant_num"], e)
                with _jobs_lock:
                    _gen_jobs[event_id]["failed"] += 1
        else:
            with _jobs_lock:
                _gen_jobs[event_id]["failed"] += 1

    with _jobs_lock:
        _gen_jobs[event_id]["complete"] = True
    app.logger.info("Horde background complete for event %s: %s", event_id, _gen_jobs[event_id])


def ai_generate_taglines(event_name, event_date=None):
    client = _ai_client()
    date_hint = f" (date: {event_date})" if event_date else ""

    for attempt in range(3):
        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                messages=[{
                    "role": "user",
                    "content": (
                        f"You are a copywriter for Cucumber Recruitment, a UK healthcare staffing agency.\n"
                        f"Generate 5 unique social media graphic taglines for the event: {event_name}{date_hint}\n\n"
                        f"Rules:\n"
                        f"- HEADLINE: 2–5 words, ALL CAPS, punchy and direct\n"
                        f"- SUBTEXT: 5–8 words, ALL CAPS, warm and supportive\n"
                        f"- Each of the 5 must feel distinct — vary the angle\n"
                        f"- Mention 'CUCUMBER RECRUITMENT' or 'CUCUMBER' in 1–2 subtexts\n"
                        f"- No hashtags, no emojis, no punctuation beyond full stops and commas\n\n"
                        f"Return ONLY a JSON array, nothing else:\n"
                        f'[{{"headline": "...", "subtext": "..."}}, ...]'
                    )
                }]
            )
            raw = msg.content[0].text.strip()
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                raw = match.group(0)
            pairs = json.loads(raw)
            return [(p["headline"].upper(), p["subtext"].upper()) for p in pairs[:5]]
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            app.logger.warning("AI tagline generation attempt %s failed: %s", attempt+1, e)
            if attempt == 2:
                raise


# ── Calendar ─────────────────────────────────────────────────────────

def upcoming_calendar(limit=20):
    try:
        today = date.today()
        raw   = [(d, s, de) for d, s, de in _parse_ics() if d >= today][:limit]
        out   = []
        for d, s, de in raw:
            s = re.sub(r"[^\x20-\x7E]", "", s).strip()
            s = re.sub(r"(?i)^post:\s*", "", s).strip()
            out.append((d, s, de))
        return out
    except Exception:
        return []


# ── Startup ───────────────────────────────────────────────────────────

@app.before_request
def ensure_ready():
    init_db()
    GFX_DIR.mkdir(parents=True, exist_ok=True)
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)


# ── Routes ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    db = get_db()
    events = db.execute("""
        SELECT e.*,
               COUNT(DISTINCT v.id)                                       AS variant_count,
               SUM(CASE WHEN v.image_path IS NOT NULL THEN 1 ELSE 0 END) AS generated_count,
               COUNT(DISTINCT vo.id)                                      AS vote_count
        FROM   events e
        LEFT JOIN variants v  ON v.event_id   = e.id
        LEFT JOIN votes    vo ON vo.variant_id = v.id
        GROUP BY e.id
        ORDER BY CASE WHEN e.event_date IS NULL THEN 1 ELSE 0 END,
                 e.event_date, e.created_at DESC
    """).fetchall()
    cal_raw = upcoming_calendar()
    db.close()

    # Keys of already-created events so we don't double-list them
    created_keys = set()
    for e in events:
        if e["event_date"]:
            created_keys.add((e["event_date"], e["name"].lower().strip()))

    # Build merged timeline
    from datetime import date as _date
    merged = []
    for e in events:
        dt = None
        if e["event_date"]:
            try:
                dt = _date.fromisoformat(e["event_date"])
            except Exception:
                pass
        merged.append({"kind": "event", "date": dt, "row": dict(e)})

    for ev_date, name, desc in cal_raw:
        if (ev_date.isoformat(), name.lower().strip()) not in created_keys:
            merged.append({"kind": "calendar", "date": ev_date, "name": name,
                           "date_iso": ev_date.isoformat()})

    merged.sort(key=lambda x: (x["date"] is None, x["date"] or _date.min))
    return render_template("index.html", merged=merged)


@app.route("/events/new", methods=["GET", "POST"])
def new_event():
    if request.method == "POST":
        name       = request.form.get("name", "").strip()
        event_date = request.form.get("event_date", "").strip() or None
        source     = request.form.get("source", "custom")

        if not name:
            return render_template("new_event.html",
                error="Please enter an event name.",
                form=request.form)

        db = get_db()
        # Redirect to existing event if same name+date already exists
        existing = db.execute(
            "SELECT id FROM events WHERE LOWER(TRIM(name))=LOWER(TRIM(?)) AND event_date=?",
            (name, event_date)
        ).fetchone()
        if existing:
            db.close()
            return redirect(url_for("event_detail", event_id=existing["id"]))
        cur = db.execute(
            "INSERT INTO events (name, event_date, source) VALUES (?,?,?)",
            (name, event_date, source)
        )
        eid = cur.lastrowid
        db.commit()
        db.close()
        return redirect(url_for("event_detail", event_id=eid))

    return render_template("new_event.html",
        form={"name": request.args.get("name", ""),
              "event_date": request.args.get("date", ""),
              "source": request.args.get("source", "custom")},
        error=None)


@app.route("/events/<int:event_id>")
def event_detail(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)
    variants = db.execute("""
        SELECT v.*, COUNT(vo.id) AS vote_count
        FROM variants v
        LEFT JOIN votes vo ON vo.variant_id = v.id
        WHERE v.event_id=?
        GROUP BY v.id ORDER BY v.variant_num
    """, (event_id,)).fetchall()
    db.close()
    all_generated  = bool(variants) and all(v["image_path"] for v in variants)
    has_any_images = any(v["image_path"] for v in variants)
    job = _gen_jobs.get(event_id)
    generating = bool(job and not job.get("complete"))
    return render_template("event.html",
        event=event, variants=variants, all_generated=all_generated,
        has_any_images=has_any_images, generating=generating,
        gen_progress=job)


@app.route("/events/<int:event_id>/gen-status")
def gen_status(event_id):
    job = _gen_jobs.get(event_id, {})
    db  = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN image_path IS NOT NULL THEN 1 ELSE 0 END) AS done "
        "FROM variants WHERE event_id=?", (event_id,)
    ).fetchone()
    db.close()
    return jsonify({
        "generating": bool(job and not job.get("complete")),
        "total":  row["total"] or 0,
        "done":   row["done"]  or 0,
        "failed": job.get("failed", 0),
    })


@app.route("/events/<int:event_id>/generate", methods=["POST"])
def generate_images(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)

    redo_ids = request.form.getlist("redo", type=int)
    app.logger.info("generate_images event=%s redo_ids=%s", event_id, redo_ids)
    variants = db.execute(
        "SELECT * FROM variants WHERE event_id=? ORDER BY variant_num", (event_id,)
    ).fetchall()

    # First time: generate taglines via Claude
    if not variants:
        try:
            pairs = ai_generate_taglines(event["name"], event["event_date"])
        except Exception as e:
            flash(f"Could not generate taglines: {e}", "error")
            db.close()
            return redirect(url_for("event_detail", event_id=event_id))
        for i, (h, s) in enumerate(pairs, 1):
            db.execute(
                "INSERT INTO variants (event_id, variant_num, headline, subtext) VALUES (?,?,?,?)",
                (event_id, i, h, s)
            )
        db.commit()
        variants = db.execute(
            "SELECT * FROM variants WHERE event_id=? ORDER BY variant_num", (event_id,)
        ).fetchall()

    # Redo: delete files and clear DB for selected variants
    if redo_ids:
        for v in variants:
            if v["id"] in redo_ids:
                photo = PHOTO_DIR / f"ev{event_id}_v{v['variant_num']}.jpg"
                photo.unlink(missing_ok=True)
                Path(str(photo) + ".rmbg.png").unlink(missing_ok=True)  # bg-removal cache
                (GFX_DIR / str(event_id) / f"v{v['variant_num']}.png").unlink(missing_ok=True)
                db.execute("UPDATE variants SET image_path=NULL WHERE id=?", (v["id"],))
        db.commit()
        variants = db.execute(
            "SELECT * FROM variants WHERE event_id=? ORDER BY variant_num", (event_id,)
        ).fetchall()
        flash(f"Regenerated {len(redo_ids)} image variants", "success")

    # Generate image prompts via Claude
    redo_indices = [i for i, v in enumerate(variants) if v["id"] in redo_ids] if redo_ids else None
    try:
        prompts = ai_image_prompts(event["name"], len(variants), redo_indices=redo_indices)
    except Exception as e:
        app.logger.error("Prompts failed: %s", e)
        flash(f"Could not generate image prompts: {e}", "error")
        db.close()
        return redirect(url_for("event_detail", event_id=event_id))

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = GFX_DIR / str(event_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    tok = os.environ.get("HF_TOKEN", "")
    now = datetime.utcnow().isoformat()

    # ── Try HuggingFace FLUX (fast, concurrent) ───────────────────
    hf_credits_ok = True

    def _hf_photo(args):
        i, v = args
        if v["image_path"]:
            return "kept"
        photo = PHOTO_DIR / f"ev{event_id}_v{v['variant_num']}.jpg"
        if photo.exists():
            return "cached"
        prompt = prompts[i] if i < len(prompts) else prompts[-1]
        try:
            r = req_lib.post(HF_URL,
                headers={"Authorization": f"Bearer {tok}"},
                json={"inputs": prompt, "parameters": {
                    "width": 1024, "height": 1024, "num_inference_steps": 8}},
                timeout=180)
            if r.status_code == 200:
                photo.write_bytes(r.content)
                return "ok"
            if r.status_code == 402:
                return "credits"
            app.logger.error("HF v%s: %s", v["variant_num"], r.status_code)
            return "error"
        except Exception as e:
            app.logger.error("HF v%s error: %s", v["variant_num"], e)
            return "error"

    with ThreadPoolExecutor(max_workers=3) as pool:
        hf_statuses = list(pool.map(_hf_photo, enumerate(variants)))

    hf_credits_ok = "credits" not in hf_statuses

    if hf_credits_ok:
        # Render graphics for all variants that have a photo
        def _render(args):
            i, v = args
            if v["image_path"]:
                return (v["id"], v["image_path"], False)
            photo = PHOTO_DIR / f"ev{event_id}_v{v['variant_num']}.jpg"
            if not photo.exists():
                return (v["id"], None, False)
            out = out_dir / f"v{v['variant_num']}.png"
            try:
                gen_graphic(v["headline"], v["subtext"], "cucumber-recruitment.co.uk",
                            str(out), photo_path=str(photo))
                return (v["id"], str(out), True)
            except Exception as e:
                app.logger.error("Render v%s: %s", v["variant_num"], e)
                return (v["id"], None, False)

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(_render, enumerate(variants)))

        ok, fail = 0, []
        for vid, path, is_new in results:
            if path:
                db.execute("UPDATE variants SET image_path=?, generated_at=? WHERE id=?",
                           (path, now, vid))
                if is_new:
                    ok += 1
            else:
                fail.append(f"variant {vid}")

        db.commit()
        db.close()
        if fail:
            flash(f"Generated {ok} image(s). Some failed.", "error")
        else:
            flash(f"Generated {ok} image(s) successfully.", "success")

    else:
        # ── HF credits exhausted → try Nano Banana (fast), then AI Horde ──
        db.close()

        def _nb_photo(args):
            i, v = args
            if v["image_path"]:
                return (v, "kept")
            photo = PHOTO_DIR / f"ev{event_id}_v{v['variant_num']}.jpg"
            if photo.exists():
                return (v, "cached")
            prompt = prompts[i] if i < len(prompts) else prompts[-1]
            return (v, "ok" if _nano_banana_photo(prompt, photo) else "failed")

        with ThreadPoolExecutor(max_workers=3) as pool:
            nb_results = list(pool.map(_nb_photo, enumerate(variants)))

        new_generated = [s for _, s in nb_results if s not in ("kept", "cached")]
        nb_any_ok     = any(s == "ok"     for s in new_generated)
        nb_any_failed = any(s == "failed" for s in new_generated)
        nb_has_new    = bool(new_generated)

        # Render if: Nano Banana generated at least one new image, or everything was already kept
        if (not nb_has_new) or nb_any_ok:
            # Render graphics for Nano Banana photos
            now = datetime.utcnow().isoformat()
            out_dir.mkdir(parents=True, exist_ok=True)

            def _render_nb(args):
                i, (v, status) = args
                if status == "kept":
                    return (v["id"], v["image_path"], False)
                photo = PHOTO_DIR / f"ev{event_id}_v{v['variant_num']}.jpg"
                if not photo.exists():
                    return (v["id"], None, False)
                out = out_dir / f"v{v['variant_num']}.png"
                try:
                    gen_graphic(v["headline"], v["subtext"], "cucumber-recruitment.co.uk",
                                str(out), photo_path=str(photo))
                    return (v["id"], str(out), True)
                except Exception as e:
                    app.logger.error("Render v%s: %s", v["variant_num"], e)
                    return (v["id"], None, False)

            db2 = get_db()
            with ThreadPoolExecutor(max_workers=5) as pool:
                render_results = list(pool.map(_render_nb, enumerate(nb_results)))
            ok2, fail2 = 0, []
            for vid, path, is_new in render_results:
                if path:
                    db2.execute("UPDATE variants SET image_path=?, generated_at=? WHERE id=?",
                                (path, now, vid))
                    if is_new:
                        ok2 += 1
                else:
                    fail2.append(f"v{vid}")
            db2.commit()
            db2.close()
            if fail2:
                flash(f"Generated {ok2} image(s) via Nano Banana. Some failed.", "error")
            else:
                flash(f"Generated {ok2} image(s) via Nano Banana.", "success")

        else:
            # Last resort: AI Horde background
            already_running = (event_id in _gen_jobs and not _gen_jobs[event_id].get("complete"))
            if not already_running:
                fresh_db   = get_db()
                fresh_vars = [dict(v) for v in fresh_db.execute(
                    "SELECT * FROM variants WHERE event_id=? ORDER BY variant_num", (event_id,)
                ).fetchall()]
                fresh_db.close()
                threading.Thread(
                    target=_horde_background,
                    args=(event_id, fresh_vars, prompts),
                    daemon=True
                ).start()
                flash("Switching to AI Horde (free, slower). Images appear automatically as each one completes.", "info")
            else:
                flash("Images are still generating in the background — please wait.", "info")

    return redirect(url_for("event_detail", event_id=event_id))


@app.route("/events/<int:event_id>/vote", methods=["GET", "POST"])
def vote(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)

    ip = request.remote_addr or ""
    voted_events = session.get("voted_events", [])

    existing_vote = db.execute(
        """SELECT vo.id, vo.variant_id, v.variant_num FROM votes vo
           JOIN variants v ON v.id = vo.variant_id
           WHERE v.event_id=? AND vo.ip_address=?
           ORDER BY vo.id DESC LIMIT 1""",
        (event_id, ip)
    ).fetchone()
    already_voted = event_id in voted_events or bool(existing_vote)

    if request.method == "POST":
        if already_voted:
            db.close()
            flash("You've already voted on this event.", "error")
            return redirect(url_for("results", event_id=event_id))
        variant_id = request.form.get("variant_id", type=int)
        voter_name = (request.form.get("voter_name") or "Anonymous").strip()
        if variant_id:
            db.execute("INSERT INTO votes (variant_id, voter_name, ip_address) VALUES (?,?,?)",
                       (variant_id, voter_name, ip))
            db.commit()
            voted_events.append(event_id)
            session["voted_events"] = voted_events
        db.close()
        return redirect(url_for("results", event_id=event_id))

    variants = db.execute(
        "SELECT * FROM variants WHERE event_id=? AND image_path IS NOT NULL ORDER BY variant_num",
        (event_id,)
    ).fetchall()
    db.close()
    return render_template("vote.html", event=event, variants=variants,
                           already_voted=already_voted,
                           current_vote_num=existing_vote["variant_num"] if existing_vote else None)


@app.route("/events/<int:event_id>/unvote", methods=["POST"])
def unvote(event_id):
    ip = request.remote_addr or ""
    db = get_db()
    db.execute(
        """DELETE FROM votes WHERE ip_address=?
           AND variant_id IN (SELECT id FROM variants WHERE event_id=?)""",
        (ip, event_id)
    )
    db.commit()
    db.close()
    voted_events = session.get("voted_events", [])
    session["voted_events"] = [e for e in voted_events if e != event_id]
    return redirect(url_for("vote", event_id=event_id))


@app.route("/events/<int:event_id>/results")
def results(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)
    variants = db.execute("""
        SELECT v.*, COUNT(vo.id) AS vote_count
        FROM variants v
        LEFT JOIN votes vo ON vo.variant_id = v.id
        WHERE v.event_id=?
        GROUP BY v.id ORDER BY vote_count DESC, v.variant_num
    """, (event_id,)).fetchall()
    total = sum(v["vote_count"] for v in variants)
    db.close()
    return render_template("results.html", event=event, variants=variants, total=total)


@app.route("/events/<int:event_id>/delete", methods=["POST"])
def delete_event(event_id):
    db = get_db()
    db.execute("DELETE FROM events WHERE id=?", (event_id,))
    db.commit()
    db.close()
    return redirect(url_for("index"))


@app.route("/events/<int:event_id>/clear-images", methods=["POST"])
def clear_images(event_id):
    db = get_db()
    variants = db.execute(
        "SELECT * FROM variants WHERE event_id=?", (event_id,)
    ).fetchall()
    for v in variants:
        photo = PHOTO_DIR / f"ev{event_id}_v{v['variant_num']}.jpg"
        photo.unlink(missing_ok=True)
        Path(str(photo) + ".rmbg.png").unlink(missing_ok=True)
        (GFX_DIR / str(event_id) / f"v{v['variant_num']}.png").unlink(missing_ok=True)
        db.execute("UPDATE variants SET image_path=NULL WHERE id=?", (v["id"],))
    db.commit()
    db.close()
    flash("Images cleared.", "success")
    return redirect(url_for("event_detail", event_id=event_id))


@app.route("/img/<int:event_id>/<filename>")
def serve_image(event_id, filename):
    return send_from_directory(GFX_DIR / str(event_id), filename)


@app.route("/cucumber_logo.png")
def serve_logo():
    return send_from_directory(ROOT, "cucumber_logo.png")


# Always run on startup (works with both gunicorn and direct)
init_db()
GFX_DIR.mkdir(parents=True, exist_ok=True)
PHOTO_DIR.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
