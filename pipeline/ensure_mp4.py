"""Ensure a file is a real MP4 (h264/aac) without changing resolution or fps.

Stream-copy first (zero quality loss, keeps fps/resolution exactly). If the
source codec is not MP4-compatible, transcode with libx264 while preserving
the original frame rate and dimensions.

Usage:
    python pipeline/ensure_mp4.py input.webm output.mp4
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if not found:
            raise SystemExit("ffmpeg not available")
        return found


def probe(path: Path) -> dict:
    # imageio-ffmpeg ships ffmpeg (not ffprobe) — parse `ffmpeg -i` stderr.
    r = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    text = r.stderr or ""
    stream: dict = {}
    m = re.search(r"Video:\s*(\w+).*?,\s*(\d+)x(\d+).*?(?:(\d+(?:\.\d+)?)\s*fps)", text)
    if m:
        stream = {
            "codec_name": m.group(1),
            "width": int(m.group(2)),
            "height": int(m.group(3)),
            "avg_frame_rate": m.group(4),
        }
    fmt = "mp4" if "mov,mp4" in text else ""
    return {"format": {"format_name": fmt}, "streams": [stream] if stream else []}


def is_mp4_h264(path: Path) -> bool:
    info = probe(path)
    fmt = (info.get("format") or {}).get("format_name", "")
    streams = info.get("streams") or []
    codec = streams[0].get("codec_name", "") if streams else ""
    return "mp4" in fmt and codec in ("h264", "avc1")


def ensure_mp4(src: Path, dst: Path) -> Path:
    """Return a path to an MP4 (h264/aac) with identical fps + resolution."""
    if not src.exists():
        raise SystemExit(f"source missing: {src}")
    # Only h264 in MP4 is universally playable on Android. AV1/VP9/HEVC
    # inside MP4 must be transcoded even though the container says "mp4".
    if is_mp4_h264(src):
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        return dst

    dst.unlink(missing_ok=True)
    cmd_enc = [
        ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", str(dst),
    ]
    res = subprocess.run(cmd_enc, capture_output=True, text=True, timeout=3600)
    if res.returncode != 0:
        raise SystemExit(f"ffmpeg convert failed: {res.stderr[:300]}")
    return dst


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: ensure_mp4.py <input> <output.mp4>")
    out = ensure_mp4(Path(sys.argv[1]), Path(sys.argv[2]))
    info = probe(out)
    streams = info.get("streams") or []
    s = streams[0] if streams else {}
    print(f"MP4: {out} ({out.stat().st_size / 1e6:.1f} MB) "
          f"{s.get('width')}x{s.get('height')} @ {s.get('avg_frame_rate')}")
