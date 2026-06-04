#!/usr/bin/env python3
"""
Cucumber Recruitment — AI Photo Generator + Batch Renderer
Generates 5 DIFFERENT photos per event (one per variant) then renders all graphics.

Run: python generate_with_photos.py
"""

import time
import urllib.request
import urllib.parse
from pathlib import Path
from generate_graphic import generate
from generate_batch import EVENTS

OUT_DIR   = Path(__file__).parent / "graphics"
PHOTO_DIR = Path(__file__).parent / "photos"
PHOTO_DIR.mkdir(exist_ok=True)

# ── 5 base person descriptions (reused across events) ─────────────
BASE_PEOPLE = [
    "professional photograph of a young Black female nurse wearing green scrubs, arms crossed, warm confident smile, studio lighting, white background, Canon 85mm, sharp focus, photorealistic",
    "professional photograph of a middle aged white male nurse wearing blue scrubs, stethoscope, warm friendly smile, studio lighting, white background, Canon 85mm, sharp focus, photorealistic",
    "professional photograph of a young South Asian female nurse wearing teal scrubs, professional pose, bright smile, studio lighting, white background, Canon 85mm, sharp focus, photorealistic",
    "professional photograph of a young white female healthcare assistant wearing blue scrubs, big bright smile, studio lighting, white background, Canon 85mm, sharp focus, photorealistic",
    "professional photograph of a Black male nurse and white female nurse in scrubs standing together smiling, studio lighting, white background, sharp focus, photorealistic",
]

# ── Event-specific overrides (all 5 slots) ────────────────────────
EVENT_PEOPLE = {
    "Mens_Health_Week": [
        "young Black male nurse in blue scrubs arms crossed confident smile",
        "middle aged white male nurse in scrubs warm friendly smile stethoscope",
        "young South Asian male healthcare worker in scrubs professional pose",
        "older white male nurse in scrubs experienced calm expression",
        "two male nurses diverse smiling together professional",
    ],
    "Fathers_Day": [
        "smiling Black male nurse in scrubs proud confident pose",
        "middle aged white male nurse in scrubs warm friendly smile",
        "young South Asian male healthcare worker in scrubs smiling",
        "older white male nurse in scrubs experienced proud expression",
        "male nurse in scrubs holding small gift or card smiling",
    ],
    "Kings_Birthday": [
        "diverse group of smiling nurses and healthcare workers in scrubs celebratory",
        "female and male nurses smiling together waving cheerfully scrubs",
        "Black female nurse in scrubs big smile celebratory pose",
        "group of three nurses diverse scrubs happy smiling",
        "mixed group healthcare workers scrubs thumbs up cheerful",
    ],
    "Pride_Month": [
        "diverse group of smiling nurses scrubs rainbow lanyards",
        "Black female nurse scrubs rainbow badge smiling confident",
        "South Asian female nurse scrubs rainbow pin warm smile",
        "male and female nurses diverse scrubs rainbow lanyards smiling together",
        "young white female nurse scrubs pride badge confident smile",
    ],
}

SUFFIX = "full body, pure white background, isolated cutout, studio portrait, photorealistic, high quality"
RAINBOW_EVENTS = {"Pride_Month"}


def download_photo(key, prompt, seed, retries=3):
    out_path = PHOTO_DIR / f"{key}.jpg"
    if out_path.exists():
        print(f"  Cached: {out_path.name}")
        return out_path

    encoded = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=600&height=700&nologo=true&seed={seed}"

    for attempt in range(1, retries + 1):
        try:
            print(f"  Downloading ({attempt}/{retries}): {key}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
            out_path.write_bytes(data)
            print(f"  Done: {out_path.name} ({len(data)//1024}KB)")
            return out_path
        except Exception as e:
            print(f"  Attempt {attempt} failed: {e}")
            if attempt < retries:
                time.sleep(5)

    print(f"  Failed — will use placeholder for {key}")
    return None


def run():
    # Build full list of photo keys needed (one per variant per event)
    needed = []
    for event_key in EVENTS:
        people = EVENT_PEOPLE.get(event_key, BASE_PEOPLE)
        for v_idx in range(5):
            photo_key  = f"{event_key}_v{v_idx + 1}"
            person     = people[v_idx] if v_idx < len(people) else BASE_PEOPLE[v_idx]
            prompt     = f"{person}, {SUFFIX}"
            needed.append((photo_key, prompt))

    print(f"Step 1: Downloading {len(needed)} AI photos...\n")
    photos = {}
    for idx, (photo_key, prompt) in enumerate(needed):
        seed = 100 + idx * 37          # unique seed per photo
        photos[photo_key] = download_photo(photo_key, prompt, seed)
        time.sleep(1)

    total = sum(len(v["variants"]) for v in EVENTS.values())
    print(f"\nStep 2: Rendering {total} graphics...\n")

    done = 0
    for event_key, event in EVENTS.items():
        event_dir = OUT_DIR / event_key
        event_dir.mkdir(parents=True, exist_ok=True)
        rainbow = event_key in RAINBOW_EVENTS

        print(f"  {event['label']}")
        for i, (headline, subtext) in enumerate(event["variants"], start=1):
            photo_path = photos.get(f"{event_key}_v{i}")
            out_path   = event_dir / f"v{i}.png"
            generate(
                headline, subtext,
                "cucumber-recruitment.co.uk",
                str(out_path),
                photo_path=str(photo_path) if photo_path else None,
                rainbow=rainbow,
            )
            done += 1
        print()

    print(f"Done. {done} graphics saved to: {OUT_DIR}")


if __name__ == "__main__":
    run()
