"""AI metadata (title/description/tags/script) via OpenRouter."""
import os
import json
import requests

URL = "https://openrouter.ai/api/v1/chat/completions"


def _key():
    k = os.getenv("OPENROUTER_API_KEY")
    if not k:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    return k


def _chat(prompt, system="", model=None):
    model = model or os.getenv("AI_MODEL", "openai/gpt-4o-mini")
    headers = {
        "Authorization": f"Bearer {_key()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system or "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 800,
    }
    r = requests.post(URL, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _json_or_text(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except Exception:
        return None


def make_script(title, description="", lang="en", secs=20):
    """Short voiceover script ~`secs` seconds (≈2.5 words/sec)."""
    n = int(secs * 2.5)
    prompt = (
        f"Write a punchy {n}-word voiceover narration for a short-form video. "
        f"Title: {title}. Context: {description[:400]}. "
        f"Language: {lang}. Return ONLY the narration text, no quotes, no labels."
    )
    return _chat(prompt, system="You write viral short-form voiceover scripts.")


def make_metadata(title, description="", tags=None, lang="en"):
    tags = tags or []
    prompt = (
        "Given this video, return JSON with keys: title (≤95 chars, catchy), "
        "description (2-4 sentences + a few hashtags), tags (array of 8-15 short strings). "
        f"Title: {title}. Description: {description[:400]}. "
        f"Existing tags: {', '.join(tags[:10])}. Language: {lang}."
    )
    raw = _chat(prompt, system="You are an SEO expert for short-form video. Return valid JSON only.")
    data = _json_or_text(raw)
    if not data:
        return {"title": title[:95], "description": description[:480], "tags": tags[:15]}
    return {
        "title": str(data.get("title", title))[:95],
        "description": str(data.get("description", description))[:4800],
        "tags": [str(t) for t in (data.get("tags") or tags)[:15]],
    }
