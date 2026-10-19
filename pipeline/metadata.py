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
        f"Language: {lang} (write everything in this language). "
        "The narration must lead with a fascinating well-established fact about "
        "the subject (history/science/nutrition/practical) or a strong insight, "
        "then mix in opinion or a thought-provoking question. Never merely "
        "describe the on-screen visuals, never invent statistics. "
        "Return ONLY the narration text, no quotes, no labels."
    )
    return _chat(prompt, system=(
        "You write monetization-safe short-form voiceovers: value-adding "
        "commentary led by fascinating facts, never scene description."))


def make_metadata(title, description="", tags=None, lang="en"):
    tags = tags or []
    prompt = (
        "Given this video, return JSON with keys: title (≤95 chars, catchy), "
        "description (2-4 sentences + a few hashtags), tags (array of 8-15 short strings). "
        "Rules: title must use a distinct angle (fact-led, question-led, or bold hook), "
        "not a paraphrase of the source title; description must include at least one "
        "fascinating fact or piece of added context, never mention Facebook, "
        "reposting, compilation, or another platform. "
        f"Write output in language: {lang}. "
        f"Title: {title}. Description: {description[:400]}. "
        f"Existing tags: {', '.join(tags[:10])}."
    )
    raw = _chat(prompt, system=(
        "You are an SEO expert for short-form video. Monetization-safe framing: "
        "original angles, facts-first, no repost traces. Return valid JSON only."))
    data = _json_or_text(raw)
    if not data:
        return {"title": title[:95], "description": description[:480], "tags": tags[:15]}
    return {
        "title": str(data.get("title", title))[:95],
        "description": str(data.get("description", description))[:4800],
        "tags": [str(t) for t in (data.get("tags") or tags)[:15]],
    }
