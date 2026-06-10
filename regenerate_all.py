"""Wipe and regenerate all event graphics with the correct Cucumber uniform prompt."""
import os, sys, sqlite3, random
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("NANO_BANANA_KEY", "AIzaSyDTthx8BsgWIz3vJ8sFjcHQ_Tz2HgGnSBM")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from generate_graphic import generate as gen_graphic

import requests as req_lib, base64

PHOTO_DIR = ROOT / "photos" / "web"
GFX_DIR   = ROOT / "graphics" / "web"
DB_PATH   = ROOT / "events.db"

NB_PRO_URL   = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent"
NB_FLASH_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"

UNIFORM_PEOPLE = [
    "young Black woman, short natural hair, warm smile, confident posture",
    "middle-aged South Asian man, friendly expression, professional demeanour",
    "young white woman, blonde hair, bright smile, poised and approachable",
    "older Black man, grey beard, calm confident expression",
    "young mixed-race woman, curly hair, natural and professional pose",
]

VISUAL_APPROACHES = [
    "tight portrait, direct eye contact with camera, shallow depth of field, warm bokeh background",
    "wide shot, person centred in frame, sense of space and purpose",
    "candid side-on moment, subject unaware, natural documentary feel",
    "action shot mid-movement, energy and dynamism, motion blur background",
    "moody split lighting, half shadow half warm light, contemplative expression",
]


def nb_photo(prompt, out_path):
    key = os.environ["NANO_BANANA_KEY"]
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "1:1"}},
    }
    for label, url in [("Pro", NB_PRO_URL), ("Flash", NB_FLASH_URL)]:
        try:
            r = req_lib.post(f"{url}?key={key}", json=body, timeout=90)
            if r.status_code == 200:
                parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                for part in parts:
                    if "inlineData" in part and part["inlineData"].get("data"):
                        out_path.write_bytes(base64.b64decode(part["inlineData"]["data"]))
                        print(f"    [{label}] photo saved")
                        return True
            print(f"    [{label}] {r.status_code}: {r.text[:80]}")
        except Exception as e:
            print(f"    [{label}] error: {e}")
    return False


def make_prompt(event_name, variant_num):
    idx = (variant_num - 1) % len(UNIFORM_PEOPLE)
    person   = UNIFORM_PEOPLE[idx]
    approach = VISUAL_APPROACHES[idx]
    return (
        f"{person}, dressed in a dark forest green (#1a5c28) polo shirt "
        f"with a company ID badge on a lanyard. "
        f"{event_name} scene. {approach}. "
        f"Pure white background, studio lighting, photorealistic, sharp focus, 8k."
    )


def process_variant(event_id, event_name, v):
    num   = v["variant_num"]
    photo = PHOTO_DIR / f"ev{event_id}_v{num}.jpg"
    out   = GFX_DIR / str(event_id) / f"v{num}.png"
    prompt = make_prompt(event_name, num)

    print(f"  V{num}: {v['headline'][:40]}")
    if not nb_photo(prompt, photo):
        print(f"  V{num}: photo generation FAILED")
        return (v["id"], None)

    try:
        gen_graphic(v["headline"], v["subtext"], "cucumber-recruitment.co.uk",
                    str(out), photo_path=str(photo))
        print(f"  V{num}: graphic rendered OK")
        return (v["id"], str(out))
    except Exception as e:
        print(f"  V{num}: render error: {e}")
        return (v["id"], None)


PHOTO_DIR.mkdir(parents=True, exist_ok=True)
GFX_DIR.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
events = conn.execute("SELECT * FROM events ORDER BY id").fetchall()

for event in events:
    eid   = event["id"]
    name  = event["name"]
    variants = conn.execute(
        "SELECT * FROM variants WHERE event_id=? ORDER BY variant_num", (eid,)
    ).fetchall()
    if not variants:
        continue

    print(f"\n{'='*50}")
    print(f"Event {eid}: {name} ({len(variants)} variants)")

    # Wipe old photos + graphics
    for v in variants:
        (PHOTO_DIR / f"ev{eid}_v{v['variant_num']}.jpg").unlink(missing_ok=True)
        (PHOTO_DIR / f"ev{eid}_v{v['variant_num']}.jpg.rmbg.png").unlink(missing_ok=True)
        gfx = GFX_DIR / str(eid) / f"v{v['variant_num']}.png"
        gfx.unlink(missing_ok=True)
    conn.execute("UPDATE variants SET image_path=NULL, generated_at=NULL WHERE event_id=?", (eid,))
    conn.commit()

    (GFX_DIR / str(eid)).mkdir(parents=True, exist_ok=True)
    variants = [dict(v) for v in variants]

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda v: process_variant(eid, name, v), variants))

    now = datetime.utcnow().isoformat()
    ok = 0
    for vid, path in results:
        if path:
            conn.execute(
                "UPDATE variants SET image_path=?, generated_at=? WHERE id=?",
                (path, now, vid)
            )
            ok += 1
    conn.commit()
    print(f"  -> {ok}/{len(variants)} generated")

conn.close()
print("\nAll done.")
