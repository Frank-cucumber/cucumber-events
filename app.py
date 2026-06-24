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
import threading
import xml.etree.ElementTree as ET
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
_DATA       = Path("/tmp") if _ON_RAILWAY else ROOT
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
from generate_graphic import generate as gen_graphic, _parse_ics, CANVAS_SIZES, FORMAT_LABELS
from generate_batch import EVENTS as _BATCH_EVENTS

app     = Flask(__name__)
app.secret_key = "cucumber-events-secret"
_BOOT_TS = str(int(time.time()))

@app.context_processor
def inject_globals():
    return {"cache_bust": _BOOT_TS}
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
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL,
                event_date    TEXT,
                source        TEXT    DEFAULT 'custom',
                output_format TEXT    DEFAULT 'square',
                created_at    TEXT    DEFAULT CURRENT_TIMESTAMP
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
        cols = [r[1] for r in conn.execute("PRAGMA table_info(votes)").fetchall()]
        if "ip_address" not in cols:
            conn.execute("ALTER TABLE votes ADD COLUMN ip_address TEXT")
        ecols = [r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
        if "output_format" not in ecols:
            conn.execute("ALTER TABLE events ADD COLUMN output_format TEXT DEFAULT 'square'")

        _dedupe_variants(conn)
        indexes = [r[1] for r in conn.execute("PRAGMA index_list(variants)").fetchall()]
        if "idx_variants_event_num" not in indexes:
            conn.execute(
                "CREATE UNIQUE INDEX idx_variants_event_num ON variants(event_id, variant_num)"
            )
        _seed_batch_events(conn)
    _db_ready = True


_MONTH_MAP = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
    "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
}

def _parse_batch_date(label):
    m = re.search(r'\(([^)]+)\)', label)
    if not m:
        return None
    s = m.group(1)
    month_m = re.search(r'([A-Za-z]{3,})', s)
    if not month_m:
        return None
    mon = _MONTH_MAP.get(month_m.group(1)[:3].lower())
    if not mon:
        return None
    day_m = re.match(r'(\d+)', s.strip())
    day = int(day_m.group(1)) if day_m else 1
    yr = date.today().year
    try:
        d = date(yr, mon, day)
        if d < date.today():
            d = date(yr + 1, mon, day)
        return d.isoformat()
    except Exception:
        return None


def _seed_batch_events(conn):
    """Populate events + taglines from generate_batch.py if the DB is empty."""
    if conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] > 0:
        return
    for key, data in _BATCH_EVENTS.items():
        name = re.sub(r"\s*\(.*?\)\s*$", "", data["label"]).rstrip("—– ").strip()
        event_date = _parse_batch_date(data["label"])
        cur = conn.execute(
            "INSERT INTO events (name, event_date, source) VALUES (?, ?, 'batch')",
            (name, event_date)
        )
        eid = cur.lastrowid
        for i, (h, s) in enumerate(data["variants"][:5], 1):
            conn.execute(
                "INSERT INTO variants (event_id, variant_num, headline, subtext) VALUES (?,?,?,?)",
                (eid, i, h, s)
            )
    conn.commit()


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
    people = random.sample(_UNIFORM_PEOPLE, min(2, len(_UNIFORM_PEOPLE)))
    p_app  = random.sample(_PERSON_APPROACHES, min(2, len(_PERSON_APPROACHES)))
    s_app  = random.sample(_SCENE_APPROACHES, min(count - 2, len(_SCENE_APPROACHES)))

    prompts = []
    for person, approach in zip(people, p_app):
        prompts.append(
            f"{person}. {event_name}. {approach}. Photorealistic, warm natural lighting, sharp focus, 8k."
        )
    for approach in s_app[:count - 2]:
        prompts.append(
            f"Photorealistic image. {approach.format(event=event_name)}. Warm natural lighting, sharp focus, 8k."
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

# Care-specific person shots — real care settings, not office environments
_PERSON_APPROACHES = [
    "warm portrait of a care worker with an elderly resident in a bright care home lounge, natural window light, soft bokeh",
    "carer supporting a patient in a hospital ward, empathetic and professional, documentary style, candid moment",
    "care worker kneeling to speak at eye level with an elderly person in a wheelchair, warm and human, shallow depth of field",
    "nurse or support worker in scrubs outdoors at a care facility entrance, empowering and bold, natural sky background",
]

# Scene/symbolic shots — contextually specific to the event
_SCENE_APPROACHES = [
    "close-up of two hands clasped together — a carer and an elderly person — warm intimate lighting, shallow depth of field, blurred care home background",
    "wide atmospheric shot of a care home garden in golden hour light, peaceful and warm, no people, photojournalistic",
    "symbolic flat-lay of meaningful objects representing {event}, overhead shot, clean surface, rich colours, studio lighting",
    "close-up of a stethoscope, ID badge lanyard, and a flower on a wooden surface — symbolic of {event}, warm light",
    "environmental wide shot of a care home corridor with warm lighting, documentary style, sense of community and purpose",
]

_VISUAL_APPROACHES = [
    "tight portrait, direct eye contact with camera, shallow depth of field, warm bokeh background",
    "wide shot, person centred in frame, sense of space and purpose",
    "candid side-on moment, subject unaware, natural documentary feel",
    "group of 2-3 people interacting, human connection, warm and natural light",
    "action shot mid-movement, energy and dynamism, motion blur background",
    "moody split lighting, half shadow half warm light, contemplative expression",
    "low angle looking up at subject, confident and empowering",
    "subject looking off-camera, thoughtful and engaged",
]

def _custom_image_prompts(scene_description, count=5):
    import random
    people = list(_UNIFORM_PEOPLE)
    random.shuffle(people)
    while len(people) < count:
        people.extend(_UNIFORM_PEOPLE)
    return [
        f"{people[i]}, working in a care or healthcare setting. "
        f"{scene_description}. Photorealistic, warm natural lighting, sharp focus, 8k."
        for i in range(count)
    ]


def ai_image_prompts(event_name, count=5, redo_indices=None):
    """Ask Claude for visually varied image prompts for this event."""
    try:
        client = _ai_client()
        msg = _claude_create(
            client,
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": (
                f"Write {count} image generation prompts for a social media graphic about: {event_name}\n"
                f"Context: Cucumber Recruitment, UK care staffing agency.\n\n"
                f"CRITICAL: Each image must be VISUALLY UNMISTAKABLE as {event_name}. "
                f"A viewer with no text should immediately recognise what the image is about. "
                f"Think: what colours, symbols, settings, objects, activities, or emotions are unique to {event_name}? "
                f"Build those visual elements INTO the scene — do not just put a person standing around.\n\n"
                f"Examples of what 'visually specific' means:\n"
                f"- Pride Month → rainbow colours, celebration, joy, pride flags, diverse group\n"
                f"- Mental Health Awareness → calm nature setting, hands holding, hopeful light, green spaces\n"
                f"- Nurses Day → clinical setting, caring gesture, hands-on patient care\n"
                f"- Anti-Bullying Week → hands joined together, unity, warm supportive gesture\n"
                f"Apply this thinking to {event_name} — use its specific symbols, palette and mood.\n\n"
                f"Make the {count} images visually varied in composition — mix freely from portraits, "
                f"candid mid-shots, wide scenes, object/detail close-ups, action moments.\n\n"
                f"RULES:\n"
                f"- Any person shown must wear a dark forest green (#16661f) polo shirt and a company ID badge on a lanyard\n"
                f"- When a person appears: face must be fully visible, centred vertically in the frame, with at least 15% clear background above the top of the head — never crop the head\n"
                f"- Vary the lighting and colour mood across the set\n"
                f"- Image 1 ONLY: the subject must be centred in the frame (it will be cropped into a circle)\n\n"
                f"Return ONLY a JSON array of {count} strings. Each ends with: photorealistic, sharp focus, 8k."
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




def _ai_fresh_taglines(event_name, count=5):
    """Always call Claude for new taglines — never returns presets. Used for redo."""
    date_hint = ""
    prompt = (
        f"You are a copywriter for Cucumber Recruitment, a UK healthcare staffing agency.\n"
        f"Generate {count} FRESH, VARIED social media graphic taglines for: {event_name}\n\n"
        f"Rules:\n"
        f"- HEADLINE: 2–5 words, ALL CAPS, punchy and direct\n"
        f"- SUBTEXT: 5–8 words, ALL CAPS, warm and supportive\n"
        f"- Each must feel distinct — vary the angle and tone\n"
        f"- Do NOT use these overused openers: CELEBRATE, HAPPY, PROUD\n"
        f"- No hashtags, no emojis, no punctuation beyond full stops and commas\n\n"
        f"Return ONLY a JSON array:\n"
        f'[{{"headline": "...", "subtext": "..."}}, ...]'
    )
    try:
        client = _ai_client()
        msg = _claude_create(
            client,
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            raw = match.group(0)
        pairs = json.loads(raw)
        return [(p["headline"].upper(), p["subtext"].upper()) for p in pairs[:count]]
    except Exception as e:
        app.logger.warning("Fresh taglines failed for %s (%s), shuffling presets", event_name, e)
        import random
        fallback = list(_fallback_taglines(event_name))
        random.shuffle(fallback)
        return fallback


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

    # Names of already-created events — suppress matching calendar entries
    created_names = {e["name"].lower().strip() for e in events}

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
        if name.lower().strip() not in created_names:
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
    ip = request.remote_addr or ""
    existing_vote = db.execute(
        """SELECT vo.variant_id, v.variant_num FROM votes vo
           JOIN variants v ON v.id = vo.variant_id
           WHERE v.event_id=? AND vo.ip_address=?
           ORDER BY vo.id DESC LIMIT 1""",
        (event_id, ip)
    ).fetchone()
    db.close()
    all_generated  = bool(variants) and all(v["image_path"] for v in variants)
    has_any_images = any(v["image_path"] for v in variants)
    return render_template("event.html",
        event=event, variants=variants, all_generated=all_generated,
        has_any_images=has_any_images, generating=False,
        gen_progress=None, format_labels=FORMAT_LABELS,
        buffer_enabled=True, buffer_ready=bool(_buffer_token()),
        already_voted=bool(existing_vote),
        voted_variant_id=existing_vote["variant_id"] if existing_vote else None)


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
        redo_variants = [v for v in variants if v["id"] in redo_ids]
        fresh_pairs = _ai_fresh_taglines(event["name"], len(redo_variants))
        for i, v in enumerate(redo_variants):
            form_hl = request.form.get(f"headline_{v['id']}", "").strip().upper()
            form_st = request.form.get(f"subtext_{v['id']}",  "").strip().upper()
            # Use manually edited text if user changed it; otherwise fresh AI text
            user_edited = (form_hl and form_hl != v["headline"].upper()) or \
                          (form_st and form_st != v["subtext"].upper())
            if user_edited:
                new_hl = form_hl or v["headline"]
                new_st = form_st or v["subtext"]
            else:
                new_hl, new_st = fresh_pairs[i % len(fresh_pairs)]
            db.execute("UPDATE variants SET headline=?, subtext=? WHERE id=?",
                       (new_hl, new_st, v["id"]))
            photo = PHOTO_DIR / f"ev{event_id}_v{v['variant_num']}.jpg"
            photo.unlink(missing_ok=True)
            Path(str(photo) + ".rmbg.png").unlink(missing_ok=True)
            (GFX_DIR / str(event_id) / f"v{v['variant_num']}.png").unlink(missing_ok=True)
            db.execute("UPDATE variants SET image_path=NULL WHERE id=?", (v["id"],))
        db.commit()
        variants = db.execute(
            "SELECT * FROM variants WHERE event_id=? ORDER BY variant_num", (event_id,)
        ).fetchall()
        flash(f"Regenerated {len(redo_ids)} image variant(s)", "success")

    fmt = request.form.get("output_format", "square")
    if fmt in CANVAS_SIZES:
        db.execute("UPDATE events SET output_format=? WHERE id=?", (fmt, event_id))
        db.commit()
    else:
        fmt = event["output_format"] or "square"

    custom_prompt = request.form.get("custom_prompt", "").strip()
    redo_indices = [i for i, v in enumerate(variants) if v["id"] in redo_ids] if redo_ids else None
    if custom_prompt:
        prompts = _custom_image_prompts(custom_prompt, len(variants))
    else:
        prompts, _ = ai_image_prompts(event["name"], len(variants), redo_indices=redo_indices)

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
                        str(out), photo_path=str(photo), template=v["variant_num"], size=fmt)
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
        flash(f"Generated {ok} image(s).", "success")
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

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if request.method == "POST":
        if already_voted:
            db.close()
            if is_ajax:
                return jsonify({"error": "Already voted"}), 400
            flash("You've already voted on this event.", "error")
            return redirect(url_for("event_detail", event_id=event_id))
        variant_id = request.form.get("variant_id", type=int)
        voter_name = (request.form.get("voter_name") or "Anonymous").strip()
        if variant_id:
            db.execute("INSERT INTO votes (variant_id, voter_name, ip_address) VALUES (?,?,?)",
                       (variant_id, voter_name, ip))
            db.commit()
            voted_events.append(event_id)
            session["voted_events"] = voted_events
        db.close()
        if is_ajax:
            return jsonify({"ok": True, "variant_id": variant_id})
        return redirect(url_for("event_detail", event_id=event_id))

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
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    return redirect(url_for("event_detail", event_id=event_id))


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
    return render_template("results.html", event=event, variants=variants, total=total,
                           buffer_enabled=True, buffer_ready=bool(_buffer_token()))


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


# ── Generate All ──────────────────────────────────────────────────────

_gen_all_state = {"running": False, "done": 0, "total": 0}


def _run_generate_all():
    global _gen_all_state
    try:
        db = get_db()
        events = db.execute("""
            SELECT e.id, e.name FROM events e
            WHERE EXISTS (
                SELECT 1 FROM variants v
                WHERE v.event_id = e.id AND v.image_path IS NULL
            )
        """).fetchall()
        _gen_all_state["total"] = len(events)
        _gen_all_state["done"]  = 0

        for event in events:
            eid  = event["id"]
            name = event["name"]
            variants = db.execute(
                "SELECT * FROM variants WHERE event_id=? AND image_path IS NULL ORDER BY variant_num",
                (eid,)
            ).fetchall()

            prompts, _ = ai_image_prompts(name, len(variants))
            out_dir = GFX_DIR / str(eid)
            out_dir.mkdir(parents=True, exist_ok=True)
            now = datetime.utcnow().isoformat()

            def _proc(args):
                i, v = args
                photo = PHOTO_DIR / f"ev{eid}_v{v['variant_num']}.jpg"
                out   = out_dir / f"v{v['variant_num']}.png"
                prompt = prompts[i] if i < len(prompts) else prompts[-1]
                if not photo.exists() and not _nano_banana_photo(prompt, photo):
                    return (v["id"], None)
                try:
                    gen_graphic(v["headline"], v["subtext"], "cucumber-recruitment.co.uk",
                                str(out), photo_path=str(photo), template=v["variant_num"])
                    return (v["id"], str(out))
                except Exception as e:
                    app.logger.error("gen-all render %s: %s", v["variant_num"], e)
                    return (v["id"], None)

            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(_proc, enumerate(variants)))

            for vid, path in results:
                if path:
                    db.execute("UPDATE variants SET image_path=?, generated_at=? WHERE id=?",
                               (path, now, vid))
            db.commit()
            _gen_all_state["done"] += 1

        db.close()
    finally:
        _gen_all_state["running"] = False


@app.route("/generate-all", methods=["POST"])
def generate_all():
    global _gen_all_state
    if _gen_all_state["running"]:
        return jsonify(_gen_all_state)
    _gen_all_state = {"running": True, "done": 0, "total": 0}
    import threading
    threading.Thread(target=_run_generate_all, daemon=True).start()
    db = get_db()
    total = db.execute("""
        SELECT COUNT(DISTINCT e.id) FROM events e
        WHERE EXISTS (SELECT 1 FROM variants v WHERE v.event_id=e.id AND v.image_path IS NULL)
    """).fetchone()[0]
    db.close()
    _gen_all_state["total"] = total
    return jsonify({"total": total})


@app.route("/generate-all/status")
def generate_all_status():
    return jsonify(_gen_all_state)


@app.route("/email-template")
def email_template():
    return render_template("email_template.html")


@app.route("/cucumber_logo.png")
def serve_logo():
    return send_from_directory(ROOT, "cucumber_logo.png")


# ── Variant text editing ─────────────────────────────────────────────

@app.route("/variants/<int:variant_id>/text", methods=["POST"])
def update_variant_text(variant_id):
    headline = request.form.get("headline", "").strip().upper()
    subtext  = request.form.get("subtext",  "").strip().upper()
    if not headline or not subtext:
        return jsonify({"error": "Headline and subtext cannot be empty"}), 400
    db = get_db()
    row = db.execute("SELECT id FROM variants WHERE id=?", (variant_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "Variant not found"}), 404
    db.execute("UPDATE variants SET headline=?, subtext=? WHERE id=?",
               (headline, subtext, variant_id))
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ── Buffer ────────────────────────────────────────────────────────────

_BUFFER_API = "https://api.bufferapp.com/1"


def _buffer_token():
    return os.environ.get("BUFFER_TOKEN", "")


@app.route("/events/<int:event_id>/buffer", methods=["POST"])
def buffer_post(event_id):
    token = _buffer_token()
    if not token:
        return jsonify({"error": "BUFFER_TOKEN not set in environment"}), 400

    variant_id = request.form.get("variant_id", type=int)
    caption    = request.form.get("caption", "").strip()

    db      = get_db()
    event   = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    variant = db.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
    db.close()

    if not event or not variant or not variant["image_path"]:
        return jsonify({"error": "Image not found"}), 404

    try:
        prof_r = req_lib.get(f"{_BUFFER_API}/profiles.json",
                             params={"access_token": token}, timeout=10)
        if prof_r.status_code != 200:
            return jsonify({"error": f"Buffer auth failed ({prof_r.status_code})"}), 400
        profiles = prof_r.json()
    except Exception as e:
        return jsonify({"error": f"Could not reach Buffer: {e}"}), 500

    if not profiles:
        return jsonify({"error": "No Buffer profiles connected"}), 400

    if not caption:
        caption = (
            f"{variant['headline']}\n\n"
            f"{variant['subtext']}\n\n"
            f"cucumber-recruitment.co.uk"
        )

    image_url    = url_for("serve_image", event_id=event_id,
                           filename=f"v{variant['variant_num']}.png", _external=True)
    scheduled_at = request.form.get("scheduled_at", "").strip()
    post_now     = request.form.get("post_now", "") == "1"

    data = [("access_token", token), ("text", caption), ("media[photo]", image_url)]
    for p in profiles:
        data.append(("profile_ids[]", p["id"]))
    if scheduled_at:
        data.append(("scheduled_at", scheduled_at))
    elif post_now:
        data.append(("now", "true"))

    try:
        post_r = req_lib.post(f"{_BUFFER_API}/updates/create.json", data=data, timeout=15)
        if post_r.status_code == 200:
            return jsonify({"ok": True, "profiles": len(profiles)})
        err = post_r.json().get("message", "Unknown error")
        return jsonify({"error": err}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Care News ────────────────────────────────────────────────────

_NEWS_FEEDS = [
    ("Children", "https://www.communitycare.co.uk/category/children-young-people/feed/"),
    ("Adults",   "https://www.communitycare.co.uk/category/adults/feed/"),
]
_NEWS_CACHE = {"data": None, "ts": 0}
_NEWS_LOCK  = threading.Lock()
_NEWS_TTL   = 1800  # 30 min

def _fetch_news():
    articles = []
    for category, url in _NEWS_FEEDS:
        try:
            r = req_lib.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link  = (item.findtext("link")  or "").strip()
                desc  = re.sub(r"<[^>]+>", "", item.findtext("description") or "")[:220].strip()
                pub   = (item.findtext("pubDate") or "").strip()
                if title and link:
                    articles.append({"category": category, "title": title,
                                     "link": link, "description": desc, "pub_date": pub})
        except Exception:
            pass
    return articles

@app.route("/care-news")
def care_news():
    bust = request.args.get("refresh") == "1"
    with _NEWS_LOCK:
        now = time.time()
        if bust or not _NEWS_CACHE["data"] or now - _NEWS_CACHE["ts"] > _NEWS_TTL:
            _NEWS_CACHE["data"] = _fetch_news()
            _NEWS_CACHE["ts"] = now
        articles = list(_NEWS_CACHE["data"])
    error = None if articles else "Could not load news right now. Please try refreshing."
    return render_template("care_news.html", articles=articles, error=error)


# Always run on startup (works with both gunicorn and direct)
init_db()
GFX_DIR.mkdir(parents=True, exist_ok=True)
PHOTO_DIR.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
