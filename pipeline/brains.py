"""Text brain chain for NOVA: Gemini -> OpenRouter -> NVIDIA NIM.

User-pinned routing (Oct 2026):
  script / titles / terms  : Gemini first, OpenRouter fallback, NVIDIA last
  video QC + video analyse : NVIDIA NIM only, always JSON
  text-to-video            : NVIDIA Cosmos only (see nova_nvidia.cosmos_text2video)

Keys: hub snapshot / env (apikeys.keys), then the local *.txt key files used in dev.
Never print keys; only provider + model names are logged.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from pipeline import apikeys
except Exception:  # pragma: no cover - direct script run
    apikeys = None

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENROUTER_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

GEMINI_MODELS = [m for m in (os.getenv("GEMINI_TEXT_MODEL") or "").split(",") if m] or [
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
]
OPENROUTER_MODELS = [m for m in (os.getenv("OPENROUTER_TEXT_MODEL") or "").split(",") if m] or [
    "deepseek/deepseek-chat-v3.1:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]
NVIDIA_MODELS = [m for m in (os.getenv("NVIDIA_TEXT_MODEL") or "").split(",") if m] or [
    "nvidia/nemotron-3.5-lightning-30b-a3b",
]

KEY_FILES = {
    "gemini": ROOT / "gemini api key.txt",
    "openrouter": ROOT / "openrouter api key.txt",
    "nvidia": ROOT / "nvidia api key.txt",
}
ENV_NAMES = {
    "gemini": ("GEMINI_API_KEYS", "GEMINI_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEYS", "OPENROUTER_API_KEY"),
    "nvidia": ("NVIDIA_API_KEYS", "NVIDIA_API_KEY"),
}
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class BrainError(RuntimeError):
    """Raised when every provider in the chain failed."""


@dataclass
class Attempt:
    provider: str
    model: str
    ok: bool
    error: str = ""

    def as_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model, "ok": self.ok, "error": self.error}


@dataclass
class Result:
    text: str
    provider: str
    model: str
    attempts: list[Attempt]

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "attempts": [a.as_dict() for a in self.attempts],
        }


def _file_key(service: str) -> str:
    path = KEY_FILES.get(service)
    if not path or not path.exists():
        return ""
    lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
    if not lines:
        return ""
    token = re.sub(r"[^A-Za-z0-9._-]", "", lines[0])
    return token if len(token) >= 20 else ""


def keys_for(service: str) -> list[str]:
    """Ordered keys: hub/env first (production), local key file second (dev)."""
    out: list[str] = []
    if apikeys is not None:
        try:
            out.extend(apikeys.keys(service, *ENV_NAMES[service]))
        except Exception:
            pass
    for name in ENV_NAMES[service]:
        for value in (os.getenv(name) or "").split(","):
            value = value.strip()
            if value and value not in out:
                out.append(value)
    local = _file_key(service)
    if local and local not in out:
        out.append(local)
    return out


def providers() -> list[str]:
    """Providers that actually have a key, in priority order."""
    return [name for name in ("gemini", "openrouter", "nvidia") if keys_for(name)]


# ---------------------------------------------------------------------------
# Provider transports
# ---------------------------------------------------------------------------

def _post(url: str, payload: dict, headers: dict, timeout: int = 180) -> dict:
    last = ""
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            last = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code not in RETRY_STATUS:
                raise BrainError(last)
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(1.5 * (attempt + 1))
    raise BrainError(last or "request failed")


def _gemini(model: str, prompt: str, system: str, max_tokens: int, temperature: float, key: str) -> str:
    payload: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    data = _post(
        GEMINI_URL.format(model=model),
        payload,
        {"x-goog-api-key": key, "Content-Type": "application/json"},
    )
    candidates = data.get("candidates") or []
    if not candidates:
        raise BrainError(f"no candidates: {json.dumps(data)[:200]}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise BrainError("empty completion")
    return text


def _openai_style(url: str, model: str, prompt: str, system: str, max_tokens: int, temperature: float, key: str) -> str:
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if "openrouter" in url:
        headers["HTTP-Referer"] = "https://nova.local"
        headers["X-Title"] = "NOVA Studio"
    data = _post(url, payload, headers)
    choices = data.get("choices") or []
    if not choices:
        raise BrainError(f"no choices: {json.dumps(data)[:200]}")
    text = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not text:
        raise BrainError("empty completion")
    return text


def _call(provider: str, model: str, prompt: str, system: str, max_tokens: int, temperature: float) -> str:
    key = keys_for(provider)[0]
    if provider == "gemini":
        return _gemini(model, prompt, system, max_tokens, temperature, key)
    if provider == "openrouter":
        return _openai_style(OPENROUTER_URL, model, prompt, system, max_tokens, temperature, key)
    if provider == "nvidia":
        return _openai_style(NVIDIA_BASE_URL, model, prompt, system, max_tokens, temperature, key)
    raise BrainError(f"unknown provider: {provider}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def complete(
    prompt: str,
    *,
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.7,
    chain: list[str] | None = None,
) -> Result:
    """Run `prompt` through the provider chain, first success wins."""
    order = chain or providers()
    attempts: list[Attempt] = []
    for provider in order:
        models = {"gemini": GEMINI_MODELS, "openrouter": OPENROUTER_MODELS, "nvidia": NVIDIA_MODELS}[provider]
        for model in models:
            try:
                text = _call(provider, model, prompt, system, max_tokens, temperature)
                attempts.append(Attempt(provider, model, True))
                return Result(text=text, provider=provider, model=model, attempts=attempts)
            except Exception as exc:  # any provider failure -> next in chain
                attempts.append(Attempt(provider, model, False, str(exc)[:300]))
                continue
    raise BrainError(
        "all providers failed: " + json.dumps([a.as_dict() for a in attempts], ensure_ascii=False)[:800]
    )


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError(f"no JSON object in model output: {text[:200]}")


def complete_json(
    prompt: str,
    *,
    schema_hint: str = "",
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.4,
    chain: list[str] | None = None,
) -> tuple[dict, Result]:
    """Same chain, but the answer must be a JSON object."""
    instruction = prompt.rstrip()
    instruction += "\n\nReturn ONLY a single valid JSON object"
    if schema_hint:
        instruction += f" with this shape: {schema_hint}"
    instruction += ". No markdown fences, no commentary."
    last_error = ""
    for _ in range(2):
        result = complete(instruction, system=system, max_tokens=max_tokens, temperature=temperature, chain=chain)
        try:
            return _extract_json(result.text), result
        except ValueError as exc:
            last_error = str(exc)
    raise BrainError(f"model did not return valid JSON ({last_error})")


DOC_SYSTEM = (
    "You are a documentary scriptwriter for short-form vertical video. "
    "Write factual, calm, cinematic narration. No hype words, no emojis, "
    "no on-screen directions, no speaker labels."
)

LANGUAGES = {
    "en": {
        "label": "English",
        "script_rule": "Write the narration in clear, simple documentary English.",
        "packaging_rule": "Write title, hook, description and tags in English.",
        "voice": "en-US-AndrewNeural-Male",
    },
    "hi": {
        "label": "Hindi (Devanagari)",
        "script_rule": (
            "Write the narration in natural spoken Hindi using Devanagari script. "
            "Brand names, numbers and English technical words may stay in Latin script. "
            "Avoid heavy Sanskritised vocabulary - keep it like a calm documentary voice-over."
        ),
        "packaging_rule": "Write title, hook, description and tags in Hindi (Devanagari).",
        "voice": "hi-IN-MadhurNeural-Male",
    },
    "hinglish": {
        "label": "Hinglish (Roman script)",
        "script_rule": (
            "Write the narration in Hinglish: everyday Hindi mixed with English, written ONLY in "
            "Roman/Latin letters (never Devanagari). Keep a natural spoken flow, for example "
            "'Coffee ki kahani Ethiopia ke pahaadon se shuru hoti hai...'. English words stay English."
        ),
        "packaging_rule": "Write title, hook, description and tags in Hinglish (Roman script).",
        "voice": "en-IN-PrabhatNeural-Male",
    },
}

BRIEF_SCHEMA = (
    '{"title": "<=60 chars searchable title", '
    '"hook": "one sentence that stops the scroll", '
    '"script": "narration text only, ready for text-to-speech", '
    '"search_terms": ["6-8 short ENGLISH stock-footage queries, 1-3 words each"], '
    '"description": "2 sentence YouTube description", '
    '"tags": ["8-12 lowercase tags"]}'
)


def documentary_brief(
    topic: str,
    *,
    paragraphs: int = 1,
    words_per_paragraph: int = 130,
    language: str = "en",
    angle: str = "",
    chain: list[str] | None = None,
) -> dict:
    """Script + packaging for the free stock documentary path. Gemini first."""
    lang = LANGUAGES.get((language or "en").lower(), LANGUAGES["en"])
    prompt = (
        f"Topic: {topic}\n"
        f"Narration length: about {paragraphs * words_per_paragraph} words total "
        f"(roughly {paragraphs} minute(s) of spoken audio).\n"
        f"Output language: {lang['label']}\n"
        f"Style rule: {lang['script_rule']}\n"
        f"Packaging rule: {lang['packaging_rule']}\n"
    )
    if angle:
        prompt += f"Angle to emphasise: {angle}\n"
    prompt += (
        "Write one continuous narration for text-to-speech: short sentences, concrete imagery, "
        "no bullet points, no headings. Then packaging: a title, a hook line, 6-8 stock-footage "
        "search terms, a description, and tags.\n"
        "IMPORTANT: search_terms must always be short English queries (they are sent to Pixabay).\n\n"
        f"JSON shape: {BRIEF_SCHEMA}"
    )
    data, result = complete_json(prompt, system=DOC_SYSTEM, max_tokens=4096, temperature=0.5, chain=chain)
    script = str(data.get("script") or "").strip()
    terms = [str(t).strip() for t in (data.get("search_terms") or []) if str(t).strip()]
    return {
        "topic": topic,
        "language": lang["label"],
        "language_code": (language or "en").lower(),
        "voice": lang["voice"],
        "angle": angle,
        "title": str(data.get("title") or topic).strip(),
        "hook": str(data.get("hook") or "").strip(),
        "script": script,
        "search_terms": terms,
        "description": str(data.get("description") or "").strip(),
        "tags": [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()],
        "words": len(script.split()),
        "provider": result.provider,
        "model": result.model,
        "attempts": [a.as_dict() for a in result.attempts],
    }


if __name__ == "__main__":
    print("providers:", providers())
    if not providers():
        raise SystemExit("no API keys found")
    out = documentary_brief("the history of coffee", paragraphs=1)
    print(json.dumps({k: v for k, v in out.items() if k != "script"}, indent=2, ensure_ascii=False))
    print("--- script preview ---")
    print(out["script"][:400])
