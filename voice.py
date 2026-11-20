"""
Voiceover via ElevenLabs text-to-speech.

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


def enabled() -> bool:
    return bool(config.ELEVENLABS_API_KEY)


def _tts(text: str, out_path: str) -> str | None:
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={"text": text, "model_id": config.ELEVENLABS_MODEL,
              "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.25}},
        timeout=60,
    )
    if resp.status_code != 200:
        log.error("ElevenLabs %s: %s", resp.status_code, resp.text[:400])
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
            log.info("ELEVENLABS_API_KEY not set - no voiceover.")
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
