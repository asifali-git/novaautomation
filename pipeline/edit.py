"""Low-RAM, fast transcode. Ultrafast preset, single thread, no denoise filter."""
import os
import subprocess
import json
import shlex

FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-800:]}")
    return proc


def probe_duration(path):
    try:
        out = subprocess.run(
            [FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, text=True,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return None


def edit(in_path, out_path, max_secs=None, denoise=None, voice_audio=None, voice_duck=False):
    """Transcode to a light mp4. Optional voiceover mix.

    - ultrafast, CRF 23, 1 video thread, 1 filter thread (peak RAM ~70MB)
    - bilinear scale to 1080x1920 vertical by default (social short)
    - tune fastdecode for quick seeking on players
    """
    cmd = [
        FFMPEG, "-y",
        "-i", in_path,
    ]
    if voice_audio:
        cmd += ["-i", voice_audio]

    # Threading + speed (the RAM levers).
    cmd += [
        "-threads", os.getenv("FFMPEG_THREADS", "1"),
        "-filter_threads", os.getenv("FFMPEG_FILTER_THREADS", "1"),
        "-filter_complex_threads", os.getenv("FFMPEG_FILTER_THREADS", "1"),
        "-preset", "ultrafast",
        "-crf", os.getenv("FFMPEG_CRF", "23"),
        "-max_muxing_queue_size", "1024",
        "-tune", "fastdecode",
    ]

    # Build filter graph.
    vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
    if denoise:
        vf = f"hqdn3d={denoise}," + vf

    if voice_audio:
        # Mix voiceover over original audio. Optionally duck original under voice.
        if voice_duck:
            ac = "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0:weights=1 0.4[a]"
        else:
            ac = "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0[a]"
        cmd += [
            "-filter_complex", f"[0:v]{vf}[v];{ac}",
            "-map", "[v]", "-map", "[a]",
        ]
    else:
        cmd += ["-vf", vf, "-map", "0:v:0", "-map", "0:a:0?"]

    cmd += ["-c:v", "libx264", "-c:a", "aac", "-b:a", "128k", "-ar", "44100"]

    if max_secs:
        cmd += ["-t", str(max_secs)]

    cmd += [out_path]
    _run(cmd)
    return out_path


def concat_clip(in_path, out_path, start=0, duration=None):
    """Cut a short clip without re-encode when possible."""
    cmd = [FFMPEG, "-y", "-ss", str(start), "-i", in_path]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero", out_path]
    try:
        _run(cmd)
    except RuntimeError:
        # Fallback: re-encode.
        cmd = [FFMPEG, "-y", "-ss", str(start), "-i", in_path]
        if duration:
            cmd += ["-t", str(duration)]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "aac", out_path]
        _run(cmd)
    return out_path
