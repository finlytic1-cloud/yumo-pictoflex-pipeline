"""
Sound design for the workflow videos, generated in code so every sound lands
exactly on its on-screen moment:

- a soft pad underneath the whole video
- a rising, musical "pop" each time a row of the workflow lights up
  (branches that fire together play a chord)
- a coin "ching" when a £ badge appears
- a whoosh as the camera pulls back to reveal the whole system
- a notification ding for each phone notification
- a low "boom" on the end card

Optionally mixes in a music track (any .mp3/.wav in assets/music/) under it.
No network, no paid service.
"""
import os
import random
import wave

import numpy as np

SR = 44100
# Notes of a major pentatonic scale, so any sequence of pops sounds pleasant.
PENTA = [261.63, 293.66, 329.63, 392.00, 440.00, 523.25, 587.33, 659.25, 783.99, 880.00, 1046.5]


def _env(n, attack=0.005, decay=0.25):
    t = np.arange(n) / SR
    a = np.clip(t / attack, 0, 1)
    return a * np.exp(-t / decay)


def _tone(freq, dur, decay, harmonics=((1, 1.0), (2, 0.35), (3, 0.12))):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = sum(amp * np.sin(2 * np.pi * freq * h * t) for h, amp in harmonics)
    return sig * _env(n, decay=decay)


def _add(buf, sig, at, gain):
    i = int(at * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j, 0] += sig[: j - i] * gain
    buf[i:j, 1] += sig[: j - i] * gain


def _pad(total):
    n = int(total * SR)
    t = np.arange(n) / SR
    chord = [130.81, 196.00, 261.63, 329.63]  # C major, soft and open
    sig = np.zeros(n)
    for k, f in enumerate(chord):
        lfo = 1 + 0.003 * np.sin(2 * np.pi * (0.13 + k * 0.05) * t)
        sig += np.sin(2 * np.pi * f * lfo * t) * (0.5 if k == 0 else 0.3)
    fade = np.clip(t / 1.5, 0, 1) * np.clip((total - t) / 1.5, 0, 1)
    return sig * fade


def _whoosh(dur=1.4):
    n = int(dur * SR)
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(n)
    out = np.zeros(n)
    # sweeping one-pole low-pass: cutoff rises then falls
    x = np.linspace(0, 1, n)
    cutoff = 0.02 + 0.25 * np.sin(np.pi * x) ** 2
    y = 0.0
    for i in range(n):
        y += cutoff[i] * (noise[i] - y)
        out[i] = y
    return out * np.sin(np.pi * x) ** 1.5 * 2.2


def build_track(events: dict, out_wav: str, music_dir: str | None = None, offset: float = 0.0,
                total: float | None = None) -> str:
    """Write a stereo WAV for the video. `offset` shifts every sound later
    (used when an opener clip plays before the workflow)."""
    total = total or (events["total"] + offset)
    buf = np.zeros((int(total * SR) + SR, 2))

    _add(buf, _pad(total), 0, 0.06)

    for k, (t, row, count) in enumerate(events["pops"]):
        base = PENTA[min(k, len(PENTA) - 1)]
        for c in range(count):  # parallel branches = chord
            f = PENTA[min(k + c * 2, len(PENTA) - 1)] if count > 1 else base
            _add(buf, _tone(f, 0.6, 0.18), offset + t + c * 0.03, 0.22 / max(1, count ** 0.5))
        _add(buf, _tone(base / 2, 0.25, 0.06, ((1, 1.0),)), offset + t, 0.18)  # soft thump

    for t in events["coins"]:
        _add(buf, _tone(1975.5, 0.5, 0.12, ((1, 1.0), (2.76, 0.3))), offset + t, 0.12)
        _add(buf, _tone(2637.0, 0.6, 0.15, ((1, 1.0),)), offset + t + 0.07, 0.10)

    _add(buf, _whoosh(), offset + events["whoosh"] - 0.2, 0.35)

    for i, t in enumerate(events["dings"]):
        f = [1318.5, 1568.0, 1760.0, 2093.0][i % 4]
        _add(buf, _tone(f, 0.7, 0.22, ((1, 1.0), (2, 0.2))), offset + t, 0.16)

    boom = _tone(55, 1.6, 0.6, ((1, 1.0), (2, 0.3)))
    _add(buf, boom, offset + events["end"], 0.45)
    _add(buf, _tone(523.25, 1.8, 0.7), offset + events["end"] + 0.05, 0.12)
    _add(buf, _tone(783.99, 1.8, 0.7), offset + events["end"] + 0.1, 0.10)

    buf = buf[: int(total * SR)]
    # gentle limiter
    peak = np.max(np.abs(buf)) or 1.0
    if peak > 0.95:
        buf *= 0.95 / peak
    pcm = (buf * 32767).astype(np.int16)
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out_wav


def pick_music(music_dir: str) -> str | None:
    if not music_dir or not os.path.isdir(music_dir):
        return None
    tracks = [os.path.join(music_dir, f) for f in os.listdir(music_dir)
              if f.lower().endswith((".mp3", ".wav", ".m4a"))]
    return random.choice(tracks) if tracks else None
