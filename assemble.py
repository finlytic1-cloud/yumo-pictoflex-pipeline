"""
Builds the finished Reel: [optional Higgsfield opener] -> workflow animation,
with the generated sound design and an optional music bed underneath.
"""
import os
import subprocess
import tempfile

import sound
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


def build_reel(spec: dict, out_path: str, opener_path: str | None = None,
               music_dir: str | None = None) -> str:
    ff = ffmpeg_path()
    with tempfile.TemporaryDirectory() as tmp:
        flow = os.path.join(tmp, "flow.mp4")
        flow_len = workflow_video.render(spec, flow, ffmpeg=ff)
        events = workflow_video.sound_events(spec)

        offset = OPENER_SECONDS - XFADE if opener_path else 0.0
        total = flow_len + offset
        sfx = os.path.join(tmp, "sfx.wav")
        sound.build_track(events, sfx, offset=offset, total=total)

        # video: opener (scaled to 1080x1920, first 3s) crossfaded into the workflow
        if opener_path:
            vf = (f"[0:v]trim=0:{OPENER_SECONDS},setpts=PTS-STARTPTS,"
                  "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                  "fps=30,format=yuv420p,setsar=1[o];"
                  "[1:v]fps=30,format=yuv420p,setsar=1[f];"
                  f"[o][f]xfade=transition=fade:duration={XFADE}:offset={OPENER_SECONDS - XFADE}[v]")
            inputs = ["-i", opener_path, "-i", flow]
            audio_idx = 2
        else:
            vf = "[0:v]null[v]"
            inputs = ["-i", flow]
            audio_idx = 1
        inputs += ["-i", sfx]

        music = sound.pick_music(music_dir) if music_dir else None
        if music:
            inputs += ["-stream_loop", "-1", "-i", music]
            af = (f"[{audio_idx + 1}:a]volume=0.22,afade=t=out:st={total - 1.5}:d=1.5[m];"
                  f"[{audio_idx}:a][m]amix=inputs=2:duration=first:normalize=0[pre]")
            log.info("Mixing music: %s", os.path.basename(music))
        else:
            af = f"[{audio_idx}:a]anull[pre]"
        af += ";[pre]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100[a]"  # social-media loudness

        _run([ff, "-y", "-loglevel", "error", *inputs,
              "-filter_complex", vf + ";" + af,
              "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
              "-c:v", "libx264", "-preset", os.environ.get("X264_PRESET", "slow"), "-crf", "17",
              "-profile:v", "high", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path])
    return out_path
