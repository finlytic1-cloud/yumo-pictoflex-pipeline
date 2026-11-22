"""
"Higgsfield makes the whole video" mode (VIDEO_MODE=higgsfield_full).

Complete videos are made in batches in the Higgsfield app (with plan credits) and
listed in assets/videos/queue.json, each with its caption, hashtags and voiceover
script. Every day Stage 1 takes the next unused video from the queue, adds the
VoiceFinder voiceover (and, if END_CARD is on, a short Pictoflex Labs logo card),
and hands it to Stage 2 to post. If the queue is empty, nothing is posted that day.

queue.json format:
[
  {"name": "wk48_01", "url": "https://...mp4", "industry": "dental practice",
   "hook": "short description", "caption": "One sentence.", "hashtags": "#a #b #c",
   "cta": "Free automation audit. Link in bio.",
   "voiceover": {"hook": "...", "build": "...", "results": "...", "cta": "..."}}
]
"""
import json
import os
import re
import subprocess
import tempfile

import requests

import config
import db
from utils import log

QUEUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "videos", "queue.json")
END_SECONDS = 2.4


def next_video() -> dict | None:
    if not os.path.exists(QUEUE):
        return None
    with open(QUEUE, encoding="utf-8") as fh:
        queue = json.load(fh)
    used = {r["v"] for r in db.fetch_all(
        "SELECT visual_spec->>'video' AS v FROM content_items WHERE brand = 'pictoflex' AND visual_spec ? 'video';")}
    for item in queue:
        if item.get("name") and item.get("url") and item["name"] not in used:
            return item
    return None


def _duration(ff: str, path: str) -> float:
    res = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", res.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def _has_audio(ff: str, path: str) -> bool:
    res = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True)
    return "Audio:" in res.stderr


def _end_card(item: dict, path: str) -> None:
    """A still Pictoflex Labs card (drawn once) for the last couple of seconds."""
    import workflow_video as wv
    from PIL import Image
    wv.apply_theme("violet")
    frame = wv.make_background()

    class _T:
        end_start = 0.0
    wv.draw_end(frame, {"cta": item.get("cta") or "Free automation audit. Link in bio."}, _T, 5.0)
    frame.convert("RGB").resize((wv.W, wv.H), Image.LANCZOS).save(path, quality=95)


def build(item: dict, out_path: str) -> str:
    import assemble
    import voice
    ff = assemble.ffmpeg_path()
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "video.mp4")
        resp = requests.get(item["url"], timeout=180)
        resp.raise_for_status()
        with open(src, "wb") as fh:
            fh.write(resp.content)
        vid_len = _duration(ff, src)
        end_len = END_SECONDS if config.END_CARD else 0.0
        total = vid_len + end_len

        inputs = ["-i", src]
        if config.END_CARD:
            card = os.path.join(tmp, "card.jpg")
            _end_card(item, card)
            inputs += ["-loop", "1", "-t", f"{end_len:.2f}", "-i", card]
            vf = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1,"
                  "format=yuv420p[a0];"
                  f"[1:v]scale=1080:1920,fps=30,setsar=1,format=yuv420p,fade=t=in:st=0:d=0.4[a1];"
                  "[a0][a1]concat=n=2:v=1:a=0[v]")
        else:
            vf = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1,format=yuv420p[v]"

        # base audio: the video's own sound if it has any, otherwise silence
        base_idx = len([x for x in inputs if x == "-i"])
        if _has_audio(ff, src):
            af_base = f"[0:a]apad=whole_dur={total:.2f}[base]"
        else:
            inputs += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
            af_base = f"[{base_idx}:a]anull[base]"
            base_idx += 1

        spec = {"voiceover": item.get("voiceover") or {}}
        cues = {"hook": 0.3, "build": vid_len * 0.3, "reveal": vid_len * 0.55, "results": vid_len * 0.7,
                "cta": vid_len + 0.2 if end_len else vid_len * 0.85}
        vo_lines = voice.build_lines(spec, cues, ff, tmp, total)

        streams, af = ["[b]"], af_base + f";[base]volume={0.45 if vo_lines else 1.0}[b]"
        nxt = len([x for x in inputs if x == "-i"])
        for i, ln in enumerate(vo_lines):
            inputs += ["-i", ln["path"]]
            ms = int(ln["start"] * 1000)
            af += f";[{nxt}:a]atempo={ln['tempo']:.3f},adelay={ms}|{ms}[vo{i}]"
            streams.append(f"[vo{i}]")
            nxt += 1
        if len(streams) > 1:
            af += (f";{''.join(streams)}amix=inputs={len(streams)}:duration=first:dropout_transition=0,"
                   f"volume={len(streams)}[pre]")
        else:
            af += ";[b]anull[pre]"
        af += (";[pre]aformat=sample_fmts=fltp:channel_layouts=stereo,loudnorm=I=-14:TP=-1.5:LRA=11,"
               "aresample=44100,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a]")
        assemble._run([ff, "-y", "-loglevel", "error", *inputs, "-filter_complex", vf + ";" + af,
                       "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
                       "-c:v", "libx264", "-preset", os.environ.get("X264_PRESET", "faster"), "-crf", "17",
                       "-threads", "1", "-x264-params", "rc-lookahead=8:sync-lookahead=0",
                       "-profile:v", "high", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path])
    log.info("Built %s (%.1fs, voiceover lines: %d)", item["name"], total, len(vo_lines))
    return out_path
