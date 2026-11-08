"""
Builds a vertical (1080x1920) silent MP4 "Reel" slideshow from a post's
slide images, for Instagram/Facebook Reels. Pure helper - no network.
"""
import os
import shutil
import subprocess
import tempfile

SECONDS_PER_SLIDE = 3.0
FADE_SECONDS = 0.4
WIDTH, HEIGHT = 1080, 1920
PAD_COLOUR = "0x08080f"  # near-black, matches the slide backgrounds


def _ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg  # bundled static ffmpeg (works on Render)
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        path = shutil.which("ffmpeg")
        if not path:
            raise RuntimeError("ffmpeg not available (install imageio-ffmpeg)")
        return path


def make_reel(slide_images: list[bytes]) -> bytes:
    """slide_images: JPEG/PNG bytes, one per slide, in order. Returns MP4 bytes."""
    if not slide_images:
        raise ValueError("no slides to build a reel from")
    ffmpeg = _ffmpeg_path()
    n = len(slide_images)
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [ffmpeg, "-y", "-loglevel", "error"]
        for i, img in enumerate(slide_images):
            p = os.path.join(tmp, f"s{i}.jpg")
            with open(p, "wb") as fh:
                fh.write(img)
            cmd += ["-loop", "1", "-t", str(SECONDS_PER_SLIDE + FADE_SECONDS), "-i", p]
        # Silent audio track (some platforms reject video with no audio stream).
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

        parts = []
        for i in range(n):
            parts.append(
                f"[{i}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color={PAD_COLOUR},"
                f"setsar=1,fps=30,format=yuv420p[v{i}]"
            )
        if n == 1:
            last = "v0"
        else:
            prev = "v0"
            for i in range(1, n):
                offset = i * SECONDS_PER_SLIDE
                out = f"x{i}"
                parts.append(
                    f"[{prev}][v{i}]xfade=transition=fade:duration={FADE_SECONDS}:offset={offset}[{out}]"
                )
                prev = out
            last = prev
        total = n * SECONDS_PER_SLIDE + FADE_SECONDS
        out_path = os.path.join(tmp, "reel.mp4")
        cmd += [
            "-filter_complex", ";".join(parts),
            "-map", f"[{last}]", "-map", f"{n}:a",
            "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-shortest",
            "-movflags", "+faststart",
            out_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if res.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError(f"ffmpeg failed: {res.stderr[-500:]}")
        with open(out_path, "rb") as fh:
            return fh.read()
