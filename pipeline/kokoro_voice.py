"""Kokoro voiceover for NOVA — one continuous narration track + exact SRT timings.

Why not Edge TTS: GitHub runners get blocked by the Edge TTS endpoint
("No audio was received" / stream timeouts). Kokoro is local, free, and gives the
same voice across the whole video, which is what the continuity rules require.

Sentences are synthesised one by one so we know each sentence's exact duration:
  voice.wav      one narration track (0.12s natural gap between sentences)
  subtitles.srt  subtitles timed to the audio - no Whisper needed

Voices:
  en        am_michael (documentary male)   - af_heart (female)
  hi        hm_omega  (Devanagari male)     - hf_alpha (female)
  hinglish  af_heart / am_michael (Roman Hinglish reads naturally in an English voice)

Env:
  KOKORO_DIR  model dir, defaults to ./kokoro; model (~310MB) downloads once.
"""

from __future__ import annotations

import os
import re
import time
import urllib.request
from pathlib import Path

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

KOKORO_DIR = Path(os.getenv("KOKORO_DIR", "kokoro"))
MODEL_PATH = KOKORO_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = KOKORO_DIR / "voices-v1.0.bin"

# language -> (default voice, female voice)
VOICES = {
    "en": ("am_michael", "af_heart"),
    "hi": ("hm_omega", "hf_alpha"),
    "hinglish": ("am_michael", "af_heart"),
}
DEFAULT_VOICE = VOICES["en"][0]

PAUSE_SEC = 0.12
SPEED = 1.0

_kokoro = None  # cache: loading the ~310MB model every call is wasteful


class VoiceError(RuntimeError):
    pass


def ensure_files() -> None:
    """Download model + voices once (cached in KOKORO_DIR)."""
    KOKORO_DIR.mkdir(parents=True, exist_ok=True)
    for path, url in ((MODEL_PATH, MODEL_URL), (VOICES_PATH, VOICES_URL)):
        if not path.exists() or path.stat().st_size < 1000:
            print(f"[kokoro] downloading {path.name} (~310MB model, one time)")
            urllib.request.urlretrieve(url, path)


def _get():
    global _kokoro
    if _kokoro is None:
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise VoiceError("pip install kokoro-onnx soundfile onnxruntime") from exc
        ensure_files()
        _kokoro = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
    return _kokoro


def split_sentences(text: str) -> list[str]:
    """Split narration into subtitle-sized sentences (English + Devanagari danda)."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?।])\s+", cleaned)
    sentences = [p.strip() for p in parts if p.strip()]
    # A sentence that is far too long reads badly on screen; split on commas as well.
    out: list[str] = []
    for sentence in sentences:
        if len(sentence) <= 90:
            out.append(sentence)
            continue
        for piece in re.split(r"(?<=[,;:])\s+", sentence):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


def voice_for(language: str, female: bool = False) -> str:
    pair = VOICES.get((language or "en").lower(), VOICES["en"])
    return pair[1] if female else pair[0]


def _stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def synthesize(
    text: str,
    out_wav: str | Path,
    *,
    language: str = "en",
    voice: str | None = None,
    srt_path: str | Path | None = None,
    female: bool = False,
) -> dict:
    """Render `text` to a single wav. Returns {"wav", "seconds", "sentences", "srt", "voice"}."""
    sentences = split_sentences(text)
    if not sentences:
        raise VoiceError("empty script")

    import numpy as np
    import soundfile as sf

    engine = _get()
    name = voice or voice_for(language, female=female)
    started = time.time()

    parts: list[list[float]] = []
    timings: list[tuple[float, float]] = []
    cursor = 0.0
    sr = None
    pause = None
    for i, sentence in enumerate(sentences):
        samples, rate = engine.create(sentence, voice=name, speed=SPEED)
        if sr is None:
            sr = rate
            pause = np.zeros(int(sr * PAUSE_SEC), dtype=samples.dtype)
        elif rate != sr:
            raise VoiceError(f"unexpected sample rate {rate} (expected {sr})")
        duration = len(samples) / sr
        parts.append(np.asarray(samples))
        timings.append((cursor, cursor + duration))
        cursor += duration
        if i != len(sentences) - 1:
            parts.append(np.asarray(pause))
            cursor += PAUSE_SEC

    wav = Path(out_wav)
    wav.parent.mkdir(parents=True, exist_ok=True)
    audio = np.concatenate(parts)
    sf.write(str(wav), audio, sr)

    srt_file = Path(srt_path) if srt_path else wav.with_suffix(".srt")
    lines = []
    for index, ((start, end), sentence) in enumerate(zip(timings, sentences), start=1):
        lines.append(f"{index}\n{_stamp(start)} --> {_stamp(end)}\n{sentence}\n")
    srt_file.write_text("\n".join(lines), encoding="utf-8")

    return {
        "wav": str(wav),
        "srt": str(srt_file),
        "seconds": round(cursor, 2),
        "sentences": len(sentences),
        "voice": name,
        "elapsed_sec": round(time.time() - started, 1),
    }


if __name__ == "__main__":
    info = synthesize(
        "The story begins in the high forests of Ethiopia. Legend speaks of a goat herder named Kaldi.",
        r"C:\Users\asu\AppData\Local\Temp\opencode\kokoro_check.wav",
        language="en",
    )
    print(info)
