"""
Voiceover via VoiceFinder text-to-speech (https://voicefinder.io, API docs in the app's Developers page).

Claude writes five short lines (hook, build, reveal, results, cta). Each line is
spoken once and placed on the timeline at its moment in the video. If the key
isn't set or a request fails, the video simply goes out without a voice.
"""
import os
import re
import subprocess

import requests

import config
from utils import log

ORDER = ["hook", "build", "reveal", "results", "cta"]


BASE = "https://api.voicefinder.io/v1"
DEFAULT_VOICE = "f6abdab0-2524-43d1-92e2-b39486f39fbe"   # VoiceFinder's sample voice, used if nothing better is found
_voice_cache: dict = {}


def enabled() -> bool:
    return bool(config.VOICEFINDER_API_KEY)


def _headers() -> dict:
    return {"x-api-key": config.VOICEFINDER_API_KEY, "Content-Type": "application/json"}


def _pick_voice() -> str:
    """Use VOICEFINDER_VOICE_ID if set; otherwise pick a British male voice from the catalogue."""
    if config.VOICEFINDER_VOICE_ID:
        return config.VOICEFINDER_VOICE_ID
    if "id" in _voice_cache:
        return _voice_cache["id"]
    chosen = DEFAULT_VOICE
    try:
        resp = requests.get(f"{BASE}/voices", headers=_headers(), timeout=30)
        voices = resp.json().get("voices", []) if resp.status_code == 200 else []
        def score(v):
            blob = str(v).lower()
            s = 0
            s += 3 if any(w in blob for w in ("british", "united kingdom", "en-gb", "uk english", "london")) else 0
            s += 2 if (" male" in blob or "'male'" in blob or '"male"' in blob) and "female" not in blob else 0
            s += 1 if any(w in blob for w in ("narrat", "commercial", "advert", "corporate", "warm")) else 0
            return s
        if voices:
            best = max(voices, key=score)
            if score(best) >= 3:
                chosen = best.get("voice_id", chosen)
                log.info("Voiceover voice: %s (%s)", best.get("name"), chosen)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not list VoiceFinder voices, using the default: %s", exc)
    _voice_cache["id"] = chosen
    return chosen


def _tts(text: str, out_path: str) -> str | None:
    resp = requests.post(
        f"{BASE}/text-to-speech/{_pick_voice()}",
        headers=_headers(),
        json={"text": text, "language": "en", "output_format": "mp3",
              "instruct": "confident, warm British advert narrator",
              "voice_settings": {"speed": 1.05}, "tail_pad_s": 0.1},
        timeout=300,
    )
    if resp.status_code != 200:
        log.error("VoiceFinder %s: %s", resp.status_code, resp.text[:400])
        return None
    with open(out_path, "wb") as fh:
        fh.write(resp.content)
    return out_path


def _duration(ff: str, path: str) -> float:
    res = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", res.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def build_lines(spec: dict, cues: dict, ff: str, tmp: str, total: float) -> list[dict]:
    """Returns [{"path", "start", "tempo"}] ready to mix, or [] if there's no voiceover."""
    vo = spec.get("voiceover") or {}
    if not enabled() or not vo:
        if vo and not enabled():
            log.info("VOICEFINDER_API_KEY not set - no voiceover.")
        return []
    lines, prev_end = [], 0.0
    keys = [k for k in ORDER if (vo.get(k) or "").strip()]
    for i, key in enumerate(keys):
        path = _tts(vo[key].strip(), os.path.join(tmp, f"vo_{key}.mp3"))
        if not path:
            return []          # all or nothing, so the voice never cuts out halfway
        dur = _duration(ff, path)
        start = max(cues[key], prev_end + 0.15)
        nxt = cues[keys[i + 1]] if i + 1 < len(keys) else total - 0.2
        room = max(0.8, nxt - start)
        tempo = min(1.25, max(1.0, dur / room))      # speed up slightly if it would overrun
        lines.append({"path": path, "start": start, "tempo": tempo})
        prev_end = start + dur / tempo
    log.info("Voiceover: %d lines", len(lines))
    return lines
