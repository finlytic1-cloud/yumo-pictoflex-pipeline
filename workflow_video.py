"""
Renders a vertical (1080x1920) "satisfying workflow" Reel for Pictoflex Labs.

Scene plan
  1. Hook line at the top. The trigger node pops in.
  2. The workflow builds itself row by row: curved connectors draw in, nodes
     pop with a ripple, branches fire in parallel, and streams of light keep
     flowing along every finished connection. The camera glides down with it.
  3. A money counter at the bottom ticks up each time an outcome node fires.
  4. The camera pulls back to reveal the whole system glowing at once.
  5. Phone-style notifications stack in: the results for the business owner.
  6. Pictoflex Labs end card.

Pure Python (Pillow + numpy) piped into ffmpeg: runs on Render, no browser,
no paid image service.
"""
import math
import os
import subprocess
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
# Everything is drawn at K x resolution, then shrunk with a high-quality
# filter. Pillow doesn't anti-alias shapes, so this is what makes edges and
# small text crisp instead of jagged.
K = int(os.environ.get("SUPERSAMPLE", "2"))  # 3 is sharper but needs ~650MB RAM; Render cron has 512MB
RW, RH = W * K, H * K
FONT_DIR = os.environ.get("FONT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"))

BG = (7, 7, 13)
INK = (242, 242, 250)
MUTED = (135, 135, 165)
PURPLE = (150, 100, 255)
PURPLE_DIM = (52, 40, 100)
GREEN = (60, 225, 150)
GOLD = (255, 196, 70)
CARD = (17, 17, 29)
CARD_LINE = (42, 42, 66)

KIND_COLOUR = {"trigger": PURPLE, "ai": (120, 170, 255), "action": PURPLE, "outcome": GREEN}

# ---------------------------------------------------------------- utils


@lru_cache(maxsize=256)
def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), max(6, int(size * K)))


class SDraw:
    """ImageDraw that takes 1080x1920 coordinates and draws on the K x canvas."""

    def __init__(self, img, scale=None):
        self.d = ImageDraw.Draw(img)
        self.k = K if scale is None else scale

    def _xy(self, xy):
        k = self.k
        if isinstance(xy[0], (tuple, list)):
            return [(x * k, y * k) for x, y in xy]
        return [v * k for v in xy]

    def line(self, xy, fill=None, width=1, joint=None):
        self.d.line(self._xy(xy), fill=fill, width=max(1, int(width * self.k)), joint=joint)

    def ellipse(self, xy, fill=None, outline=None, width=1):
        self.d.ellipse(self._xy(xy), fill=fill, outline=outline, width=max(1, int(width * self.k)))

    def rounded_rectangle(self, xy, radius=0, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self._xy(xy), radius=radius * self.k, fill=fill, outline=outline,
                                 width=max(1, int(width * self.k)))

    def polygon(self, xy, fill=None):
        self.d.polygon(self._xy(xy), fill=fill)

    def text(self, xy, text, font=None, fill=None):
        self.d.text((xy[0] * self.k, xy[1] * self.k), text, font=font, fill=fill)

    def textlength(self, text, font=None):
        return self.d.textlength(text, font=font) / K


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = clamp(t)
    return t * t * (3 - 2 * t)


def ease_back(t):
    t = clamp(t)
    c1, c3 = 1.9, 2.9
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def mix(c1, c2, t):
    t = clamp(t)
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def rgba(c, a):
    return (*c[:3], int(255 * clamp(a)))


def lerp(a, b, t):
    return a + (b - a) * t


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=fnt) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def bezier(p0, p3, n=48):
    (x0, y0), (x3, y3) = p0, p3
    dy = (y3 - y0) * 0.55
    p1, p2 = (x0, y0 + dy), (x3, y3 - dy)
    pts = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        pts.append((a * x0 + b * p1[0] + c * p2[0] + d * x3, a * y0 + b * p1[1] + c * p2[1] + d * y3))
    return pts


def point_on(pts, f):
    f = clamp(f) * (len(pts) - 1)
    i = int(f)
    if i >= len(pts) - 1:
        return pts[-1]
    r = f - i
    return (lerp(pts[i][0], pts[i + 1][0], r), lerp(pts[i][1], pts[i + 1][1], r))


# ---------------------------------------------------------------- glyphs
def glyph(d, kind, cx, cy, r, col):
    if kind == "trigger":  # lightning bolt
        p = [(0.15, -0.95), (-0.55, 0.12), (-0.05, 0.12), (-0.2, 0.95), (0.55, -0.15), (0.05, -0.15)]
    elif kind == "ai":  # four-point sparkle
        p = []
        for i in range(8):
            ang = -math.pi / 2 + i * math.pi / 4
            rr = 0.95 if i % 2 == 0 else 0.28
            p.append((math.cos(ang) * rr, math.sin(ang) * rr))
    elif kind == "outcome":  # star
        p = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rr = 0.95 if i % 2 == 0 else 0.42
            p.append((math.cos(ang) * rr, math.sin(ang) * rr))
    else:  # action: play arrow
        p = [(-0.45, -0.7), (0.7, 0.0), (-0.45, 0.7)]
    d.polygon([(cx + x * r, cy + y * r) for x, y in p], fill=col)


# ---------------------------------------------------------------- layout
ROW_Y0, ROW_GAP = 560, 270
COLS = {1: ([540], 600), 2: ([300, 780], 430), 3: ([185, 540, 895], 310)}
NODE_H = 128


def layout(spec):
    rows = {}
    for n in spec["nodes"]:
        rows.setdefault(n["row"], []).append(n)
    pos = {}
    for r, items in rows.items():
        xs, w = COLS[len(items)]
        for n, x in zip(items, xs):
            pos[n["id"]] = (x, ROW_Y0 + r * ROW_GAP, w)
    n_rows = max(rows) + 1
    world_h = ROW_Y0 + (n_rows - 1) * ROW_GAP + NODE_H + 200
    return pos, n_rows, world_h


# ---------------------------------------------------------------- timeline
class TL:
    def __init__(self, n_rows):
        self.first = 1.1           # trigger pops in
        self.step = 0.95           # seconds per row
        self.build_end = self.first + (n_rows - 1) * self.step + 0.8
        self.zoom_start = self.build_end
        self.zoom_len = 1.6
        self.notif_start = self.zoom_start + self.zoom_len + 0.6
        self.notif_len = 3.6
        self.end_start = self.notif_start + self.notif_len
        self.total = self.end_start + 2.4

    def row_t(self, r):
        return self.first + r * self.step


# ---------------------------------------------------------------- static layers
def make_background():
    img = Image.new("RGB", (RW, RH), BG)
    d = SDraw(img)
    for y in range(0, H, 44):
        for x in range(0, W, 44):
            d.ellipse((x - 1.4, y - 1.4, x + 1.4, y + 1.4), fill=(24, 24, 38))
    glow = Image.new("RGB", (W // 8, H // 8), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((W / 8 * 0.05, H / 8 * 0.25, W / 8 * 0.95, H / 8 * 0.8), fill=(34, 18, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(16)).resize((RW, RH), Image.BILINEAR)
    arr = np.asarray(img, dtype=np.int16) + np.asarray(glow, dtype=np.int16)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")


def make_fades():
    # Only the strips that fade are stored (not full frames), to save memory.
    th, bh = 460 * K, 330 * K
    top = np.zeros((th, RW, 4), dtype=np.uint8)
    top[..., :3] = BG
    ys = np.arange(th) / K
    top[..., 3] = (np.clip(1 - (ys - 300) / 160, 0, 1)[:, None] * 255).astype(np.uint8)  # solid to 300px
    bot = np.zeros((bh, RW, 4), dtype=np.uint8)
    bot[..., :3] = BG
    yb = np.arange(bh) / K
    bot[..., 3] = (np.clip(yb / 150, 0, 1)[:, None] * 255).astype(np.uint8)
    return Image.fromarray(top), Image.fromarray(bot)


# ---------------------------------------------------------------- camera
def camera(tl, n_rows, world_h, t):
    """Returns (cx, cy, scale): world point shown at the screen centre, and zoom."""
    follow_row = clamp((t - tl.first) / tl.step, 0, n_rows - 1)
    row_y = ROW_Y0 + follow_row * ROW_GAP
    cy_follow = max(H / 2, row_y - 170)             # keep the active row just below the middle
    z = ease_in_out((t - tl.zoom_start) / tl.zoom_len)
    fit = min(1.0, (H - 520) / (world_h - ROW_Y0 + 120))
    cy_fit = (ROW_Y0 - 60 + world_h) / 2
    return 540, lerp(cy_follow, cy_fit + 90 / max(fit, 0.01) * 0, z), lerp(1.0, fit, z)


def to_screen(cam, x, y):
    cx, cy, s = cam
    return ((x - cx) * s + W / 2, (y - cy) * s + H / 2 + (40 if s < 1 else 0))


# ---------------------------------------------------------------- the workflow
def draw_workflow(frame, spec, pos, tl, cam, t):
    s = cam[2]
    crisp = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    # The glow is blurred anyway, so it's drawn at half resolution to save memory.
    glow = Image.new("RGBA", (W // 2, H // 2), (0, 0, 0, 0))
    d, g = SDraw(crisp), SDraw(glow, scale=0.5)
    rows = {n["id"]: n["row"] for n in spec["nodes"]}
    kinds = {n["id"]: n.get("kind", "action") for n in spec["nodes"]}
    overview = ease_in_out((t - tl.zoom_start) / tl.zoom_len)

    # connectors + flowing light
    for a, b in spec["edges"]:
        ax, ay, _ = pos[a]
        bx, by, _ = pos[b]
        p0 = to_screen(cam, ax, ay + NODE_H / 2)
        p3 = to_screen(cam, bx, by - NODE_H / 2)
        pts = bezier(p0, p3)
        start = tl.row_t(rows[a]) + 0.35
        end = tl.row_t(rows[b]) - 0.05
        grow = ease_in_out((t - start) / max(0.1, end - start))
        if grow <= 0:
            continue
        col = KIND_COLOUR[kinds[b]]
        cut = max(2, int(grow * (len(pts) - 1)) + 1)
        seg = pts[:cut]
        lw = max(2, int(4 * s))
        d.line(seg, fill=rgba(mix(CARD_LINE, col, 0.75), 1), width=lw, joint="curve")
        g.line(seg, fill=rgba(col, 0.9), width=max(4, int(12 * s)), joint="curve")
        if grow < 1:  # bright head while drawing
            hx, hy = seg[-1]
            g.ellipse((hx - 22 * s, hy - 22 * s, hx + 22 * s, hy + 22 * s), fill=rgba(col, 1))
            d.ellipse((hx - 7 * s, hy - 7 * s, hx + 7 * s, hy + 7 * s), fill=rgba(INK, 1))
        else:      # continuous stream of particles once connected
            k = 4 + int(4 * overview)
            for j in range(k):
                f = ((t - end) * 0.55 + j / k) % 1.0
                px, py = point_on(pts, f)
                rr = (5 + 3 * math.sin(f * math.pi)) * s
                g.ellipse((px - rr * 2.4, py - rr * 2.4, px + rr * 2.4, py + rr * 2.4), fill=rgba(col, 0.9))
                d.ellipse((px - rr, py - rr, px + rr, py + rr), fill=rgba(INK, 0.95))

    # nodes
    pulse_all = clamp(1 - abs(t - (tl.zoom_start + tl.zoom_len + 0.15)) / 0.45)
    for n in spec["nodes"]:
        x, y, w = pos[n["id"]]
        t0 = tl.row_t(n["row"])
        appear = ease_back((t - t0 + 0.12) / 0.45)
        if appear <= 0:
            continue
        lit = ease_out((t - t0) / 0.35)
        col = KIND_COLOUR[n.get("kind", "action")]
        sc = s * (0.82 + 0.18 * appear)
        cx, cy = to_screen(cam, x, y)
        hw, hh = w / 2 * sc, NODE_H / 2 * sc
        a = clamp(appear)
        box = (cx - hw, cy - hh, cx + hw, cy + hh)
        rad = 30 * sc
        d.rounded_rectangle(box, radius=rad, fill=rgba(CARD, a), outline=rgba(mix(CARD_LINE, col, lit), a),
                            width=max(2, int(3 * s)))
        g.rounded_rectangle(box, radius=rad, outline=rgba(col, lit * (0.55 + 0.45 * pulse_all)),
                            width=max(4, int(16 * s)))
        # activation ripple
        rp = (t - t0) / 0.7
        if 0 < rp < 1:
            grow = 26 * ease_out(rp) * s
            d.rounded_rectangle((box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow),
                                radius=rad + grow, outline=rgba(col, (1 - rp) * 0.8), width=max(2, int(3 * s)))
        # icon bubble
        r = 36 * sc
        bx, by = box[0] + 28 * sc + r, cy
        d.ellipse((bx - r, by - r, bx + r, by + r), fill=rgba(mix(PURPLE_DIM, col, lit), a))
        glyph(d, n.get("kind", "action"), bx, by, r * 0.55, rgba(BG if lit > 0.5 else INK, a))
        # text
        tx = bx + r + 22 * sc
        max_w = box[2] - tx - 20 * sc
        fl = font("Inter-SemiBold.otf", (40 if w >= 500 else 33 if w >= 400 else 29) * sc)
        fs = font("Inter-Regular.otf", (28 if w >= 500 else 24 if w >= 400 else 22) * sc)
        label = n["label"]
        lines = wrap(d, label, fl, max_w)[:2]
        sub = n.get("sub", "")
        line_h = fl.size / K * 1.15
        block = line_h * len(lines) + (fs.size / K * 1.25 if sub else 0)
        ty = cy - block / 2
        for i, ln in enumerate(lines):
            d.text((tx, ty + i * line_h), ln, font=fl, fill=rgba(mix(MUTED, INK, lit), a))
        if sub:
            d.text((tx, ty + line_h * len(lines) + 2 * sc), wrap(d, sub, fs, max_w)[0], font=fs, fill=rgba(MUTED, a))
        # value badge on outcomes
        if n.get("value") and lit > 0:
            badge = f"+£{n['value']}"
            fb = font("Inter-Bold.otf", 26 * sc)
            bw = d.textlength(badge, font=fb) + 26 * sc
            bh = 40 * sc
            bx1, by1 = box[2] - bw * 0.6, box[1] - bh * 0.55 - (1 - ease_back((t - t0 - 0.2) / 0.4)) * 20 * s
            ba = clamp((t - t0 - 0.2) / 0.25) * a
            d.rounded_rectangle((bx1, by1, bx1 + bw, by1 + bh), radius=bh / 2, fill=rgba(GOLD, ba))
            d.text((bx1 + 13 * sc, by1 + 6 * sc), badge, font=fb, fill=rgba(BG, ba))

    small = glow.resize((W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(5))
    glow = small.resize((RW, RH), Image.BILINEAR)
    frame.alpha_composite(glow)
    frame.alpha_composite(glow)
    frame.alpha_composite(crisp)


# ---------------------------------------------------------------- HUD
def draw_hud(frame, spec, tl, t, fades):
    top, bot = fades
    frame.alpha_composite(top, dest=(0, 0))
    frame.alpha_composite(bot, dest=(0, RH - bot.height))
    d = SDraw(frame)
    fh = font("InterDisplay-ExtraBold.otf", 74)

    def headline(text, alpha, y0=128):
        for i, ln in enumerate(wrap(d, text, fh, W - 150)):
            tw = d.textlength(ln, font=fh)
            d.text(((W - tw) / 2, y0 + i * 86), ln, font=fh, fill=rgba(INK, alpha))

    swap = tl.zoom_start + 0.2
    a1 = ease_out(t / 0.6) * (1 - ease_out((t - swap) / 0.35))
    a2 = ease_out((t - swap - 0.25) / 0.4) * (1 - ease_out((t - tl.notif_start + 0.3) / 0.3))
    if a1 > 0:
        headline(spec["hook"], a1, 128 + (1 - ease_out(t / 0.6)) * 30)
    if a2 > 0:
        headline(spec.get("overview_line", "One system. Running 24/7."), a2)

    # small label so nobody mistakes the scenario for a real client's numbers
    tag_a = ease_out(t / 0.6) * (1 - ease_out((t - tl.notif_start + 0.3) / 0.3))
    if tag_a > 0:
        ft = font("Inter-Medium.otf", 24)
        tag = spec.get("tag", "Example automation")
        tw = d.textlength(tag, font=ft)
        d.text(((W - tw) / 2, 92), tag.upper(), font=ft, fill=rgba(PURPLE, tag_a * 0.9))

    # money counter: ticks up as each outcome fires
    total = 0.0
    for n in spec["nodes"]:
        if n.get("value"):
            total += n["value"] * ease_out((t - tl.row_t(n["row"]) - 0.1) / 0.6)
    ca = ease_out((t - tl.first) / 0.5) * (1 - ease_out((t - tl.notif_start + 0.3) / 0.3))
    if ca > 0 and spec.get("counter_label"):
        fl = font("Inter-Medium.otf", 32)
        fv = font("InterDisplay-ExtraBold.otf", 92)
        lab = spec["counter_label"]
        lw = d.textlength(lab, font=fl)
        d.text(((W - lw) / 2, H - 250), lab, font=fl, fill=rgba(MUTED, ca))
        val = f"£{int(round(total)):,}"
        vw = d.textlength(val, font=fv)
        d.text(((W - vw) / 2, H - 205), val, font=fv, fill=rgba(GOLD, ca))


# ---------------------------------------------------------------- notifications
def draw_notifications(frame, spec, tl, t):
    local = t - tl.notif_start
    out = ease_out((t - (tl.end_start - 0.35)) / 0.35)
    dim = ease_out(local / 0.5) * 0.72
    frame.alpha_composite(Image.new("RGBA", (RW, RH), rgba(BG, dim)))
    d = SDraw(frame)
    ft = font("InterDisplay-ExtraBold.otf", 66)
    title = spec.get("results_title", "Meanwhile, on your phone…")
    ta = ease_out(local / 0.4) * (1 - out)
    for i, ln in enumerate(wrap(d, title, ft, W - 160)):
        tw = d.textlength(ln, font=ft)
        d.text(((W - tw) / 2, 300 + i * 78), ln, font=ft, fill=rgba(INK, ta))
    y = 520
    f1, f2, f3 = font("Inter-SemiBold.otf", 34), font("Inter-Regular.otf", 29), font("Inter-Medium.otf", 24)
    for i, nt in enumerate(spec["notifications"]):
        p = (local - 0.35 - i * 0.45) / 0.5
        if p <= 0:
            continue
        a = clamp(p * 2) * (1 - out)
        dy = (1 - ease_back(p)) * -70
        x0, x1 = 80, W - 80
        h = 150
        y0 = y + dy
        d.rounded_rectangle((x0, y0, x1, y0 + h), radius=36, fill=rgba((30, 30, 46), a * 0.97),
                            outline=rgba((58, 58, 84), a), width=2)
        col = {"booking": PURPLE, "money": GOLD, "review": GREEN}.get(nt.get("type"), PURPLE)
        r = 38
        cx, cy = x0 + 36 + r, y0 + h / 2
        d.rounded_rectangle((cx - r, cy - r, cx + r, cy + r), radius=20, fill=rgba(col, a))
        glyph(d, {"booking": "action", "money": "trigger", "review": "outcome"}.get(nt.get("type"), "ai"),
              cx, cy, 20, rgba(BG, a))
        tx = cx + r + 26
        d.text((tx, y0 + 30), nt["title"], font=f1, fill=rgba(INK, a))
        d.text((tx, y0 + 80), nt["body"], font=f2, fill=rgba((190, 190, 210), a))
        tw = d.textlength("now", font=f3)
        d.text((x1 - 34 - tw, y0 + 34), "now", font=f3, fill=rgba(MUTED, a))
        y += h + 26


# ---------------------------------------------------------------- end card
def draw_end(frame, spec, tl, t):
    d = SDraw(frame)
    a = ease_out((t - tl.end_start) / 0.5)
    fm = font("InterDisplay-ExtraBold.otf", 104)
    w1, w2, gap = d.textlength("Pictoflex", font=fm), d.textlength("Labs", font=fm), 26
    x = (W - (w1 + gap + w2)) / 2
    y = 800 + (1 - a) * 30
    d.text((x, y), "Pictoflex", font=fm, fill=rgba(INK, a))
    d.text((x + w1 + gap, y), "Labs", font=fm, fill=rgba(PURPLE, a))
    ca = ease_out((t - tl.end_start - 0.4) / 0.5)
    fc = font("Inter-SemiBold.otf", 40)
    for i, ln in enumerate(wrap(d, spec.get("cta", "Free automation audit. Link in bio."), fc, W - 200)):
        tw = d.textlength(ln, font=fc)
        d.text(((W - tw) / 2, 980 + i * 54), ln, font=fc, fill=rgba(MUTED, ca))


# ---------------------------------------------------------------- render
def render_frame(bg, fades, spec, pos, n_rows, world_h, tl, t):
    frame = bg.copy()
    if t < tl.end_start:
        cam = camera(tl, n_rows, world_h, t)
        draw_workflow(frame, spec, pos, tl, cam, t)
        draw_hud(frame, spec, tl, t, fades)
        if t >= tl.notif_start:
            draw_notifications(frame, spec, tl, t)
    if t >= tl.end_start - 0.35:
        fade = ease_out((t - (tl.end_start - 0.35)) / 0.35)
        if t < tl.end_start:
            frame.alpha_composite(Image.new("RGBA", (RW, RH), rgba(BG, fade)))
        else:
            frame = bg.copy()
            draw_end(frame, spec, tl, t)
    return frame.convert("RGB").resize((W, H), Image.LANCZOS)


def sound_events(spec: dict) -> dict:
    """Times (seconds) of every on-screen moment, so the audio can hit them exactly."""
    _, n_rows, _ = layout(spec)
    tl = TL(n_rows)
    rows = sorted({n["row"] for n in spec["nodes"]})
    pops = []
    for r in rows:
        count = sum(1 for n in spec["nodes"] if n["row"] == r)
        pops.append((tl.row_t(r), r, count))
    coins = [tl.row_t(n["row"]) + 0.2 for n in spec["nodes"] if n.get("value")]
    dings = [tl.notif_start + 0.35 + i * 0.45 for i in range(len(spec.get("notifications", [])))]
    return {"total": tl.total, "pops": pops, "coins": coins, "dings": dings,
            "whoosh": tl.zoom_start, "end": tl.end_start}


def render_frames(spec: dict, frames_dir: str) -> float:
    """Draw every frame as a high-quality JPEG (f00000.jpg, ...). Returns the duration.

    assemble.py runs this in a separate process and encodes afterwards, so the
    drawing memory is fully released before ffmpeg starts (Render's cron plan has 512MB).
    """
    pos, n_rows, world_h = layout(spec)
    tl = TL(n_rows)
    bg, fades = make_background(), make_fades()
    for f in range(int(tl.total * FPS)):
        img = render_frame(bg, fades, spec, pos, n_rows, world_h, tl, f / FPS)
        img.save(os.path.join(frames_dir, f"f{f:05d}.jpg"), quality=97, subsampling=0)
    return tl.total


def render(spec: dict, out_path: str, ffmpeg: str = "ffmpeg") -> float:
    """Render a silent preview MP4 (local testing)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        total = render_frames(spec, tmp)
        res = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS),
                              "-i", os.path.join(tmp, "f%05d.jpg"), "-c:v", "libx264", "-crf", "16",
                              "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr[-500:])
    return total


SAMPLE = {
    "hook": "It's 11:42pm. Your clinic is closed.",
    "overview_line": "One system. Running 24/7.",
    "counter_label": "Booked while you slept",
    "nodes": [
        {"id": "dm", "row": 0, "kind": "trigger", "label": "New enquiry comes in", "sub": "Instagram, WhatsApp, website"},
        {"id": "ai", "row": 1, "kind": "ai", "label": "AI reads what they want", "sub": "Price, booking or problem"},
        {"id": "price", "row": 2, "kind": "action", "label": "Price question", "sub": "Answered in 8s"},
        {"id": "book", "row": 2, "kind": "action", "label": "Wants to book", "sub": "Free slots offered"},
        {"id": "issue", "row": 2, "kind": "action", "label": "Complaint", "sub": "Flagged to you"},
        {"id": "booked", "row": 3, "kind": "outcome", "label": "3 appointments booked", "sub": "Straight into your calendar", "value": 255},
        {"id": "remind", "row": 4, "kind": "action", "label": "Reminder sent", "sub": "24h before"},
        {"id": "deposit", "row": 4, "kind": "outcome", "label": "Deposits taken", "sub": "No more no-shows", "value": 60},
        {"id": "visit", "row": 5, "kind": "action", "label": "Visit done, review requested", "sub": "Only happy clients asked"},
        {"id": "review", "row": 6, "kind": "outcome", "label": "New 5-star Google review", "sub": "More people find you"},
    ],
    "edges": [
        ("dm", "ai"), ("ai", "price"), ("ai", "book"), ("ai", "issue"),
        ("price", "booked"), ("book", "booked"),
        ("booked", "remind"), ("booked", "deposit"),
        ("remind", "visit"), ("deposit", "visit"), ("visit", "review"),
    ],
    "results_title": "What you wake up to",
    "notifications": [
        {"type": "booking", "title": "3 new bookings", "body": "Thu 10:30 · Fri 14:00 · Sat 09:15"},
        {"type": "money", "title": "£60 in deposits", "body": "Paid overnight, no chasing"},
        {"type": "review", "title": "New 5-star review", "body": "\"Booked in seconds, so easy.\""},
        {"type": "booking", "title": "0 missed enquiries", "body": "Every message answered"},
    ],
    "cta": "Your competitors are setting this up. Free audit, link in bio.",
}

if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == "--frames":
        # used by assemble.py: python workflow_video.py --frames spec.json out_dir
        with open(sys.argv[2], encoding="utf-8") as fh:
            print(render_frames(json.load(fh), sys.argv[3]))
    else:
        out = sys.argv[1] if len(sys.argv) > 1 else "workflow_sample.mp4"
        print("duration", render(SAMPLE, out))
