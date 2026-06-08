import os
from flask import Flask, render_template, request, redirect, url_for, abort, send_from_directory, flash
from pathlib import Path
from datetime import date, datetime
import sqlite3
import sys
import re
import json
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

# On Railway use /tmp (always writable); locally use project dir
_ON_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT"))
_DATA       = Path("/tmp") if _ON_RAILWAY else ROOT
PHOTO_DIR   = _DATA / "photos" / "web"

HF_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

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
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            );
        """)


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

def ai_image_prompts(event_name, count=5):
    """Ask Claude for count complete FLUX image prompts for this event."""
    client = _ai_client()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": (
            f"You are an image prompt writer for Cucumber Recruitment, a UK healthcare staffing agency.\n"
            f"Write {count} complete FLUX image generation prompts for the event: {event_name}\n\n"
            f"Rules:\n"
            f"- Each prompt must be a COMPLETE, ready-to-use image description — not a subject fragment.\n"
            f"- Not every image needs a person. Choose what fits the theme:\n"
            f"  * Recruitment/brand posts → person in dark forest green polo shirt and company ID lanyard\n"
            f"  * Awareness events (mental health, carers, pride, diabetes, etc.) → real-life scenes: "
            f"a carer with an elderly relative, hands being held, a diverse crowd, symbolic objects, "
            f"an empty hospital corridor, a red ribbon, a rainbow — whatever fits the moment\n"
            f"  * Mix person and non-person images across the {count} variants\n"
            f"- Always end every prompt with: pure white background, studio lighting, photorealistic, high quality\n"
            f"- Vary gender, age, and ethnicity when people appear.\n"
            f"- Keep each prompt under 40 words.\n"
            f"Return ONLY a JSON array of strings, nothing else:\n"
            f'["prompt 1", "prompt 2", ...]'
        )}]
    )
    raw = msg.content[0].text.strip()
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if m:
        raw = m.group(0)
    prompts = json.loads(raw)
    return prompts[:count]


def fetch_photos(event_name, event_id, count=5):
    """Generate photos via FLUX.1-schnell on HuggingFace."""
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    cached = [PHOTO_DIR / f"ev{event_id}_v{i+1}.jpg" for i in range(count)]
    if all(p.exists() for p in cached):
        return [str(p) for p in cached]

    try:
        context_prompts = ai_image_prompts(event_name, count)
    except Exception as e:
        app.logger.error("AI image prompts failed: %s", e)
        context_prompts = [
            f"photorealistic studio photograph, {_UNIFORM_PEOPLE[i % len(_UNIFORM_PEOPLE)]}, "
            "dark forest green polo shirt, company ID lanyard, white background, high quality"
            for i in range(count)
        ]

    tok = os.environ.get('HF_TOKEN', '')

    def _generate(args):
        i, out_path = args
        if out_path.exists():
            return str(out_path)
        prompt = context_prompts[i] if i < len(context_prompts) else context_prompts[-1]
        try:
            resp = req_lib.post(HF_URL,
                headers={"Authorization": f"Bearer {tok}"},
                json={"inputs": prompt, "parameters": {"width": 512, "height": 1024}},
                timeout=120)
            if resp.status_code == 200:
                out_path.write_bytes(resp.content)
                return str(out_path)
            app.logger.error("HF failed v%s: %s %s", i+1, resp.status_code, resp.text[:80])
        except Exception as e:
            app.logger.error("HF error v%s: %s", i+1, e)
        return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        paths = list(pool.map(_generate, enumerate(cached)))

    return paths


def ai_generate_taglines(event_name, event_date=None):
    client = _ai_client()
    date_hint = f" (date: {event_date})" if event_date else ""

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
    calendar = upcoming_calendar()
    db.close()
    return render_template("index.html", events=events, calendar=calendar)


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
    all_generated = bool(variants) and all(v["image_path"] for v in variants)
    return render_template("event.html",
        event=event, variants=variants, all_generated=all_generated)


@app.route("/events/<int:event_id>/generate", methods=["POST"])
def generate_images(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)

    variants = db.execute(
        "SELECT * FROM variants WHERE event_id=? ORDER BY variant_num", (event_id,)
    ).fetchall()

    # If no variants yet, ask Claude to generate taglines first
    if not variants:
        try:
            pairs = ai_generate_taglines(event["name"], event["event_date"])
        except Exception as e:
            flash(f"Could not generate taglines: {e}", "error")
            db.close()
            return redirect(url_for("event_detail", event_id=event_id))

        now = datetime.utcnow().isoformat()
        for i, (h, s) in enumerate(pairs, 1):
            db.execute(
                "INSERT INTO variants (event_id, variant_num, headline, subtext) VALUES (?,?,?,?)",
                (event_id, i, h, s)
            )
        db.commit()
        variants = db.execute(
            "SELECT * FROM variants WHERE event_id=? ORDER BY variant_num", (event_id,)
        ).fetchall()

    out_dir = GFX_DIR / str(event_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow().isoformat()

    photos = fetch_photos(event["name"], event_id, count=len(variants))

    def _render(args):
        i, v = args
        out = out_dir / f"v{v['variant_num']}.png"
        try:
            gen_graphic(v["headline"], v["subtext"], "cucumber-recruitment.co.uk", str(out),
                        photo_path=photos[i])
            return (v["id"], str(out), None)
        except Exception as e:
            app.logger.error("Failed to generate v%s for event %s: %s",
                             v["variant_num"], event_id, e)
            return (v["id"], None, str(e))

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(_render, enumerate(variants)))

    ok, fail = 0, []
    for vid, path, err in results:
        if path:
            db.execute("UPDATE variants SET image_path=?, generated_at=? WHERE id=?",
                       (path, now, vid))
            ok += 1
        else:
            fail.append(err)

    db.commit()
    db.close()

    if fail:
        flash(f"Generated {ok} image(s). Errors: " + "; ".join(fail), "error")
    else:
        flash(f"Generated {ok} image(s) successfully.", "success")

    return redirect(url_for("event_detail", event_id=event_id))


@app.route("/events/<int:event_id>/vote", methods=["GET", "POST"])
def vote(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)

    if request.method == "POST":
        variant_id = request.form.get("variant_id", type=int)
        voter_name = (request.form.get("voter_name") or "Anonymous").strip()
        if variant_id:
            db.execute("INSERT INTO votes (variant_id, voter_name) VALUES (?,?)",
                       (variant_id, voter_name))
            db.commit()
        db.close()
        return redirect(url_for("results", event_id=event_id))

    variants = db.execute(
        "SELECT * FROM variants WHERE event_id=? AND image_path IS NOT NULL ORDER BY variant_num",
        (event_id,)
    ).fetchall()
    db.close()
    return render_template("vote.html", event=event, variants=variants)


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
