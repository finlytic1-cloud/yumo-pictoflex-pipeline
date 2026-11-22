"""
Cinematic mode: Higgsfield generates the workflow footage, we add the words.

AI video models make beautiful motion but can't write clean text, so the split is:
  - Higgsfield: one continuous, high-end 3D motion-graphics shot of the workflow
    (light travelling between glowing glass nodes, branching, merging, payoff).
  - This file: a transparent overlay drawn on top, frame by frame - hook line,
    each step appearing as a glass "chip" that types itself out, the £ counter,
    then the phone notifications and the Pictoflex Labs end card.

render_overlay_frames() writes RGBA PNGs; assemble.py lays them over the clip.
"""
import math
import os
import sys

import numpy as np
from PIL import Image

import workflow_video as wv
from workflow_video import (H, W, K, RH, RW, FPS, SDraw, clamp, ease_back, ease_out, font, glyph, lerp,
                            mix, rgba, wrap)

STEP_GAP = 1.25        # seconds between steps
BRANCH_STAGGER = 0.3   # branches in the same row fire this far apart
FIRST_STEP = 2.6       # after the hook
NOTIF_LEN = 3.6
END_LEN = 2.4

# words the video prompt uses for each colour theme, so footage and overlay match
THEME_WORDS = {
    "violet": "deep violet and emerald green light on near-black",
    "circuit": "electric cyan and lime light on a dark blue-black circuit-board world",
    "ember": "warm amber gold and coral light on deep charcoal, luxurious",
    "aqua": "teal and magenta light on a deep ocean-black background",
}

VIDEO_STYLE = (
    "Ultra high-end 3D motion design commercial, vertical 9:16. A living automation workflow visualised as "
    "floating frosted-glass panels and glowing orbs connected by fibre-optic light paths. Pulses of light race "
    "along the paths, split into parallel branches, merge back together, and end in a satisfying burst. "
    "One smooth continuous camera move (slow dolly and gentle orbit), shallow depth of field, volumetric haze, "
    "crisp reflections, satisfying precise motion, premium tech-brand aesthetic. "
    "Absolutely NO readable text, letters, numbers, logos, UI words, people, hands, phones or screens."
)


def steps(spec: dict) -> list[dict]:
    """Every node, in the order it fires, with its time."""
    rows = {}
    for n in spec["nodes"]:
        rows.setdefault(n["row"], []).append(n)
    out = []
    for i, r in enumerate(sorted(rows)):
        for j, n in enumerate(rows[r]):
            out.append({**n, "t": FIRST_STEP + i * STEP_GAP + j * BRANCH_STAGGER})
    return out


def clip_seconds(spec: dict) -> int:
    n_rows = len({n["row"] for n in spec["nodes"]})
    return int(min(15, max(10, math.ceil(FIRST_STEP + n_rows * STEP_GAP + 0.8))))


def video_prompt(spec: dict) -> str:
    beats = spec.get("cinematic_prompt", "").strip()
    colours = THEME_WORDS.get(spec.get("theme", "violet"), THEME_WORDS["violet"])
    return f"{VIDEO_STYLE} Colour palette: {colours}. Story beats: {beats}"[:2400]


def timeline(spec: dict) -> dict:
    clip = clip_seconds(spec)
    return {"clip": clip, "notif": clip, "end": clip + NOTIF_LEN, "total": clip + NOTIF_LEN + END_LEN}


def sound_events(spec: dict) -> dict:
    tl = timeline(spec)
    st = steps(spec)
    pops = [(s["t"], i, 1) for i, s in enumerate(st)]
    coins = [s["t"] + 0.2 for s in st if s.get("value")]
    dings = [tl["notif"] + 0.35 + i * 0.45 for i in range(len(spec.get("notifications", [])))]
    return {"total": tl["total"], "pops": pops, "coins": coins, "dings": dings,
            "whoosh": tl["clip"] - 0.6, "end": tl["end"]}


# ---------------------------------------------------------------- drawing
def _gradients():
    """Dark bands top and bottom so white text reads over any footage."""
    top_h, bot_h = 560 * K, 980 * K
    top = np.zeros((top_h, RW, 4), dtype=np.uint8)
    top[..., :3] = wv.BG
    ys = np.arange(top_h) / K
    top[..., 3] = (np.clip(0.85 - ys / 560 * 0.85, 0, 1)[:, None] * 255).astype(np.uint8)
    bot = np.zeros((bot_h, RW, 4), dtype=np.uint8)
    bot[..., :3] = wv.BG
    yb = np.arange(bot_h) / K
    bot[..., 3] = (np.clip(yb / 980 * 0.9, 0, 0.9)[:, None] * 255).astype(np.uint8)
    return Image.fromarray(top), Image.fromarray(bot)


def _chip(d, n, x0, y0, t, a):
    col = wv.KIND_COLOUR.get(n.get("kind", "action"), wv.PURPLE)
    local = t - n["t"]
    lit = ease_out(local / 0.3)
    w, h = W - 2 * x0, 124
    r_ = wv.THEME["radius"]
    d.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=r_, fill=rgba(wv.CARD, 0.82 * a),
                        outline=rgba(mix(wv.CARD_LINE, col, lit), a), width=3)
    if 0 < local < 0.6:   # ripple as it lands
        g = 22 * ease_out(local / 0.6)
        d.rounded_rectangle((x0 - g, y0 - g, x0 + w + g, y0 + h + g), radius=r_ + g,
                            outline=rgba(col, (1 - local / 0.6) * 0.8 * a), width=3)
    br = 36
    bx, by = x0 + 30 + br, y0 + h / 2
    d.ellipse((bx - br, by - br, bx + br, by + br), fill=rgba(mix(wv.PURPLE_DIM, col, lit), a))
    glyph(d, n.get("kind", "action"), bx, by, br * 0.55, rgba(wv.BG if lit > 0.5 else wv.INK, a))
    if n.get("kind") == "outcome" and 0 < local < 0.9:   # sparks
        bp = local / 0.9
        for k in range(14):
            ang = k * math.pi / 7
            dist = br + 80 * ease_out(bp)
            px, py = bx + math.cos(ang) * dist, by + math.sin(ang) * dist
            pr = 5 * (1 - bp) + 1.5
            d.ellipse((px - pr, py - pr, px + pr, py + pr), fill=rgba(col, (1 - bp) * a))
    tx = bx + br + 24
    max_w = x0 + w - tx - 24
    fl, fs = font("Inter-SemiBold.otf", 38), font("Inter-Regular.otf", 27)
    label = n["label"]
    shown = label[: int(len(label) * clamp(local / 0.35))] if local < 0.35 else label
    d.text((tx, y0 + 22), wrap(d, shown, fl, max_w)[0] if shown else "", font=fl, fill=rgba(wv.INK, a))
    if n.get("sub"):
        d.text((tx, y0 + 72), wrap(d, n["sub"], fs, max_w)[0], font=fs, fill=rgba(wv.MUTED, a * lit))
    if n.get("value"):
        badge = f"+£{n['value']}"
        fb = font("Inter-Bold.otf", 28)
        bw, bh = d.textlength(badge, font=fb) + 28, 44
        ba = clamp((local - 0.2) / 0.25) * a
        bx1, by1 = x0 + w - bw - 18, y0 - bh * 0.5 - (1 - ease_back((local - 0.2) / 0.4)) * 18
        d.rounded_rectangle((bx1, by1, bx1 + bw, by1 + bh), radius=bh / 2, fill=rgba(wv.GOLD, ba))
        d.text((bx1 + 14, by1 + 7), badge, font=fb, fill=rgba(wv.BG, ba))


def _overlay(spec, st, tl, grads, bg, t):
    if t >= tl["end"]:                                   # end card: fully opaque
        frame = bg.copy()
        wv.draw_end(frame, spec, _TL(tl), t)
        return frame
    frame = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    top, bot = grads
    frame.alpha_composite(top, dest=(0, 0))
    frame.alpha_composite(bot, dest=(0, RH - bot.height))
    d = SDraw(frame)
    phase_a = 1 - ease_out((t - tl["notif"] + 0.3) / 0.3)

    if phase_a > 0:
        # tag + headline (hook, then the overview line)
        ft = font("Inter-Medium.otf", 24)
        tag = spec.get("tag", "Example automation").upper()
        d.text(((W - d.textlength(tag, font=ft)) / 2, 92), tag, font=ft, fill=rgba(wv.PURPLE, 0.95 * phase_a))
        fh = font("InterDisplay-ExtraBold.otf", 74)
        a1 = ease_out(t / 0.5) * (1 - ease_out((t - FIRST_STEP + 0.4) / 0.35))
        a2 = ease_out((t - FIRST_STEP) / 0.4) * phase_a
        for text, a in ((spec["hook"], a1), (spec.get("overview_line", ""), a2)):
            if a > 0 and text:
                for i, ln in enumerate(wrap(d, text, fh, W - 150)):
                    d.text(((W - d.textlength(ln, font=fh)) / 2, 132 + i * 86), ln, font=fh,
                           fill=rgba(wv.INK, a))

        # step chips stack upwards from the bottom, newest at the bottom
        base, pitch = 1380, 160
        appeared = [s for s in st if t >= s["t"] - 0.15]
        for j, s in enumerate(appeared):
            slot = sum(ease_out((t - o["t"]) / 0.35) for o in appeared[j + 1:])
            a = clamp(ease_out((t - s["t"] + 0.15) / 0.3)) * clamp(1 - (slot - 3)) * phase_a
            if a <= 0:
                continue
            y = base - slot * pitch + (1 - ease_back((t - s["t"] + 0.15) / 0.45)) * 60
            _chip(d, s, 70, y, t, a)

        # £ counter
        total = sum(s["value"] * ease_out((t - s["t"] - 0.1) / 0.6) for s in st if s.get("value"))
        if spec.get("counter_label") and total > 0:
            fl, fv = font("Inter-Medium.otf", 32), font("InterDisplay-ExtraBold.otf", 92)
            lab = spec["counter_label"]
            d.text(((W - d.textlength(lab, font=fl)) / 2, H - 250), lab, font=fl, fill=rgba(wv.MUTED, phase_a))
            val = f"£{int(round(total)):,}"
            d.text(((W - d.textlength(val, font=fv)) / 2, H - 205), val, font=fv, fill=rgba(wv.GOLD, phase_a))

    if t >= tl["notif"]:
        wv.draw_notifications(frame, spec, _TL(tl), t)
    if t >= tl["end"] - 0.35:                            # fade to the end card
        f = ease_out((t - (tl["end"] - 0.35)) / 0.35)
        frame.alpha_composite(Image.new("RGBA", (RW, RH), rgba(wv.BG, f)))
    return frame


class _TL:
    def __init__(self, tl):
        self.notif_start, self.end_start = tl["notif"], tl["end"]


def render_overlay_frames(spec: dict, out_dir: str) -> float:
    wv.apply_theme(spec.get("theme", "violet"))
    st, tl = steps(spec), timeline(spec)
    grads, bg = _gradients(), wv.make_background()
    for f in range(int(tl["total"] * FPS)):
        img = _overlay(spec, st, tl, grads, bg, f / FPS).resize((W, H), Image.LANCZOS)
        img.save(os.path.join(out_dir, f"o{f:05d}.png"), compress_level=1)
    return tl["total"]


if __name__ == "__main__":
    import json
    # used by assemble.py: python cinematic.py spec.json out_dir
    with open(sys.argv[1], encoding="utf-8") as fh:
        print(render_overlay_frames(json.load(fh), sys.argv[2]))
