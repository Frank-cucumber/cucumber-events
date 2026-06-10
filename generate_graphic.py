#!/usr/bin/env python3
"""
Cucumber Recruitment — Social Media Graphic Generator
Produces a 1080x1080 PNG matching the branded template.

Usage:
  python generate_graphic.py --headline "CARERS WEEK 2026" \
      --subtext "THANK YOU TO EVERY UNPAID CARER IN THE UK." \
      --out carers_week.png

  python generate_graphic.py --list            # show upcoming events from calendar
  python generate_graphic.py --event 3         # generate graphic for event #3 in the list
"""

import argparse
import os
import sys
import re
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── Brand colours ─────────────────────────────────────────────────
GREEN = (26, 92, 40)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
PLACEHOLDER = (210, 210, 210)

# ── Canvas ────────────────────────────────────────────────────────
W, H = 1080, 1080

# ── Layout zones (px) ─────────────────────────────────────────────
BANNER_H  = 155      # top green bar
DIVIDER_Y = 470      # y-start of divider line
DIVIDER_H = 7
SPLIT_Y   = DIVIDER_Y + DIVIDER_H   # start of photo/subtext zone
FOOTER_Y  = 950      # start of bottom green bar
PAD       = 50       # left/right padding

# ── Fonts ─────────────────────────────────────────────────────────
_FONT_DIR  = Path(r"C:\Windows\Fonts")
_MONTSERRAT = Path(__file__).parent / "Montserrat-ExtraBold.ttf"

def _load(name, size):
    try:
        return ImageFont.truetype(str(_FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()

def font_heavy(size):
    # Montserrat ExtraBold — wide, rounded, matches reference font
    try:
        return ImageFont.truetype(str(_MONTSERRAT), size)
    except OSError:
        return _load("impact.ttf", size)

def font_bold(size):    return _load("arialbd.ttf",  size)
def font_regular(size): return _load("arial.ttf",    size)


# ── Text helpers ──────────────────────────────────────────────────

def wrap(draw, text, font, max_px):
    """Word-wrap text to fit within max_px width."""
    words = text.split()
    lines, cur = [], []
    for word in words:
        trial = " ".join(cur + [word])
        w = draw.textlength(trial, font=font)
        if w <= max_px or not cur:
            cur.append(word)
        else:
            lines.append(" ".join(cur))
            cur = [word]
    if cur:
        lines.append(" ".join(cur))
    return lines


def block_height(draw, lines, font):
    if not lines:
        return 0
    h = draw.textbbox((0, 0), lines[0], font=font)[3]
    gap = int(h * 0.08)
    return h * len(lines) + gap * (len(lines) - 1)


def auto_fit(draw, text, font_fn, max_w, max_h, hi=130, lo=38, step=4):
    """Return (font, lines) that fit within max_w × max_h."""
    for size in range(hi, lo - 1, -step):
        f = font_fn(size)
        lines = wrap(draw, text, f, max_w)
        if block_height(draw, lines, f) <= max_h:
            return f, lines
    f = font_fn(lo)
    return f, wrap(draw, text, f, max_w)


# ── Zone renderers ────────────────────────────────────────────────

_ANTON_PATH = Path(__file__).parent / "Anton-Regular.ttf"
_LOGO_PATH  = Path(__file__).parent / "cucumber_logo.png"

def font_anton(size):
    try:
        return ImageFont.truetype(str(_ANTON_PATH), size)
    except OSError:
        return font_heavy(size)

def draw_banner(draw):
    """Dark green top bar with the real Cucumber Recruitment logo."""
    draw.rectangle([0, 0, W, BANNER_H], fill=GREEN)

    if _LOGO_PATH.exists():
        logo = Image.open(_LOGO_PATH).convert("RGBA")
        # Scale to fit banner
        target_h = BANNER_H - 24
        ratio = target_h / logo.height
        target_w = int(logo.width * ratio)
        logo = logo.resize((target_w, target_h), Image.LANCZOS)
        lx = (W - target_w) // 2
        ly = (BANNER_H - target_h) // 2
        # Paste the logo with transparency
        draw._image.paste(logo, (lx, ly), logo)
    else:
        # Fallback if logo file missing
        bw, bh = 310, 92
        bx = (W - bw) // 2
        by = (BANNER_H - bh) // 2
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=14, fill=WHITE)
        draw.text((bx + 56, by + 17), "cucumber",    font=font_bold(30),    fill=GREEN)
        draw.text((bx + 56, by + 53), "recruitment", font=font_regular(17), fill=GREEN)


def draw_headline(draw, text):
    """Large bold headline in the white zone."""
    max_w = W - PAD * 2
    max_h = DIVIDER_Y - BANNER_H - 20
    font, lines = auto_fit(draw, text.upper(), font_heavy, max_w, max_h, hi=145, lo=52)

    sample_h = draw.textbbox((0, 0), lines[0], font=font)[3]
    gap = int(sample_h * 0.04)
    total = block_height(draw, lines, font)
    y = BANNER_H + ((DIVIDER_Y - BANNER_H) - total) // 2

    for line in lines:
        draw.text((PAD, y), line, fill=GREEN, font=font)
        y += sample_h + gap


def draw_divider(draw):
    draw.rectangle([PAD, DIVIDER_Y, W - PAD, DIVIDER_Y + DIVIDER_H], fill=GREEN)


def _fade_left(photo, fade_width=220):
    """Blend the left edge of photo into white using a gradient mask."""
    w, h = photo.size
    mask = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(mask)
    for x in range(fade_width):
        d.line([(x, 0), (x, h - 1)], fill=int(255 * x / fade_width))
    white = Image.new("RGB", (w, h), WHITE)
    return Image.composite(photo, white, mask)


def _rainbow_bg(img, top, bottom):
    """Soft pastel rainbow gradient across the right half of the split zone."""
    bands = [(255,180,180),(255,220,160),(255,255,160),(180,255,180),(160,200,255),(200,160,255)]
    right_w = W - W // 2
    band_w  = right_w // len(bands) + 1
    for i, rgb in enumerate(bands):
        x = W // 2 + i * band_w
        img.paste(Image.new("RGB", (band_w, bottom - top), rgb), (x, top))


def _remove_bg(photo_path):
    """Remove near-white background using threshold + edge smoothing.
    Works without rembg on Railway. Falls back to rembg locally if available."""
    import os
    cache = Path(str(photo_path) + ".rmbg.png")
    if cache.exists():
        return Image.open(cache).convert("RGB")

    on_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT"))

    if not on_railway:
        try:
            import io
            from rembg import remove as rembg_remove
            with open(photo_path, "rb") as f:
                result = rembg_remove(f.read())
            rgba = Image.open(io.BytesIO(result)).convert("RGBA")
            bbox = rgba.getbbox()
            if bbox:
                rgba = rgba.crop(bbox)
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            rgb = Image.alpha_composite(white, rgba).convert("RGB")
            rgb.save(cache)
            return rgb
        except Exception:
            pass

    # Lightweight threshold-based removal (works on HF white-background images)
    img = Image.open(photo_path).convert("RGBA")
    import numpy as np
    arr = np.array(img).astype(int)
    r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]
    # Near-white: all channels > 230
    bg_mask = (r > 230) & (g > 230) & (b > 230)
    arr[bg_mask, 3] = 0
    rgba = Image.fromarray(arr.astype(np.uint8))
    # Crop to non-transparent bounding box
    bbox = rgba.getbbox()
    if bbox:
        rgba = rgba.crop(bbox)
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(white, rgba).convert("RGB")
    rgb.save(cache)
    return rgb


def draw_split(draw, subtext, photo_path=None, rainbow=False):
    """Left: bold subtext + heart. Right: large dominant person photo."""
    img     = draw._image
    photo_x = int(W * 0.38)          # person starts at 38% — very dominant
    ph_w    = W - photo_x
    ph_h    = H - SPLIT_Y            # extends full height including footer

    if rainbow:
        _rainbow_bg(img, SPLIT_Y, FOOTER_Y)

    if photo_path and Path(photo_path).exists():
        try:
            photo = _remove_bg(photo_path)
        except Exception:
            photo = Image.open(photo_path).convert("RGB")
        # Scale to fit zone height (full body visible), centre in zone
        ratio = ph_h / photo.height
        new_w = int(photo.width * ratio)
        photo = photo.resize((new_w, ph_h), Image.LANCZOS)
        canvas = Image.new("RGB", (ph_w, ph_h), WHITE)
        x_pos = max(0, (ph_w - new_w) // 2)
        canvas.paste(photo, (x_pos, 0))
        photo = canvas
        photo = _fade_left(photo, fade_width=200)
        img.paste(photo, (photo_x, SPLIT_Y))
    else:
        # Branded gradient: dark green → light green → white
        import numpy as np
        zone_w = W - photo_x
        zone_h = FOOTER_Y - SPLIT_Y
        grad = np.zeros((zone_h, zone_w, 3), dtype=np.uint8)
        for x in range(zone_w):
            t = x / max(zone_w - 1, 1)
            r = int(GREEN[0] * (1 - t) + 255 * t)
            g = int(GREEN[1] * (1 - t) + 255 * t)
            b = int(GREEN[2] * (1 - t) + 255 * t)
            grad[:, x] = [r, g, b]
        img.paste(Image.fromarray(grad), (photo_x, SPLIT_Y))

    # Subtext — Anton Regular, left-aligned
    max_w = photo_x - PAD - 15
    max_h = FOOTER_Y - SPLIT_Y - 40
    font, lines = auto_fit(draw, subtext.upper(), font_anton, max_w, max_h, hi=90, lo=36)

    sample_h = draw.textbbox((0, 0), lines[0], font=font)[3]
    gap = int(sample_h * 0.06)
    y = SPLIT_Y + 30

    for line in lines:
        draw.text((PAD, y), line, fill=GREEN, font=font)
        y += sample_h + gap


def draw_footer(draw, url):
    """Dark green footer: APPLY NOW pill + URL."""
    draw.rectangle([0, FOOTER_Y, W, H], fill=GREEN)

    px, py, pw, ph = 35, FOOTER_Y + 30, 218, 68
    draw.rounded_rectangle([px, py, px + pw, py + ph], radius=34, fill=BLACK)

    af = font_bold(24)
    aw = draw.textlength("APPLY NOW", font=af)
    ah = draw.textbbox((0, 0), "APPLY NOW", font=af)[3]
    draw.text((px + (pw - aw) // 2, py + (ph - ah) // 2 - 1),
              "APPLY NOW", fill=WHITE, font=af)

    uf = font_regular(28)
    uh = draw.textbbox((0, 0), url, font=uf)[3]
    draw.text((px + pw + 24, py + (ph - uh) // 2 - 1), url, fill=WHITE, font=uf)


# ── Main generator ────────────────────────────────────────────────

def generate(headline, subtext, url, output_path, photo_path=None, rainbow=False):
    img = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    draw_banner(draw)
    draw_headline(draw, headline)
    draw_divider(draw)
    draw_split(draw, subtext, photo_path=photo_path, rainbow=rainbow)
    draw_footer(draw, url)

    img.save(output_path)
    print(f"Saved: {output_path}")
    return output_path


# ── Calendar helpers ──────────────────────────────────────────────

ICS_PATH = Path(__file__).parent / "ical_extracted" / \
    "Cucumber Marketing_c_a52165069117848d25ecde2e8b24ceaa1d3dfb00a5b0dd692e487478cbc6c6eb@group.calendar.google.com.ics"


def _parse_ics():
    """Return list of (start_date, summary, description) sorted by date."""
    events = []
    raw = ICS_PATH.read_text(encoding="utf-8", errors="ignore")

    blocks = re.split(r"BEGIN:VEVENT", raw)[1:]
    for block in blocks:
        summary = re.search(r"^SUMMARY:(.+)$", block, re.MULTILINE)
        dtstart = re.search(r"^DTSTART[^:]*:(\d{8})", block, re.MULTILINE)
        desc    = re.search(r"^DESCRIPTION:(.+?)(?=\n[A-Z])", block, re.MULTILINE | re.DOTALL)

        if not summary or not dtstart:
            continue

        raw_date = dtstart.group(1)
        try:
            ev_date = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
        except ValueError:
            continue

        desc_text = ""
        if desc:
            desc_text = desc.group(1).replace("\\n", " ").replace("\n ", "").strip()

        events.append((ev_date, summary.group(1).strip(), desc_text))

    events.sort(key=lambda x: x[0])
    return events


def _extract_tip(description):
    """Pull out the Content tip from a description string."""
    m = re.search(r"💡 Content tip:(.+?)(?:\.|$)", description)
    if m:
        return m.group(1).strip()
    # Fall back: return first sentence
    first = description.split(".")[0].strip()
    return first[:120] if first else ""


def list_upcoming():
    today = date.today()
    events = _parse_ics()
    upcoming = [(d, s, de) for d, s, de in events if d >= today][:20]
    print(f"\n{'#':<4} {'Date':<12} {'Event'}")
    print("-" * 70)
    for i, (d, s, de) in enumerate(upcoming):
        clean = re.sub(r"[^\x20-\x7E]", "", s).strip()
        print(f"{i+1:<4} {str(d):<12} {clean}")
    print()
    return upcoming


def generate_from_calendar(event_num):
    upcoming = list_upcoming()
    if event_num < 1 or event_num > len(upcoming):
        sys.exit(f"Event number must be 1–{len(upcoming)}")

    ev_date, summary, description = upcoming[event_num - 1]

    # Derive headline: strip emoji, clean up
    headline = re.sub(r"[^\x20-\x7E]", "", summary).strip()
    # Remove "POST:" prefixes
    headline = re.sub(r"^📱\s*POST:\s*", "", headline)
    headline = re.sub(r"^POST:\s*", "", headline, flags=re.IGNORECASE)
    # Truncate if very long
    if len(headline) > 50:
        headline = headline[:50].rsplit(" ", 1)[0]

    subtext = _extract_tip(description) or "CELEBRATE WITH CUCUMBER RECRUITMENT."

    clean_name = re.sub(r"[^\w\s-]", "", summary).strip().replace(" ", "_")[:40]
    output = Path(__file__).parent / f"graphic_{clean_name}.png"

    clean_summary = re.sub(r"[^\x20-\x7E]", "", summary).strip()
    print(f"\nGenerating graphic for: {clean_summary}")
    print(f"  Headline : {headline}")
    print(f"  Subtext  : {subtext}")
    generate(headline, subtext, "cucumber-recruitment.co.uk", str(output))


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cucumber Recruitment graphic generator")
    parser.add_argument("--headline", help="Large headline text")
    parser.add_argument("--subtext",  help="Supporting message text (left panel)")
    parser.add_argument("--url",      default="cucumber-recruitment.co.uk")
    parser.add_argument("--out",      default="graphic.png", help="Output PNG filename")
    parser.add_argument("--list",     action="store_true", help="List upcoming calendar events")
    parser.add_argument("--event",    type=int, metavar="N",
                        help="Generate graphic for event #N from --list")
    args = parser.parse_args()

    if args.list or args.event:
        if args.event:
            generate_from_calendar(args.event)
        else:
            list_upcoming()
    elif args.headline and args.subtext:
        out = args.out if args.out != "graphic.png" else "graphic.png"
        generate(args.headline, args.subtext, args.url, out)
    else:
        parser.print_help()
        print("\nExamples:")
        print('  python generate_graphic.py --list')
        print('  python generate_graphic.py --event 2')
        print('  python generate_graphic.py --headline "CARERS WEEK 2026" --subtext "THANK YOU TO EVERY UNPAID CARER." --out carers.png')
