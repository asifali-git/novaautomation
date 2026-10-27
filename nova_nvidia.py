"""NOVA - NVIDIA NIM video module.

User routing (Oct 2026): NVIDIA is used for exactly two jobs.
  1. text_to_video()  -> Cosmos3-Nano, free B-roll clips (720p / 9:16 support)
  2. analyze_video()  -> Nemotron 3.5 Super VL, frames in -> JSON verdict out
Text/script work lives in pipeline/brains.py (Gemini -> OpenRouter -> NVIDIA).

Endpoint notes
  * Cosmos text2video : POST https://ai.api.nvidia.com/v1/cosmos/nvidia/cosmos3-nano
    The account must have acquired the endpoint first (build.nvidia.com -> "Get API").
    Until then the route answers HTTP 404 even though the key is valid.
  * Vision            : POST https://integrate.api.nvidia.com/v1/chat/completions
    Same key file as before - no separate key needed.

Key file: "nvidia api key.txt" (project root), or NVIDIA_API_KEY(S) env.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import requests

PROJECT_ROOT = Path(__file__).resolve().parent
KEY_FILE = PROJECT_ROOT / "nvidia api key.txt"

VISION_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
COSMOS_URL = "https://ai.api.nvidia.com/v1/cosmos/nvidia/cosmos3-nano"

MODEL_VL = "nvidia/nemotron-3.5-super-vl-120b-a12b"

DEFAULT_TIMEOUT = 180
RETRIES = 2
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

# Cosmos resolution tiers: 256 | 480 | 720, optionally with an aspect suffix.
COSMOS_ASPECTS = {"9:16": "_9_16", "16:9": "_16_9", "1:1": "_1_1", "4:3": "_4_3", "3:4": "_3_4"}


class NvidiaError(RuntimeError):
    pass


def load_key(path: Path | str | None = None) -> str:
    """Key from env first (GitHub Actions / hub), then the local key file."""
    for name in ("NVIDIA_API_KEYS", "NVIDIA_API_KEY"):
        raw = os.getenv(name) or ""
        for value in raw.split(","):
            value = value.strip()
            if value:
                return value

    key_path = Path(path) if path else KEY_FILE
    if not key_path.exists():
        raise NvidiaError(f"key file not found: {key_path}")
    lines = [l.strip() for l in key_path.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
    if not lines:
        raise NvidiaError(f"key file is empty: {key_path}")
    token = re.sub(r"[^A-Za-z0-9._-]", "", lines[0])
    token = token.removeprefix("Bearer").strip() or token
    if not token:
        raise NvidiaError(f"no usable key in {key_path}")
    return token


def _headers(key: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key or load_key()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _post(url: str, payload: Mapping[str, Any], key: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    last_error = ""
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.post(url, headers=_headers(key), json=payload, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
            if resp.status_code not in RETRY_STATUS:
                raise NvidiaError(last_error)
        except requests.RequestException as exc:
            last_error = str(exc)
        if attempt < RETRIES:
            time.sleep(2 * (attempt + 1))
    raise NvidiaError(last_error or "request failed")


# ---------------------------------------------------------------------------
# 1. Text to video (Cosmos3-Nano, free)
# ---------------------------------------------------------------------------

def _cosmos_resolution(resolution: str, aspect: str) -> str:
    if re.fullmatch(r"(256|480|720)(_[0-9]+_[0-9]+)?", resolution):
        return resolution
    tier = resolution if resolution in {"256", "480", "720"} else "720"
    return tier + COSMOS_ASPECTS.get(aspect, "_9_16")


def text_to_video(
    prompt: str,
    *,
    out_path: str | Path | None = None,
    resolution: str = "720",
    aspect: str = "9:16",
    num_frames: int = 189,
    fps: int = 24,
    seed: int = 42,
    steps: int = 35,
    timeout: int = 900,
    key: str | None = None,
) -> dict[str, Any]:
    """Generate one short clip. Returns {"path", "seconds", "resolution", "prompt"}.

    num_frames 25..197; 189 frames @24fps ~= 7.9s at the 720p tier.
    """
    frames = max(25, min(197, int(num_frames)))
    res = _cosmos_resolution(resolution, aspect)
    payload = {
        "model_mode": "text2video",
        "prompt": prompt,
        "resolution": res,
        "num_frames": frames,
        "num_inference_steps": int(steps),
        "fps": int(fps),
        "seed": int(seed),
    }
    data = _post(COSMOS_URL, payload, key=key, timeout=timeout)
    b64 = data.get("b64_video")
    if not b64:
        raise NvidiaError(f"no b64_video in response: {json.dumps(data)[:300]}")
    target = Path(out_path) if out_path else PROJECT_ROOT / "storage" / f"cosmos_{int(time.time())}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(b64))
    return {
        "path": str(target),
        "seconds": round(frames / max(1, fps), 2),
        "resolution": res,
        "aspect": aspect,
        "seed": seed,
        "prompt": prompt,
    }


# ---------------------------------------------------------------------------
# 2. Video analysis (Nemotron Super VL) -> JSON
# ---------------------------------------------------------------------------

def _media_block(path_or_url: str | Path) -> dict[str, Any]:
    value = str(path_or_url)
    if value.startswith(("http://", "https://", "data:")):
        return {"type": "image_url", "image_url": {"url": value}}
    file_path = Path(value)
    if not file_path.exists():
        raise NvidiaError(f"media file not found: {file_path}")
    mime = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(file_path.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if not found:
            raise NvidiaError("ffmpeg not available (pip install imageio-ffmpeg)")
        return found


def sample_frames(video_path: str | Path, count: int = 8) -> list[Path]:
    """Extract `count` evenly spaced JPEG frames from a video."""
    video = Path(video_path)
    if not video.exists():
        raise NvidiaError(f"video not found: {video}")
    count = max(2, min(16, int(count)))
    out_dir = Path(tempfile.mkdtemp(prefix="nova_vl_"))
    pattern = out_dir / "frame-%02d.jpg"
    cmd = [
        _ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-vf", f"fps={count}/60,scale=512:-2",
        "-frames:v", str(count),
        str(pattern),
    ]
    subprocess.run(cmd, check=True, timeout=300)
    frames = sorted(out_dir.glob("frame-*.jpg"))
    if not frames:
        raise NvidiaError("frame extraction produced nothing")
    return frames


ANALYSIS_SCHEMA = {
    "summary": "one sentence",
    "scene": "what is on screen",
    "quality": {"score": "0-10", "issues": ["blur", "watermark", "text", "shake", "artifacts"]},
    "continuity": {"character_consistent": "true|false|null", "background_consistent": "true|false|null", "notes": ""},
    "safety": {"safe": "true|false", "reason": ""},
    "caption": "<=80 char caption for this clip",
}


def analyze_frames(
    frames: Sequence[str | Path],
    *,
    question: str = "Describe this clip and rate its technical quality.",
    schema: Mapping[str, Any] | None = None,
    model: str = MODEL_VL,
    max_tokens: int = 1024,
    key: str | None = None,
) -> dict[str, Any]:
    """Ask the VL model about sampled frames. Always returns a JSON object."""
    if not frames:
        raise NvidiaError("no frames supplied")
    shape = schema or ANALYSIS_SCHEMA
    content: list[dict[str, Any]] = [_media_block(f) for f in frames]
    content.append(
        {
            "type": "text",
            "text": (
                f"{question}\n\nReturn ONLY valid JSON matching this shape: "
                f"{json.dumps(shape, ensure_ascii=False)}"
            ),
        }
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "top_p": 0.95,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    data = _post(VISION_URL, payload, key=key)
    message = (data.get("choices") or [{}])[0].get("message") or {}
    text = (message.get("content") or message.get("reasoning_content") or "").strip()
    return _parse_json(text)


def analyze_video(
    video_path: str | Path,
    *,
    question: str = "Describe this clip and rate its technical quality.",
    frames: int = 8,
    schema: Mapping[str, Any] | None = None,
    model: str = MODEL_VL,
    key: str | None = None,
) -> dict[str, Any]:
    """Sample frames from a local video and return a JSON analysis."""
    sampled = sample_frames(video_path, frames)
    result = analyze_frames(sampled, question=question, schema=schema, model=model, key=key)
    result["_video"] = str(video_path)
    result["_frames_sampled"] = len(sampled)
    return result


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
    return {"error": "model did not return JSON", "raw": text[:400]}


# ---------------------------------------------------------------------------
# Self-test: analysis works today; text_to_video needs the endpoint acquired
# ---------------------------------------------------------------------------

def _self_test() -> None:
    print("key:", load_key()[:8] + "...")
    sample = PROJECT_ROOT / "analytics-view.png"
    if sample.exists():
        print("vision json:", json.dumps(analyze_frames([sample], question="What is shown here?"), ensure_ascii=False)[:300])
    else:
        print("vision json: skipped (analytics-view.png missing)")
    try:
        info = text_to_video("a slow push-in on a coffee cup, cinematic", out_path=PROJECT_ROOT / "storage" / "cosmos_selftest.mp4")
        print("text_to_video:", info)
    except NvidiaError as exc:
        print("text_to_video: not available yet ->", str(exc)[:160])


if __name__ == "__main__":
    _self_test()
