#!/usr/bin/env python3
"""
Cucumber Recruitment — Social Media Graphic Generator
"""

import argparse
import os
import sys
import re
import threading
from collections import namedtuple
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── Brand colours ─────────────────────────────────────────────────
GREEN       = (22, 102, 31)
WHITE       = (255, 255, 255)
BLACK       = (0, 0, 0)
PLACEHOLDER = (210, 210, 210)

# ── Canvas formats ────────────────────────────────────────────────
CANVAS_SIZES = {
    'square':    (1080, 1080),
    'story':     (1080, 1920),
    'landscape': (1920, 1080),
    'a4':        (1240, 1754),
}
FORMAT_LABELS = {
    'square':    'Square (1:1)',
    'story':     'Story (9:16)',
    'landscape': 'Landscape (16:9)',
    'a4':        'A4 Flyer',
}

# ── Layout system ─────────────────────────────────────────────────
Layout = namedtuple('Layout', 'W H BANNER_H DIVIDER_Y DIVIDER_H SPLIT_Y FOOTER_Y PAD')

def _make_layout(w=1080, h=1080):
    banner_h  = int(h * 0.1435)
    divider_y = int(h * 0.4352)
    divider_h = 7
    footer_y  = int(h * 0.8796)
    pad       = int(w * 0.0463)
    return Layout(w, h, banner_h, divider_y, divider_h, divider_y + divider_h, footer_y, pad)

_thread_lyt = threading.local()

def _L():
    """Current thread's layout — defaults to 1080×1080 if not set."""
    if not hasattr(_thread_lyt, 'v'):
        _thread_lyt.v = _make_layout()
    return _thread_lyt.v


# ── Fonts ─────────────────────────────────────────────────────────
_FONT_DIR   = Path(r"C:\Windows\Fonts")
_MONTSERRAT = Path(__file__).parent / "Montserrat-ExtraBold.ttf"
_ANTON_PATH = Path(__file__).parent / "Anton-Regular.ttf"
_LOGO_PATH  = Path(__file__).parent / "cucumber_logo.png"

def _load(name, size):
    try:    return ImageFont.truetype(str(_FONT_DIR / name), size)
    except: return ImageFont.load_default()

def font_heavy(size):
    try:    return ImageFont.truetype(str(_MONTSERRAT), size)
    except: return _load("impact.ttf", size)

def font_bold(size):    return _load("arialbd.ttf", size)
def font_regular(size): return _load("arial.ttf",   size)

def font_anton(size):
    try:    return ImageFont.truetype(str(_ANTON_PATH), size)
    except: return font_heavy(size)


# ── Text helpers ──────────────────────────────────────────────────

def wrap(draw, text, font, max_px):
    words = text.split()
    lines, cur = [], []
    for word in words:
        trial = " ".join(cur + [word])
        if draw.textlength(trial, font=font) <= max_px or not cur:
            cur.append(word)
        else:
            lines.append(" ".join(cur)); cur = [word]
    if cur: lines.append(" ".join(cur))
    return lines

def block_height(draw, lines, font):
    if not lines: return 0
    h = draw.textbbox((0,0), lines[0], font=font)[3]
    return h * len(lines) + int(h * 0.08) * (len(lines) - 1)

def auto_fit(draw, text, font_fn, max_w, max_h, hi=130, lo=38, step=4):
    for size in range(hi, lo-1, -step):
        f = font_fn(size); lines = wrap(draw, text, f, max_w)
        if (block_height(draw, lines, f) <= max_h and
                all(draw.textlength(l, font=f) <= max_w for l in lines)):
            return f, lines
    f = font_fn(lo); return f, wrap(draw, text, f, max_w)


# ── Shared renderers ──────────────────────────────────────────────

def _paste_logo(img):
    if not _LOGO_PATH.exists(): return
    lyt  = _L()
    logo = Image.open(_LOGO_PATH).convert("RGBA")
    th   = lyt.BANNER_H - 24
    tw   = int(logo.width * th / logo.height)
    logo = logo.resize((tw, th), Image.LANCZOS)
    img.paste(logo, ((lyt.W - tw)//2, (lyt.BANNER_H - th)//2), logo)

def draw_banner(draw):
    lyt = _L()
    draw.rectangle([0, 0, lyt.W, lyt.BANNER_H], fill=GREEN)
    if _LOGO_PATH.exists():
        _paste_logo(draw._image)
    else:
        bw, bh = 310, 92
        bx = (lyt.W - bw)//2; by = (lyt.BANNER_H - bh)//2
        draw.rounded_rectangle([bx, by, bx+bw, by+bh], radius=14, fill=WHITE)
        draw.text((bx+56, by+17), "cucumber",    font=font_bold(30),    fill=GREEN)
        draw.text((bx+56, by+53), "recruitment", font=font_regular(17), fill=GREEN)

def draw_footer(draw, url):
    lyt = _L()
    draw.rectangle([0, lyt.FOOTER_Y, lyt.W, lyt.H], fill=GREEN)
    px, py = 35, lyt.FOOTER_Y + 30
    pw, ph = int(lyt.W * 0.202), 68
    draw.rounded_rectangle([px, py, px+pw, py+ph], radius=34, fill=BLACK)
    af = font_bold(24)
    aw = draw.textlength("APPLY NOW", font=af)
    ah = draw.textbbox((0,0), "APPLY NOW", font=af)[3]
    draw.text((px+(pw-aw)//2, py+(ph-ah)//2-1), "APPLY NOW", fill=WHITE, font=af)
    uf = font_regular(28)
    uh = draw.textbbox((0,0), url, font=uf)[3]
    draw.text((px+pw+24, py+(ph-uh)//2-1), url, fill=WHITE, font=uf)

def _remove_bg(photo_path):
    cache = Path(str(photo_path) + ".rmbg.png")
    if cache.exists():
        return Image.open(cache).convert("RGB")
    on_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT"))
    use_rembg  = os.environ.get("USE_REMBG","").lower() in ("1","true","yes")
    if not on_railway and use_rembg:
        try:
            import io
            from rembg import remove as rembg_remove
            with open(photo_path, "rb") as f: result = rembg_remove(f.read())
            rgba = Image.open(io.BytesIO(result)).convert("RGBA")
            bbox = rgba.getbbox()
            if bbox: rgba = rgba.crop(bbox)
            white = Image.new("RGBA", rgba.size, (255,255,255,255))
            rgb   = Image.alpha_composite(white, rgba).convert("RGB")
            rgb.save(cache); return rgb
        except Exception:
            pass
    img = Image.open(photo_path).convert("RGBA")
    if max(img.width, img.height) > 1024:
        ratio = 1024 / max(img.width, img.height)
        img   = img.resize((int(img.width*ratio), int(img.height*ratio)), Image.LANCZOS)
    import numpy as np
    arr = np.array(img).astype(int)
    r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]
    bg_mask = ((r > 235) & (g > 235) & (b > 235)) | ((r+g+b) > 720)
    try:
        from scipy.ndimage import binary_dilation
        dilated = binary_dilation(bg_mask, iterations=1)
    except ImportError:
        dilated = bg_mask.copy()
        dilated[1:]    |= bg_mask[:-1];  dilated[:-1]   |= bg_mask[1:]
        dilated[:,1:]  |= bg_mask[:,:-1]; dilated[:,:-1] |= bg_mask[:,1:]
    arr[dilated, 3] = 0
    rgba = Image.fromarray(arr.astype(np.uint8))
    bbox = rgba.getbbox()
    if bbox: rgba = rgba.crop(bbox)
    white = Image.new("RGBA", rgba.size, (255,255,255,255))
    rgb   = Image.alpha_composite(white, rgba).convert("RGB")
    rgb.save(cache); return rgb

def _fill_photo(photo_path, target_w, target_h, remove_bg=False):
    """Load photo, scale to fill target dimensions, centre-crop."""
    if not photo_path or not Path(photo_path).exists():
        return None
    try:
        photo = _remove_bg(photo_path) if remove_bg else Image.open(photo_path).convert("RGB")
        ratio = max(target_w / photo.width, target_h / photo.height)
        nw, nh = int(photo.width*ratio), int(photo.height*ratio)
        photo  = photo.resize((nw, nh), Image.LANCZOS)
        x_off  = (nw - target_w) // 2
        # For very narrow crops (tall image, thin target) bias harder toward top so faces stay in frame.
        # For near-square crops a small bias is enough.
        crop_ratio = (nh - target_h) / nh if nh > target_h else 0
        bias = 0.08 if crop_ratio < 0.3 else 0.05
        y_off  = int((nh - target_h) * bias)
        return photo.crop((x_off, y_off, x_off+target_w, y_off+target_h))
    except Exception:
        return None

def _blend_overlay(img, y, h, rgba):
    layer = Image.new("RGBA", (img.width, h), rgba)
    base  = img.convert("RGBA")
    base.paste(layer, (0, y), layer)
    return base.convert("RGB")


# ── Template 1: Circle Crop ───────────────────────────────────────

def _tpl_circle(headline, subtext, url, output_path, photo_path):
    lyt  = _L()
    img  = Image.new("RGB", (lyt.W, lyt.H), WHITE)
    draw = ImageDraw.Draw(img)
    draw_banner(draw)

    CONTENT_Y = lyt.BANNER_H
    CONTENT_H = lyt.FOOTER_Y - lyt.BANNER_H
    CX = int(lyt.W * 0.78)
    CY = CONTENT_Y + int(CONTENT_H * 0.55)
    R  = int(min(lyt.W, lyt.H) * 0.259)

    draw.ellipse([CX-R-12, CY-R-12, CX+R+12, CY+R+12], fill=GREEN)

    if photo_path and Path(photo_path).exists():
        D     = R * 2
        photo = _fill_photo(photo_path, D, D)
        if photo:
            mask = Image.new("L", (D, D), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, D, D], fill=255)
            img.paste(photo, (CX-R, CY-R), mask)

    text_w = max(100, CX - R - lyt.PAD - 60)
    font_h, lines_h = auto_fit(draw, headline.upper(), font_heavy, text_w,
                                int(CONTENT_H * 0.55), hi=120, lo=38)
    sh      = draw.textbbox((0,0), lines_h[0], font=font_h)[3]
    total_h = block_height(draw, lines_h, font_h)
    y = CONTENT_Y + (CONTENT_H - total_h)//2 - 30
    for line in lines_h:
        draw.text((lyt.PAD, y), line, fill=GREEN, font=font_h); y += sh + int(sh*0.06)

    y += 20
    draw.rectangle([lyt.PAD, y, lyt.PAD+text_w, y+6], fill=GREEN); y += 22

    font_s, lines_s = auto_fit(draw, subtext.upper(), font_anton, text_w,
                                lyt.FOOTER_Y - y - 20, hi=66, lo=26)
    ss = draw.textbbox((0,0), lines_s[0], font=font_s)[3]
    for line in lines_s:
        draw.text((lyt.PAD, y), line, fill=GREEN, font=font_s); y += ss + int(ss*0.06)

    draw_footer(draw, url)
    img.save(output_path)
    return output_path


# ── Template 2: Full Bleed ────────────────────────────────────────

def _tpl_fullbleed(headline, subtext, url, output_path, photo_path):
    lyt       = _L()
    photo_h   = lyt.H - lyt.BANNER_H
    photo     = _fill_photo(photo_path, lyt.W, photo_h)
    img       = Image.new("RGB", (lyt.W, lyt.H), GREEN)
    if photo:
        img.paste(photo, (0, lyt.BANNER_H))   # photo starts below banner — faces never hidden

    _paste_logo(img)

    # Headline band sits just below the banner (top of photo = usually sky/background)
    HL_Y = lyt.BANNER_H
    HL_H = int(lyt.H * 0.22)
    img  = _blend_overlay(img, HL_Y, HL_H, (0, 0, 0, 160))
    draw = ImageDraw.Draw(img)

    max_w  = lyt.W - lyt.PAD * 2
    font_h, lines_h = auto_fit(draw, headline.upper(), font_heavy, max_w, HL_H-20, hi=130, lo=46)
    sh      = draw.textbbox((0,0), lines_h[0], font=font_h)[3]
    total_h = block_height(draw, lines_h, font_h)
    y = HL_Y + (HL_H - total_h)//2
    for line in lines_h:
        draw.text((lyt.PAD, y), line, fill=WHITE, font=font_h); y += sh + int(sh*0.04)

    # Subtext band sits just above the footer
    SUB_Y = lyt.FOOTER_Y - int(lyt.H * 0.20)
    img   = _blend_overlay(img, SUB_Y, lyt.FOOTER_Y - SUB_Y, (*GREEN, 220))
    draw  = ImageDraw.Draw(img)

    font_s, lines_s = auto_fit(draw, subtext.upper(), font_anton, max_w,
                                lyt.FOOTER_Y - SUB_Y - 20, hi=72, lo=26)
    ss      = draw.textbbox((0,0), lines_s[0], font=font_s)[3]
    total_s = block_height(draw, lines_s, font_s)
    y_s     = SUB_Y + (lyt.FOOTER_Y - SUB_Y - total_s)//2
    for line in lines_s:
        draw.text((lyt.PAD, y_s), line, fill=WHITE, font=font_s); y_s += ss + int(ss*0.06)

    draw_footer(draw, url)
    img.save(output_path)
    return output_path


# ── Template 3: Stacked Bands ─────────────────────────────────────

def _tpl_stacked(headline, subtext, url, output_path, photo_path):
    lyt  = _L()
    img  = Image.new("RGB", (lyt.W, lyt.H), WHITE)
    draw = ImageDraw.Draw(img)

    BAND2_H = int(lyt.H * 0.222)
    BAND3_Y = lyt.BANNER_H + BAND2_H
    BAND3_H = int(lyt.H * 0.306)
    BAND4_Y = BAND3_Y + BAND3_H
    BAND4_H = lyt.FOOTER_Y - BAND4_Y

    draw.rectangle([0, 0, lyt.W, lyt.BANNER_H], fill=GREEN)
    _paste_logo(img); draw = ImageDraw.Draw(img)

    max_w  = lyt.W - lyt.PAD * 2
    font_h, lines_h = auto_fit(draw, headline.upper(), font_heavy, max_w, BAND2_H-30, hi=130, lo=48)
    sh      = draw.textbbox((0,0), lines_h[0], font=font_h)[3]
    total_h = block_height(draw, lines_h, font_h)
    y = lyt.BANNER_H + (BAND2_H - total_h)//2
    for line in lines_h:
        draw.text((lyt.PAD, y), line, fill=GREEN, font=font_h); y += sh + int(sh*0.04)

    photo = _fill_photo(photo_path, lyt.W, BAND3_H)
    if photo:
        img.paste(photo, (0, BAND3_Y))

    draw.rectangle([0, BAND4_Y, lyt.W, lyt.FOOTER_Y], fill=GREEN)
    font_s, lines_s = auto_fit(draw, subtext.upper(), font_anton, max_w, BAND4_H-20, hi=80, lo=28)
    ss      = draw.textbbox((0,0), lines_s[0], font=font_s)[3]
    total_s = block_height(draw, lines_s, font_s)
    y_s     = BAND4_Y + (BAND4_H - total_s)//2
    for line in lines_s:
        draw.text((lyt.PAD, y_s), line, fill=WHITE, font=font_s); y_s += ss + int(ss*0.06)

    draw_footer(draw, url)
    img.save(output_path)
    return output_path


# ── Template 4: Bold Panel ────────────────────────────────────────

def _tpl_boldpanel(headline, subtext, url, output_path, photo_path):
    lyt  = _L()
    img  = Image.new("RGB", (lyt.W, lyt.H), WHITE)
    draw = ImageDraw.Draw(img)

    SPLIT_X = lyt.W // 2
    ZONE_H  = lyt.FOOTER_Y - lyt.BANNER_H

    if photo_path and Path(photo_path).exists():
        try:    photo = _remove_bg(photo_path)
        except: photo = Image.open(photo_path).convert("RGB")
        ph     = lyt.H - lyt.BANNER_H
        nw     = int(photo.width * ph / photo.height)
        photo  = photo.resize((nw, ph), Image.LANCZOS)
        canvas = Image.new("RGB", (lyt.W - SPLIT_X, ph), WHITE)
        canvas.paste(photo, (max(0, (lyt.W - SPLIT_X - nw)//2), 0))
        img.paste(canvas, (SPLIT_X, lyt.BANNER_H))

    draw.rectangle([0, 0, SPLIT_X, lyt.H], fill=GREEN)
    draw.rectangle([0, 0, lyt.W, lyt.BANNER_H], fill=GREEN)
    _paste_logo(img); draw = ImageDraw.Draw(img)

    text_w = SPLIT_X - lyt.PAD * 2
    font_h, lines_h = auto_fit(draw, headline.upper(), font_heavy, text_w,
                                int(ZONE_H * 0.55), hi=115, lo=38)
    sh      = draw.textbbox((0,0), lines_h[0], font=font_h)[3]
    total_h = block_height(draw, lines_h, font_h)
    y = lyt.BANNER_H + (int(ZONE_H * 0.48) - total_h)//2
    for line in lines_h:
        draw.text((lyt.PAD, y), line, fill=WHITE, font=font_h); y += sh + int(sh*0.06)

    y += 20
    draw.rectangle([lyt.PAD, y, lyt.PAD+text_w, y+4], fill=WHITE); y += 22

    font_s, lines_s = auto_fit(draw, subtext.upper(), font_anton, text_w,
                                lyt.FOOTER_Y - y - 20, hi=72, lo=26)
    ss = draw.textbbox((0,0), lines_s[0], font=font_s)[3]
    for line in lines_s:
        draw.text((lyt.PAD, y), line, fill=WHITE, font=font_s); y += ss + int(ss*0.06)

    draw_footer(draw, url)
    img.save(output_path)
    return output_path


# ── Template 5: Framed ────────────────────────────────────────────

def _tpl_framed(headline, subtext, url, output_path, photo_path):
    lyt   = _L()
    FRAME = int(lyt.W * 0.037)
    img   = Image.new("RGB", (lyt.W, lyt.H), GREEN)
    draw  = ImageDraw.Draw(img)

    iw = lyt.W - FRAME*2; ih = lyt.H - FRAME*2
    ix, iy = FRAME, FRAME

    draw.rectangle([ix, iy, ix+iw, iy+ih], fill=WHITE)

    LOGO_H = int(lyt.H * 0.102)
    draw.rectangle([ix, iy, ix+iw, iy+LOGO_H], fill=GREEN)
    if _LOGO_PATH.exists():
        logo = Image.open(_LOGO_PATH).convert("RGBA")
        th   = LOGO_H - 16; tw = int(logo.width * th / logo.height)
        logo = logo.resize((tw, th), Image.LANCZOS)
        img.paste(logo, ((lyt.W - tw)//2, iy + 8), logo)
        draw = ImageDraw.Draw(img)

    PHOTO_Y = iy + LOGO_H
    PHOTO_H = int(lyt.H * 0.407)
    photo   = _fill_photo(photo_path, iw, PHOTO_H)
    if photo:
        img.paste(photo, (ix, PHOTO_Y))

    DIV_Y = PHOTO_Y + PHOTO_H
    draw.rectangle([ix, DIV_Y, ix+iw, DIV_Y + lyt.DIVIDER_H], fill=GREEN)

    HL_Y      = DIV_Y + lyt.DIVIDER_H + 20
    SUBTEXT_Y = iy + ih - int(lyt.H * 0.130)
    text_w    = iw - lyt.PAD

    font_h, lines_h = auto_fit(draw, headline.upper(), font_heavy, text_w,
                                SUBTEXT_Y - HL_Y - 10, hi=120, lo=42)
    sh      = draw.textbbox((0,0), lines_h[0], font=font_h)[3]
    total_h = block_height(draw, lines_h, font_h)
    y = HL_Y + (SUBTEXT_Y - HL_Y - total_h)//2
    for line in lines_h:
        draw.text((ix + lyt.PAD//2, y), line, fill=GREEN, font=font_h); y += sh + int(sh*0.04)

    pill_w, pill_h = int(lyt.W * 0.185), 54
    pill_x = ix + iw - pill_w - 10
    pill_y = iy + ih - pill_h - 14
    draw.rounded_rectangle([pill_x, pill_y, pill_x+pill_w, pill_y+pill_h], radius=27, fill=GREEN)
    af = font_bold(20)
    aw = draw.textlength("APPLY NOW", font=af)
    ah = draw.textbbox((0,0), "APPLY NOW", font=af)[3]
    draw.text((pill_x+(pill_w-aw)//2, pill_y+(pill_h-ah)//2), "APPLY NOW", fill=WHITE, font=af)

    sub_w = pill_x - ix - lyt.PAD//2 - 20
    font_s, lines_s = auto_fit(draw, subtext.upper(), font_anton, sub_w,
                                int(lyt.H * 0.102), hi=60, lo=24)
    ss      = draw.textbbox((0,0), lines_s[0], font=font_s)[3]
    total_s = block_height(draw, lines_s, font_s)
    y_s     = SUBTEXT_Y + (int(lyt.H * 0.130) - total_s)//2
    for line in lines_s:
        draw.text((ix + lyt.PAD//2, y_s), line, fill=GREEN, font=font_s); y_s += ss + int(ss*0.06)

    img.save(output_path)
    return output_path


# ── Main generator ────────────────────────────────────────────────

def generate(headline, subtext, url, output_path, photo_path=None, rainbow=False,
             template=1, size='square'):
    w, h = CANVAS_SIZES.get(size, (1080, 1080)) if isinstance(size, str) else size
    _thread_lyt.v = _make_layout(w, h)

    if template == 2:
        return _tpl_fullbleed(headline, subtext, url, output_path, photo_path)
    if template == 3:
        return _tpl_stacked(headline, subtext, url, output_path, photo_path)
    if template == 4:
        return _tpl_boldpanel(headline, subtext, url, output_path, photo_path)
    if template == 5:
        return _tpl_framed(headline, subtext, url, output_path, photo_path)
    return _tpl_circle(headline, subtext, url, output_path, photo_path)


# ── Calendar helpers ──────────────────────────────────────────────

ICS_PATH = Path(__file__).parent / "ical_extracted" / \
    "Cucumber Marketing_c_a52165069117848d25ecde2e8b24ceaa1d3dfb00a5b0dd692e487478cbc6c6eb@group.calendar.google.com.ics"


def _parse_ics():
    events = []
    raw    = ICS_PATH.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"BEGIN:VEVENT", raw)[1:]
    for block in blocks:
        summary = re.search(r"^SUMMARY:(.+)$",        block, re.MULTILINE)
        dtstart = re.search(r"^DTSTART[^:]*:(\d{8})", block, re.MULTILINE)
        desc    = re.search(r"^DESCRIPTION:(.+?)(?=\n[A-Z])", block, re.MULTILINE | re.DOTALL)
        if not summary or not dtstart: continue
        raw_date = dtstart.group(1)
        try:
            ev_date = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
        except ValueError:
            continue
        desc_text = ""
        if desc:
            desc_text = desc.group(1).replace("\\n"," ").replace("\n ","").strip()
        events.append((ev_date, summary.group(1).strip(), desc_text))
    events.sort(key=lambda x: x[0])
    return events


def _extract_tip(description):
    m = re.search(r"💡 Content tip:(.+?)(?:\.|$)", description)
    if m: return m.group(1).strip()
    first = description.split(".")[0].strip()
    return first[:120] if first else ""


def list_upcoming():
    today    = date.today()
    events   = _parse_ics()
    upcoming = [(d,s,de) for d,s,de in events if d >= today][:20]
    print(f"\n{'#':<4} {'Date':<12} {'Event'}")
    print("-" * 70)
    for i, (d,s,de) in enumerate(upcoming):
        clean = re.sub(r"[^\x20-\x7E]","",s).strip()
        print(f"{i+1:<4} {str(d):<12} {clean}")
    print()
    return upcoming


def generate_from_calendar(event_num):
    upcoming = list_upcoming()
    if event_num < 1 or event_num > len(upcoming):
        sys.exit(f"Event number must be 1–{len(upcoming)}")
    ev_date, summary, description = upcoming[event_num - 1]
    headline = re.sub(r"[^\x20-\x7E]","",summary).strip()
    headline = re.sub(r"^📱\s*POST:\s*","",headline)
    headline = re.sub(r"^POST:\s*","",headline,flags=re.IGNORECASE)
    if len(headline) > 50:
        headline = headline[:50].rsplit(" ",1)[0]
    subtext  = _extract_tip(description) or "CELEBRATE WITH CUCUMBER RECRUITMENT."
    clean    = re.sub(r"[^\w\s-]","",summary).strip().replace(" ","_")[:40]
    output   = Path(__file__).parent / f"graphic_{clean}.png"
    clean_summary = re.sub(r"[^\x20-\x7E]", "", summary).strip()
    print(f"\nGenerating graphic for: {clean_summary}")
    print(f"  Headline : {headline}")
    print(f"  Subtext  : {subtext}")
    generate(headline, subtext, "cucumber-recruitment.co.uk", str(output))


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cucumber Recruitment graphic generator")
    parser.add_argument("--headline", nargs='*')
    parser.add_argument("--subtext",  nargs='*')
    parser.add_argument("--url",      default="cucumber-recruitment.co.uk")
    parser.add_argument("--out",      default="graphic.png")
    parser.add_argument("--size",     default="square", choices=list(CANVAS_SIZES))
    parser.add_argument("--template", type=int, default=1, choices=[1,2,3,4,5])
    parser.add_argument("--list",     action="store_true")
    parser.add_argument("--event",    type=int, metavar="N")
    args = parser.parse_args()

    if args.list or args.event:
        if args.event: generate_from_calendar(args.event)
        else:          list_upcoming()
    elif args.headline and args.subtext:
        generate(" ".join(args.headline), " ".join(args.subtext),
                 args.url, args.out, template=args.template, size=args.size)
    else:
        parser.print_help()
