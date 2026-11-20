"""
Builds the finished Reel: [optional Higgsfield opener] -> workflow animation,
with the generated sound design and an optional music bed underneath.
"""
import json
import os
import subprocess
import sys
import tempfile

import sound
import voice
import workflow_video
from utils import log

OPENER_SECONDS = 3.0   # only the first 3s of the opener are used
XFADE = 0.5


def ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr[-800:]}")


def _cues(events: dict, offset: float) -> dict:
    """When each voiceover line should start (seconds into the finished video)."""
    pops = events["pops"]
    build = pops[1][0] if len(pops) > 1 else pops[0][0]
    return {
        "hook": 0.3,
        "build": offset + build,
        "reveal": offset + events["whoosh"] + 0.3,
        "results": offset + (events["dings"][0] if events["dings"] else events["whoosh"] + 2),
        "cta": offset + events["end"] + 0.2,
    }


def build_reel(spec: dict, out_path: str, opener_path: str | None = None,
               music_dir: str | None = None) -> str:
    ff = ffmpeg_path()
    with tempfile.TemporaryDirectory() as tmp:
        # Draw the frames in a separate process so its memory is freed before encoding.
        frames = os.path.join(tmp, "frames")
        os.makedirs(frames)
        spec_path = os.path.join(tmp, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow_video.py")
        res = subprocess.run([sys.executable, script, "--frames", spec_path, frames],
                             capture_output=True, text=True, timeout=3600)
        if res.returncode != 0:
            raise RuntimeError(f"frame rendering failed: {res.stderr[-800:]}")
        flow_len = float(res.stdout.strip().splitlines()[-1])
        flow = ["-framerate", str(workflow_video.FPS), "-i", os.path.join(frames, "f%05d.jpg")]
        events = workflow_video.sound_events(spec)

        offset = OPENER_SECONDS if opener_path else 0.0
        total = flow_len + offset
        sfx = os.path.join(tmp, "sfx.wav")
        sound.build_track(events, sfx, offset=offset, total=total)

        # video: opener (scaled to 1080x1920, first 3s) dips to black into the workflow.
        # (fade + concat rather than xfade, which Render's bundled ffmpeg 4.2 doesn't have)
        if opener_path:
            vf = (f"[0:v]trim=0:{OPENER_SECONDS},setpts=PTS-STARTPTS,"
                  "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                  f"fps=30,format=yuv420p,setsar=1,fade=t=out:st={OPENER_SECONDS - XFADE}:d={XFADE}[o];"
                  "[1:v]fps=30,format=yuv420p,setsar=1[f];"
                  "[o][f]concat=n=2:v=1:a=0[v]")
            inputs = ["-i", opener_path, *flow]
            audio_idx = 2
        else:
            vf = "[0:v]format=yuv420p,setsar=1[v]"
            inputs = [*flow]
            audio_idx = 1
        inputs += ["-i", sfx]

        # --- audio: sound design, optional music, optional voiceover -------------------
        streams = []                                   # labelled audio streams to mix
        nxt = audio_idx + 1
        vo_lines = voice.build_lines(spec, _cues(events, offset), ff, tmp, total)
        sfx_gain = 0.55 if vo_lines else 1.0           # sit the effects under the voice
        af = f"[{audio_idx}:a]volume={sfx_gain}[sx]"
        streams.append("[sx]")

        music = sound.pick_music(music_dir) if music_dir else None
        if music:
            inputs += ["-stream_loop", "-1", "-i", music]
            mg = 0.12 if vo_lines else 0.22
            af += f";[{nxt}:a]volume={mg},afade=t=out:st={total - 1.5}:d=1.5[mu]"
            streams.append("[mu]")
            nxt += 1
            log.info("Mixing music: %s", os.path.basename(music))

        for i, ln in enumerate(vo_lines):
            inputs += ["-i", ln["path"]]
            ms = int(ln["start"] * 1000)
            af += f";[{nxt}:a]atempo={ln['tempo']:.3f},adelay={ms}|{ms},volume=1.0[vo{i}]"
            streams.append(f"[vo{i}]")
            nxt += 1

        if len(streams) > 1:
            # amix divides by the input count; multiply back (works on old ffmpeg, no normalize option)
            af += (f";{''.join(streams)}amix=inputs={len(streams)}:duration=first:dropout_transition=0,"
                   f"volume={len(streams)}[pre]")
        else:
            af += ";[sx]anull[pre]"
        # social-media loudness; explicit format so older ffmpeg builds (Render's) can negotiate it
        af += (";[pre]aformat=sample_fmts=fltp:channel_layouts=stereo,loudnorm=I=-14:TP=-1.5:LRA=11,"
               "aresample=44100,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a]")

        _run([ff, "-y", "-loglevel", "error", *inputs,
              "-filter_complex", vf + ";" + af,
              "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
              "-c:v", "libx264", "-preset", os.environ.get("X264_PRESET", "faster"), "-crf", "17",
              "-threads", "2", "-filter_complex_threads", "1", "-x264-params", "rc-lookahead=10:sync-lookahead=0",
              "-profile:v", "high", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path])
    return out_path
