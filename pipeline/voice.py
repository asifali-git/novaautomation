"""Kokoro TTS voiceover. Downloads model once (~310MB), synthesizes a short script."""
import os
import urllib.request

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
MODEL_DIR = os.getenv("KOKORO_DIR", "kokoro")
MODEL_PATH = os.path.join(MODEL_DIR, "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(MODEL_DIR, "voices-v1.0.bin")

_kokoro = None  # cached — load the ~310MB model only once per process


def _ensure_files():
    os.makedirs(MODEL_DIR, exist_ok=True)
    for path, url in ((MODEL_PATH, MODEL_URL), (VOICES_PATH, VOICES_URL)):
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            print(f"[voice] downloading {os.path.basename(path)} ...")
            urllib.request.urlretrieve(url, path)


def _get():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
    return _kokoro


def synthesize(text, out_path="work/voice.wav", voice=None, speed=1.0):
    """Render `text` to a wav. Splits into sentences and adds tiny pauses
    between them for natural human-like flow (not one robotic block).
    Returns out_path. Reuses the cached model."""
    _ensure_files()
    kokoro = _get()
    voice = voice or os.getenv("KOKORO_VOICE", "af_heart")
    speed = max(0.85, min(1.1, speed))  # keep natural range

    import re
    import numpy as np

    # Split into sentences (keep punctuation).
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s for s in sentences if s]

    if not sentences:
        samples, sr = kokoro.create(text, voice=voice, speed=speed)
    elif len(sentences) == 1:
        samples, sr = kokoro.create(sentences[0], voice=voice, speed=speed)
    else:
        # Synthesize each sentence, join with tiny silence for natural breathing.
        all_parts = []
        sr = None
        pause_samples = None
        for j, sent in enumerate(sentences):
            s, sr_j = kokoro.create(sent, voice=voice, speed=speed)
            if sr is None:
                sr = sr_j
                pause_samples = np.zeros(int(sr * 0.12), dtype=s.dtype)  # 120ms pause
            all_parts.append(s)
            if j < len(sentences) - 1:
                all_parts.append(pause_samples)
        samples = np.concatenate(all_parts)

    import soundfile as sf
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    sf.write(out_path, samples, sr)
    return out_path
