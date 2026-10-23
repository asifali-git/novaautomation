"""AI metadata (title/description/tags/script) via OpenRouter."""
import os
import json
import re
import requests
import apikeys

URL = "https://openrouter.ai/api/v1/chat/completions"


def _lang(lang=None):
    return lang or os.getenv("VIDEO_LANG", "en")


# Rolling history of recent upload titles (committed like facts vault so every
# GH run sees previous titles) — used to kill near-duplicate YT titles.
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "title_history.json")
_STOP = {
    "the", "a", "an", "of", "in", "on", "to", "for", "and", "with", "is", "it",
    "this", "that", "how", "why", "what", "when", "who", "at", "by", "from",
    "your", "you", "my", "me", "vs", "before", "after", "into", "over", "its",
}


def _words(text):
    return [w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in _STOP]


def load_history(limit=40):
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(t) for t in data][:limit]
    except Exception:
        pass
    return []


def remember_title(title):
    t = (title or "").strip()
    if not t:
        return
    hist = load_history(limit=200)
    if any(t.lower() == h.lower() for h in hist):
        return
    hist.insert(0, t)
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist[:60], f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def is_near_dup(title, recent=None):
    """True when `title` repeats/rephrases any recent title."""
    recent = load_history() if recent is None else recent
    tw = _words(title)
    if not tw:
        return True
    tset = set(tw)
    for r in recent:
        rw = _words(r)
        if not rw:
            continue
        # identical opening (first 4 significant words) = dup even if tail differs
        if tw[:4] == rw[:4] and len(tw) >= 3 and len(rw) >= 3:
            return True
        rset = set(rw)
        if len(tset & rset) / len(tset | rset) >= 0.55:
            return True
    return False


def make_title(old_title, avoid=None, context="", lang=None):
    """Rewrite a title with a completely different angle. Returns title text only."""
    prompt = (
        "Rewrite this short-form video title with a COMPLETELY DIFFERENT angle "
        "and different opening words (not the same hook, not a rephrase). "
        "<=95 chars, catchy, fact-led or question-led. "
        f"Language: {_lang(lang)}. Return ONLY the title text, no quotes, no labels. "
        f"Old title: {old_title}."
    )
    if context:
        prompt += f" Video context: {context[:300]}."
    if avoid:
        prompt += (
            " These recent titles were already used — never repeat or rephrase them: "
            + " | ".join(avoid[:15])
        )
    raw = _chat(prompt, system=(
        "You write distinct, monetization-safe YouTube Shorts titles. Return only the title."))
    return raw.strip().strip('"').strip()[:95]


def ensure_unique_title(title, description="", tags=None, video_key=None):
    """Guarantee a non-duplicate title. Remembers the final result."""
    title = (title or "").strip()[:95]
    recent = load_history()
    if title and not is_near_dup(title, recent):
        remember_title(title)
        return title
    print(f"[meta] near-duplicate title detected: {title!r}; rewriting", flush=True)
    if apikeys.keys("openrouter", "OPENROUTER_API_KEYS", "OPENROUTER_API_KEY"):
        for _ in range(2):
            try:
                cand = make_title(title, avoid=recent,
                                  context=(description or "")[:300])
                if cand and not is_near_dup(cand, recent):
                    remember_title(cand)
                    return cand
                if cand:
                    title = cand
            except Exception as e:
                print(f"[warn] title rewrite failed: {e}", flush=True)
                break
    # Deterministic fallback — guaranteed unique.
    base = re.sub(r"\s+#+\S+$", "", title).strip()[:80] or "Short"
    key = str(video_key) if video_key else str(len(load_history()) + 1)
    cand = f"{base} #{key}"[:95]
    remember_title(cand)
    return cand


def _key():
    pool = apikeys.keys("openrouter", "OPENROUTER_API_KEYS", "OPENROUTER_API_KEY")
    if not pool:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    return pool[0]


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


def make_script(title, description="", lang=None, secs=20):
    """Short voiceover script ~`secs` seconds (≈2.5 words/sec)."""
    n = int(secs * 2.5)
    prompt = (
        f"Write a punchy {n}-word voiceover narration for a short-form video. "
        f"Title: {title}. Context: {description[:400]}. "
        f"Language: {_lang(lang)} (write everything in this language). "
        "The narration must lead with a fascinating well-established fact about "
        "the subject (history/science/nutrition/practical) or a strong insight, "
        "then mix in opinion or a thought-provoking question. Never merely "
        "describe the on-screen visuals, never invent statistics. "
        "Return ONLY the narration text, no quotes, no labels."
    )
    return _chat(prompt, system=(
        "You write monetization-safe short-form voiceovers: value-adding "
        "commentary led by fascinating facts, never scene description."))


def make_metadata(title, description="", tags=None, lang=None):
    tags = tags or []
    prompt = (
        "Given this video, return JSON with keys: title (≤95 chars, catchy), "
        "description (2-4 sentences + a few hashtags), tags (array of 8-15 short strings). "
        "Rules: title must use a distinct angle (fact-led, question-led, or bold hook), "
        "not a paraphrase of the source title; description must include at least one "
        "fascinating fact or piece of added context, never mention Facebook, "
        "reposting, compilation, or another platform. "
        f"Write output in language: {_lang(lang)}. "
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
