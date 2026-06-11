import os
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

_NB_PRO_URL     = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent"
_NB_FLASH_URL   = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"

_UNIFORM_PEOPLE = [
    "young Black woman, short natural hair, warm smile, confident posture",
    "middle-aged South Asian man, friendly expression, professional demeanour",
    "young white woman, blonde hair, bright smile, poised and approachable",
    "older Black man, grey beard, calm confident expression",
    "young mixed-race woman, curly hair, natural and professional pose",
]
sys.path.insert(0, str(ROOT))
from generate_graphic import generate as gen_graphic, _parse_ics
from generate_batch import EVENTS as _BATCH_EVENTS

app     = Flask(__name__)
app.secret_key = "cucumber-events-secret"
DB_PATH = _DATA / "events.db"
GFX_DIR = _DATA / "graphics" / "web"


# ── Database ─────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


_db_ready = False

def init_db():
    global _db_ready
    if _db_ready:
        return
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

        _dedupe_variants(conn)
        indexes = [r[1] for r in conn.execute("PRAGMA index_list(variants)").fetchall()]
        if "idx_variants_event_num" not in indexes:
            conn.execute(
                "CREATE UNIQUE INDEX idx_variants_event_num ON variants(event_id, variant_num)"
            )
    _db_ready = True


def _dedupe_variants(conn):
    """Remove duplicate variant_num rows; keep the one with an image, else the newest."""
    conn.execute("""
        DELETE FROM variants WHERE id IN (
            SELECT v.id FROM variants v
            JOIN (
                SELECT event_id, variant_num,
                       COALESCE(MAX(CASE WHEN image_path IS NOT NULL THEN id END), MAX(id)) AS keep_id
                FROM variants GROUP BY event_id, variant_num
            ) k ON v.event_id = k.event_id AND v.variant_num = k.variant_num
            WHERE v.id != k.keep_id
        )
    """)


# ── AI tagline generation ─────────────────────────────────────────────

_RATE_LIMIT_WAITS = (15, 30, 60, 90, 120)

def _is_rate_limited(exc):
    code = getattr(exc, "status_code", None)
    if code == 429:
        return True
    msg = str(exc).lower()
    return "rate_limit" in msg or "error code: 429" in msg


def _claude_create(client, **kwargs):
    """Call Claude with exponential backoff on 429 rate limits."""
    last_err = None
    for attempt, wait in enumerate(_RATE_LIMIT_WAITS):
        try:
            return client.messages.create(**kwargs)
        except Exception as e:
            last_err = e
            if _is_rate_limited(e) and attempt < len(_RATE_LIMIT_WAITS) - 1:
                app.logger.warning("Claude rate limited, retry in %ss (%s/%s)",
                                   wait, attempt + 1, len(_RATE_LIMIT_WAITS))
                time.sleep(wait)
            else:
                raise
    raise last_err


def _norm_event_key(name):
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("'", ""))


def _batch_tagline_lookup():
    lookup = []
    for key, data in _BATCH_EVENTS.items():
        for label in (key.replace("_", " "), data["label"].split("(")[0]):
            lookup.append((_norm_event_key(label), data["variants"]))
    return lookup


_BATCH_TAGLINE_LOOKUP = _batch_tagline_lookup()


def _lookup_batch_taglines(event_name):
    norm = _norm_event_key(event_name)
    for key_norm, variants in _BATCH_TAGLINE_LOOKUP:
        if key_norm in norm or norm in key_norm:
            return [(h, s) for h, s in variants[:5]]
    return None


def _fallback_taglines(event_name):
    preset = _lookup_batch_taglines(event_name)
    if preset:
        app.logger.info("Using preset taglines for %s", event_name)
        return preset
    topic = re.sub(r"\s+", " ", event_name.strip()).upper()
    return [
        (f"{topic}.", "FROM ALL OF US AT CUCUMBER RECRUITMENT."),
        ("THANK YOU.", "WE SEE THE DIFFERENCE YOU MAKE EVERY DAY."),
        ("YOU MAKE A DIFFERENCE.", "PROUD TO SUPPORT HEALTHCARE PROFESSIONALS."),
        (f"HAPPY {topic}.", "CUCUMBER RECRUITMENT STANDS WITH YOU."),
        ("TOGETHER WE CARE.", "CELEBRATING THE PEOPLE WHO SHOW UP."),
    ]


def _fallback_image_prompts(event_name, count=5):
    import random
    people   = random.sample(_UNIFORM_PEOPLE, min(2, len(_UNIFORM_PEOPLE)))
    p_app    = random.sample(_PERSON_APPROACHES, min(2, len(_PERSON_APPROACHES)))
    s_app    = random.sample(_SCENE_APPROACHES, min(count - 2, len(_SCENE_APPROACHES)))

    prompts = []
    # 2 person-in-uniform shots
    for person, approach in zip(people, p_app):
        prompts.append(
            f"{person}, dressed in a dark forest green (#1a5c28) polo shirt with a company ID badge on a lanyard. "
            f"{event_name} theme. {approach}. Photorealistic, sharp focus, 8k."
        )
    # Remaining slots: contextual/symbolic, no person needed
    for approach in s_app[:count - 2]:
        prompts.append(
            f"Photorealistic image. {approach.format(event=event_name)}. Sharp focus, 8k."
        )

    random.shuffle(prompts)
    return prompts[:count]


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

# Person shots — always wear the Cucumber uniform, real environmental backgrounds
_PERSON_APPROACHES = [
    "tight portrait, direct eye contact, warm smile, natural indoor setting, soft bokeh background, shallow depth of field",
    "confident mid-shot standing in a professional environment, warm natural window light, depth of field",
    "candid moment, subject looking slightly off-camera, thoughtful expression, blurred workplace background",
    "low angle looking up, empowering and bold, outdoor environment with natural sky background",
]

# Scene/symbolic shots — no person required, contextually unique to the event
_SCENE_APPROACHES = [
    "symbolic flat-lay of meaningful objects representing {event}, overhead shot, styled on a clean white surface, rich colours, studio lighting",
    "wide atmospheric scene representing the spirit of {event}, evocative and emotional, golden hour light, no people, photojournalistic",
    "close-up of hands in a meaningful gesture related to {event}, warm intimate lighting, shallow depth of field, blurred background",
    "bold graphic composition: a single powerful symbolic object representing {event}, centred, minimal, striking colours, clean background",
    "environmental wide shot capturing the feeling of {event}, real-world setting, documentary style, warm and human",
]

def _custom_image_prompts(scene_description, count=5):
    import random
    people = list(_UNIFORM_PEOPLE)
    random.shuffle(people)
    while len(people) < count:
        people.extend(_UNIFORM_PEOPLE)
    return [
        f"{people[i]}, dressed in a dark forest green (#1a5c28) polo shirt with a company ID badge on a lanyard. "
        f"{scene_description}. Photorealistic, sharp focus, 8k."
        for i in range(count)
    ]


def ai_image_prompts(event_name, count=5, redo_indices=None, use_presets=False):
    """Image prompts — templates instantly; Claude only for custom events (one try)."""
    if use_presets or _lookup_batch_taglines(event_name):
        return _fallback_image_prompts(event_name, count), True
    import random
    approaches = random.sample(_VISUAL_APPROACHES, min(count, len(_VISUAL_APPROACHES)))
    while len(approaches) < count:
        approaches.append(random.choice(_VISUAL_APPROACHES))
    redo_note = ""
    if redo_indices:
        redo_note = (
            f"\nIMPORTANT: Regenerating variants {[i+1 for i in redo_indices]}. "
            f"Be bold and unexpected.\n"
        )
    approach_list = "\n".join(f"  Variant {i+1}: {a}" for i, a in enumerate(approaches))
    try:
        client = _ai_client()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            messages=[{"role": "user", "content": (
                f"Write {count} image generation prompts for: {event_name}\n{redo_note}\n"
                f"Approaches:\n{approach_list}\n\n"
                f"Every prompt MUST open by describing a specific person dressed in a dark forest green (#1a5c28) polo shirt and a company ID badge on a lanyard. "
                f"Then describe the scene using the assigned approach.\n"
                f"End every prompt with: pure white background, studio lighting, photorealistic, sharp focus, 8k\n"
                f"Return ONLY a JSON array of {count} strings."
            )}],
        )
        raw = msg.content[0].text.strip()
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            raw = m.group(0)
        return json.loads(raw)[:count], False
    except Exception as e:
        app.logger.warning("AI image prompts unavailable for %s (%s), using templates", event_name, e)
        return _fallback_image_prompts(event_name, count), True


# ── Nano Banana (Google Imagen 3 / Gemini image generation) ──────────

def _nano_banana_photo(prompt, out_path):
    """Generate one image via Nano Banana (Pro model only)."""
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




def ai_generate_taglines(event_name, event_date=None):
    """Preset copy for known events; one Claude try for custom events."""
    preset = _lookup_batch_taglines(event_name)
    if preset:
        return preset, False
    date_hint = f" (date: {event_date})" if event_date else ""
    prompt = (
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
    try:
        client = _ai_client()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            raw = match.group(0)
        pairs = json.loads(raw)
        return [(p["headline"].upper(), p["subtext"].upper()) for p in pairs[:5]], False
    except Exception as e:
        app.logger.warning("AI taglines unavailable for %s (%s), using presets", event_name, e)
        return _fallback_taglines(event_name), True


# ── Calendar ─────────────────────────────────────────────────────────

_calendar_cache = {"at": 0.0, "data": ()}
_CALENDAR_TTL = 300  # seconds


def upcoming_calendar(limit=20):
    now = time.time()
    if now - _calendar_cache["at"] < _CALENDAR_TTL and _calendar_cache["data"]:
        return list(_calendar_cache["data"])[:limit]
    try:
        today = date.today()
        raw   = [(d, s, de) for d, s, de in _parse_ics() if d >= today][:limit]
        out   = []
        for d, s, de in raw:
            s = re.sub(r"[^\x20-\x7E]", "", s).strip()
            s = re.sub(r"(?i)^post:\s*", "", s).strip()
            out.append((d, s, de))
        _calendar_cache["at"] = now
        _calendar_cache["data"] = tuple(out)
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
    return render_template("event.html",
        event=event, variants=variants, all_generated=all_generated,
        has_any_images=has_any_images, generating=False,
        gen_progress=None)


@app.route("/events/<int:event_id>/gen-status")
def gen_status(event_id):
    db  = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN image_path IS NOT NULL THEN 1 ELSE 0 END) AS done "
        "FROM variants WHERE event_id=?", (event_id,)
    ).fetchone()
    db.close()
    return jsonify({
        "generating": False,
        "total":  row["total"] or 0,
        "done":   row["done"]  or 0,
        "failed": 0,
    })


@app.route("/events/<int:event_id>/generate", methods=["POST"])
def generate_images(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)

    redo_ids = request.form.getlist("redo", type=int)
    taglines_fallback = False

    db.execute("BEGIN IMMEDIATE")
    variant_count = db.execute(
        "SELECT COUNT(*) FROM variants WHERE event_id=?", (event_id,)
    ).fetchone()[0]
    if variant_count == 0:
        pairs, taglines_fallback = ai_generate_taglines(event["name"], event["event_date"])
        if taglines_fallback:
            flash("Using preset taglines. Images will still generate.", "info")
        for i, (h, s) in enumerate(pairs[:5], 1):
            db.execute(
                "INSERT INTO variants (event_id, variant_num, headline, subtext) VALUES (?,?,?,?)",
                (event_id, i, h, s)
            )
    db.commit()

    variants = db.execute(
        "SELECT * FROM variants WHERE event_id=? ORDER BY variant_num", (event_id,)
    ).fetchall()

    if redo_ids:
        for v in variants:
            if v["id"] in redo_ids:
                photo = PHOTO_DIR / f"ev{event_id}_v{v['variant_num']}.jpg"
                photo.unlink(missing_ok=True)
                Path(str(photo) + ".rmbg.png").unlink(missing_ok=True)
                (GFX_DIR / str(event_id) / f"v{v['variant_num']}.png").unlink(missing_ok=True)
                db.execute("UPDATE variants SET image_path=NULL WHERE id=?", (v["id"],))
        db.commit()
        variants = db.execute(
            "SELECT * FROM variants WHERE event_id=? ORDER BY variant_num", (event_id,)
        ).fetchall()
        flash(f"Regenerated {len(redo_ids)} image variants", "success")

    custom_prompt = request.form.get("custom_prompt", "").strip()
    redo_indices = [i for i, v in enumerate(variants) if v["id"] in redo_ids] if redo_ids else None
    if custom_prompt:
        prompts = _custom_image_prompts(custom_prompt, len(variants))
    else:
        used_presets = bool(_lookup_batch_taglines(event["name"])) or taglines_fallback
        prompts, _ = ai_image_prompts(
            event["name"], len(variants), redo_indices=redo_indices, use_presets=used_presets)

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = GFX_DIR / str(event_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow().isoformat()

    def _process(args):
        i, v = args
        if v["image_path"]:
            return (v["id"], v["image_path"], False)
        photo = PHOTO_DIR / f"ev{event_id}_v{v['variant_num']}.jpg"
        out   = out_dir   / f"v{v['variant_num']}.png"
        prompt = prompts[i] if i < len(prompts) else prompts[-1]
        if not photo.exists() and not _nano_banana_photo(prompt, photo):
            return (v["id"], None, False)
        try:
            gen_graphic(v["headline"], v["subtext"], "cucumber-recruitment.co.uk",
                        str(out), photo_path=str(photo))
            return (v["id"], str(out), True)
        except Exception as e:
            app.logger.error("Render v%s: %s", v["variant_num"], e)
            return (v["id"], None, False)

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(_process, enumerate(variants)))

    ok = 0
    for vid, path, is_new in results:
        if path:
            db.execute("UPDATE variants SET image_path=?, generated_at=? WHERE id=?",
                       (path, now, vid))
            if is_new:
                ok += 1
    db.commit()
    db.close()

    if ok:
        flash(f"Generated {ok} image(s) via Nano Banana.", "success")
    elif not redo_ids:
        flash("Image generation failed — check your NANO_BANANA_KEY.", "error")

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
