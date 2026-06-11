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

PERSON_APPROACHES = [
    "tight portrait, direct eye contact, warm smile, natural indoor setting, soft bokeh, shallow depth of field",
    "confident mid-shot in a professional environment, warm natural window light, depth of field",
    "candid moment, thoughtful expression, blurred warm workplace background, natural light",
    "low angle looking up, empowering, outdoor environment with natural sky",
]

EVENT_SCENES = {
    "anti-bullying":       ["hands of different skin tones joined together in solidarity, warm close-up, natural light", "chain of colourful paper people standing together, overhead flat-lay on white surface", "one hand reaching out to help another, symbolic and warm, shallow depth of field"],
    "men's health":        ["running shoes, water bottle and apple arranged on a clean white surface, overhead flat-lay", "a green plant growing strongly in sunlight on a windowsill, symbolic of health and growth", "man's hands holding a small thriving plant, nurturing, warm natural light"],
    "pride":               ["rainbow colours blending in a joyful abstract wave, vibrant, clean white background", "colourful pride flags in soft bokeh, warm celebration light, no people", "hands of different skin tones forming a heart with rainbow wristbands, close-up"],
    "carers":              ["two mugs of tea side by side on a wooden table, warm natural light, comforting", "a single sunflower in a vase on a sunlit windowsill, symbolic of care and warmth", "one pair of hands gently holding another older pair of hands, close and tender"],
    "world aids day":      ["a single red ribbon centred on a clean white surface, bold and symbolic, studio lighting", "red candles glowing around a red ribbon, warm memorial atmosphere, golden light", "collection of red ribbons arranged artistically, meaningful, clean background"],
    "learning disability": ["colourful jigsaw puzzle pieces completing a picture together, overhead flat-lay, bright colours", "diverse hands of different sizes coming together, inclusive and warm", "bright sunflowers of different heights in a row, symbolic of diversity, golden light"],
    "mental health":       ["hands gently cradling a small green plant, nurturing, soft natural light", "calm still lake at golden hour, peaceful and restorative, no people", "a single lit candle in a dark room, warm hopeful glow, darkness around it"],
    "diabetes":            ["bold blue circle on a clean white background, centred and powerful, studio lighting", "fresh colourful fruits and vegetables arranged in a circle, health and vitality, overhead shot", "a glucose monitor beside a green apple on a clean white surface, minimal and clear"],
    "national inclusion":  ["hands of many different skin tones stacked on top of each other, unity, close-up, warm light", "a colourful mosaic heart made of small tiles, diversity symbolism, overhead flat-lay", "open door leading to a bright welcoming space, opportunity and inclusion, wide angle"],
    "inclusion":           ["hands of many different skin tones stacked on top of each other, unity, close-up, warm light", "a colourful mosaic heart made of small tiles, diversity symbolism, overhead flat-lay", "open door leading to a bright welcoming space, opportunity and inclusion, wide angle"],
    "saturday spotlight":  ["a single dramatic spotlight beam on a dark stage floor, bold, no people", "a microphone on a clean stage, bold and simple, warm spotlight", "an open door leading to warm bright light, opportunity and possibility, wide angle"],
    "open day":            ["an open door leading to a bright welcoming modern office, warm natural light, wide angle", "a welcome sign on a clean desk with fresh flowers, professional and inviting", "keys on a clean desk beside a thriving green plant, new beginnings, warm light"],
    "volunteer":           ["many hands arranging flowers or planting together, community spirit, warm natural light", "a neatly tied bunch of wildflowers on a wooden table, gift of care, warm light", "open hands offering a small glowing heart, symbolic of giving, warm light"],
    "recruitment":         ["a clean modern desk with a laptop and coffee, professional workspace, natural light", "a handshake close-up, professional and warm, shallow depth of field", "upward staircase leading to bright light, career growth symbolism, wide angle"],
}

def get_scene_prompts(event_name):
    key = event_name.lower()
    for k, scenes in EVENT_SCENES.items():
        if k in key:
            return list(scenes)
    return [
        f"symbolic objects representing {event_name}, styled flat-lay on a clean white surface, overhead shot, rich colours",
        f"atmospheric scene capturing the spirit of {event_name}, golden hour light, no people, photojournalistic",
        f"hands in a meaningful gesture related to {event_name}, warm intimate lighting, shallow depth of field",
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


def make_prompts(event_name):
    """5 prompts: 2 person-in-uniform + 3 contextual/symbolic, shuffled."""
    people = random.sample(UNIFORM_PEOPLE, 2)
    p_app  = random.sample(PERSON_APPROACHES, 2)
    scenes = get_scene_prompts(event_name)
    random.shuffle(scenes)

    prompts = []
    for person, approach in zip(people, p_app):
        prompts.append(
            f"{person}, dressed in a dark forest green (#1a5c28) polo shirt with a company ID badge on a lanyard. "
            f"{event_name} theme. {approach}. Photorealistic, sharp focus, 8k."
        )
    for scene in scenes[:3]:
        prompts.append(f"Photorealistic image. {scene}. Sharp focus, 8k.")

    random.shuffle(prompts)
    return prompts


def process_variant(event_id, event_name, v, prompt):
    num   = v["variant_num"]
    photo = PHOTO_DIR / f"ev{event_id}_v{num}.jpg"
    out   = GFX_DIR / str(event_id) / f"v{num}.png"

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
    prompts  = make_prompts(name)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda args: process_variant(eid, name, args[1], prompts[args[0]]), enumerate(variants)))

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
