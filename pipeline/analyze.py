"""Video analysis via NVIDIA Nemotron 3 Nano Omni (omni-modal).

Samples frames + audio, sends to hosted NIM, gets back a structured JSON
"edit plan" (segments, transforms, narration, caption, title/desc/hashtags/tags)
that the ffmpeg/Kokoro backend (apply.py + voice.py) executes.

Transformative floor is enforced so content is always altered enough to
reduce reused-content / copyright risk, even if the model returns sparse data.

Env: NVIDIA_API_KEY  (primary — Nemotron on build.nvidia.com)
     GEMINI_API_KEY  (2nd fallback — Google Gemini when Nemotron fails)
"""
import os
import io
import sys
import base64
import json
import subprocess
import re
import time
import requests
import apikeys

FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")
MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
API = "https://integrate.api.nvidia.com/v1/chat/completions"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_API = ("https://generativelanguage.googleapis.com/v1beta/models/"
              f"{GEMINI_MODEL}:generateContent")


def probe_duration(path):
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return None


def extract_frames(path, fps=1.0, max_frames=10, width=512):
    dur = probe_duration(path) or 0
    if dur <= 0:
        raise RuntimeError("cannot probe video duration")
    n = max(4, min(max_frames, int(dur * fps)))
    step = dur / n
    frames = []
    for i in range(n):
        t = i * step
        out = subprocess.run(
            [FFMPEG, "-y", "-ss", f"{t:.3f}", "-i", path,
             "-frames:v", "1", "-vf", f"scale={width}:-2",
             "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True,
        )
        if out.returncode != 0 or not out.stdout:
            continue
        b64 = base64.b64encode(out.stdout).decode("ascii")
        frames.append((round(t, 2), b64))
    return frames


def extract_audio(path, max_secs=20):
    dur = probe_duration(path) or 0
    if dur <= 0:
        return None
    t = min(dur, max_secs)
    out = subprocess.run(
        [FFMPEG, "-y", "-t", f"{t:.3f}", "-i", path,
         "-vn", "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le", "-f", "wav", "-"],
        capture_output=True,
    )
    if out.returncode != 0 or not out.stdout or len(out.stdout) < 1000:
        return None
    return base64.b64encode(out.stdout).decode("ascii")


PROMPT = """You are a professional short-form video editor and director.
You receive N labeled frames sampled from a video (each prefixed with timestamp)
AND the audio track. Use BOTH visuals and audio.

Analyze content, pacing, camera, lighting, audio mood, subject.
Then produce an EDIT PLAN the render engine will execute.
LANGUAGE (HARD RULE): EVERY output string — title, description, narration,
captions, hashtags, tags, notes — must be in ENGLISH, regardless of the
source video's language.

CRITICAL - VARIETY: Do NOT reuse the same effect/caption/narration for every video.
Each video must feel UNIQUE. A nature clip, action scene, cartoon, talking head,
product demo each get different treatment.

CRITICAL - TRANSFORMATIVE EDITS (copyright/reused-content safety):
Every segment MUST get real visual changes so the output is sufficiently distinct
from the source. Never leave a segment with speed=1.0, color=preset none.
Always apply: color grade, speed variation, sharpness. This is MANDATORY per segment.
NO ZOOM, NO CROP — zoom is always "none" and crop is always all zeros (full frame
kept so the main subject stays fully visible). Old source text is removed
separately via source_captions — never via crop.

AUDIO AWARENESS - decide "audio_type" by LISTENING:
  "vocal"  = spoken words: human/AI speech, movie dialogue, dubbing, narration, singing
  "asmr"   = ASMR: tapping, scratching, whispering-ambience, mouth sounds
  "music"  = background music/song only, no meaningful speech
  "sfx"    = sound effects / ambient noise only
  "silent" = no meaningful audio

Then set audio_mode + audio_volume (NEVER mute the original audio):
  "asmr" -> audio_mode "lower", audio_volume 0.35
  else   -> audio_mode "lower", audio_volume 0.55

VOICEOVER RULES (MONETIZATION-CRITICAL — reused-content safety):
YouTube reviewers reject "reused content": someone else's footage plus a voice
that only describes what is already visible on screen. Every narration line
MUST add value the viewer CANNOT get from the picture alone.
When a "VERIFIED FACTS FOR THIS VIDEO" block is attached with the request, it
REPLACES free fact generation: use ONLY those facts (rewrite them into the
narration as the block instructs), never swap in facts of your own.

FACT BACKBONE — pick from this menu (mix at least 2 kinds across the video):
- science/physics: WHY it works (e.g. "cutting along the grain gives the
  blade a cleaner path, so it slips less")
- nutrition/chemistry: what's inside (e.g. "yellow coconuts sit longer on
  the palm, so their water turns sweeter")
- origin/history/tradition: where the practice comes from
- craft/professional technique: what a pro does differently and what goes
  wrong for amateurs (e.g. "a dull edge crushes fibers instead of cutting")
- consumer utility: how to pick, store, or use the thing better
- myth-bust: "many think X — actually Y"
Every fact must be TRUE for THIS clip's exact subject (the fruit/tool/motion
shown). When unsure of a detail, phrase it qualitatively ("far more",
"naturally sweeter") or switch to opinion — NEVER invent numbers, studies,
percentages, or origins.

FACT QUALITY BAR (YouTube's reused-content reviewer decides on THIS):
Each narration line must be a DEEP transformative fact. Apply the
reviewer test: "Could a viewer derive this sentence just by looking at the
clip?" If YES — reject the line and pick a different angle.
- ❌ WEAK / visual-derived (gets flagged as minimal commentary):
  "The rind is smooth yet firm, indicating proper ripeness."
  "This variety thrives in warm climates, making it sweeter."
  Anything about color, shape, texture, or motion already visible.
- ✅ STRONG / invisible knowledge (safe): facts that NAME a concept,
  mechanism, origin, or history the frames cannot show — e.g. "yellow
  watermelons lack lycopene, the pigment that turns regular melons red."
  Chemistry/botany terms, where the practice originates, what professionals
  do differently and WHY, myth-busts that explain the real mechanism.
Aim for at least one named concept or mechanism across the narration lines. Never
pad with observation to hit the count — if the subject is thin, go for the
deepest true fact you know instead.

HOOK (mandatory — the FIRST narrated segment):
- Open with a hook: a direct question, a bold surprising claim, a myth-bust,
  or a sharp comparison. Use a DIFFERENT hook style than the obvious default
  for this subject — do NOT open every video with "Did you know...".
- The LAST narrated line: preferably a question or verdict that invites
  comments ("Would you drink it straight from the shell?").

CLICHÉ / ENGAGEMENT-BAIT — BANNED in narration, title, description,
hashtags, AND tags: "satisfying", "oddly satisfying", "you won't believe",
"wait for it", "watch till the end", "shocking", "insane", "pure
satisfaction", "ASMR lovers", "mind-blowing". Say the actual thing instead.
In hashtags/tags replace them with SPECIFIC search terms (e.g. fruit
preparation, knife technique, kitchen skills, food science, how to cut,
fresh produce) — precise terms rank better than generic bait.

VOICE: conversational — like a knowledgeable friend, with contractions
("it's", "you'll"). One idea per line. No textbook/listicle tone. Never
say "in this video we will see" and never address "guys".

Also welcome alongside facts:
- expert-style insight: what a professional notices in this exact motion
- a genuine opinion or verdict (e.g. "a dull knife would crush this, not cut it")
- light humor or a relatable human observation
BANNED in narration (fails monetization review):
- pure visual description of the scene ("the knife slices slowly", "watch the
  water pour out", "so satisfying to look at",
  "each layer comes off in thin, even strips", "the last peel reveals the
  coconut's interior", "the perfectly peeled coconut awaits...")
- restating the on-screen caption
SPACING & PACING (MANDATORY — ASMR quality + monetization):
- NARRATION BUDGET (hard rule): follow the narrated-segment count given with
  the source duration.
  * "asmr": exactly that count — one hook early, facts spread across the
    middle, one question at the end. ALL OTHER segments: narration ""
    (empty string). Exceeding the asmr budget REJECTS the plan.
    Natural cutting/scraping/splash audio must dominate (~70-80% of runtime).
  * general (vocal/music/sfx/silent): up to budget+2 narrated segments
    (hook -> build -> insight -> payoff). Remaining "" so audio breathes.
- NEVER narrate visible actions ("the hand grips", "the knife cuts", "the
  blade glides", "the inside is yellow"). Narration must state information
  NOT visible on screen: history, botany, nutrition, origin, technique WHY,
  consumer tips, myth-busting.
- Do NOT narrate every segment in either mode.
- Each spoken line: 10-15 words (under 3 seconds of speech).
- Arc (whole video length): hook/surprising fact early -> breathing room -> insight
  mid-video -> educational fact near the end -> final question/conclusion
  (ASMR uses the sparse subset of this arc; general videos cover it fully).
- OUTRO / AUDIO LOOP (hard rule): your LAST narrated line must END within
  the final 2-3 seconds of the video — place it on the FINAL segment (the
  one running to the very end). Never leave more than ~3 seconds of dead
  air after the last voiceover line (a long silent tail kills retention).
  Spread narration evenly: hook early, mid fact around the middle, outro
  question in the closing seconds — the voice must run to the end, never
  stop early. Keep pacing tight; no dragged silent stretches anywhere.
- Captions ONLY on the narrated segments (a <=5-word headline of the fact,
  e.g. "Yellow vs Green", "Blade Angle"). Every other segment: caption.text "".
Per audio_type (original audio levels unchanged):
- "vocal": original audio stays at 55% + OUR commentary on the key segments.
- "asmr": ADD sparse soft commentary ON TOP of lowered ASMR audio — the facts
  and insight carry the value, do NOT narrate the visuals.
- "music"/"sfx"/"silent": punchy commentary on the key segments only.

NARRATION MUST BE CLEAN:
No fake interjections ("Hahaha!", "Oh no no no--", "YO DID YOU SEE THAT?!").
No screams, no exaggerated reactions. Just real natural spoken words.

UNIQUENESS (MANDATORY):
Every video gets UNIQUE narration and captions. Never use generic template
phrases or lines you would give to any similar clip. Base the SUBJECT of each
line on the actual frames provided (angle, hand position, knife movement,
background details), then add commentary a viewer would not already know.
Each caption must differ from any caption you might give to a similar video.
Hook style must also differ across videos — never ship the same opener twice.

Return ONLY valid JSON (no markdown fences, no commentary):

{
  "audio_type": "vocal"|"asmr"|"music"|"sfx"|"silent",
  "audio_mode": "lower",
  "audio_volume": 0.55,
  "has_speech": true,
  "video_summary": "1-2 sentences",
  "style": "cinematic|energetic|dramatic|comedic|educational|suspense|uplifting|raw",
  "title": "<=90 chars YouTube Shorts title",
  "description": "2-3 sentences",
  "hashtags": ["#tag1","#tag2","#tag3"],
  "tags": ["8-15 short seo tags"],
  "source_captions": [
    {"top": 0.85, "bottom": 0.89, "kind": "text"}
  ],
  "segments": [
    {
      "index": 0,
      "start": 0.0,
      "end": 3.0,
      "action": "keep"|"remove"|"highlight",
      "speed": 1.10,
      "crop": {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0},
      "zoom": {"type": "none", "amount": 1.0},
      "sharpness": 0.10,
      "color": {"preset": "vivid"|"warm"|"cool"|"moody"|"noir"|"vintage"|"neon", "brightness": 0.0, "contrast": 1.06, "saturation": 1.12},
      "overlay": {"type": "none", "value": ""},
      "caption": {"text": "<=5 words on narrated segments, else empty", "position": "top"|"center"|"bottom", "style": "outline"|"box"|"bold"},
      "narration": "value-adding commentary (fact/insight/opinion/humor); empty=silent",
      "voice_style": {"speed": 1.0},
      "notes": "brief reason"
    }
  ],
  "global": {"intro_effect": "fade", "ending_effect": "fade", "voice_gain": 1.0}
}

Rules:
- segments cover timeline contiguously (first start=0, each start=previous
  end, LAST end == source duration — never stop early at half the clip).
- segment count target is given with the source duration (~4-6s each).
- speed: 1.0=normal, >1=faster. Vary per segment (1.08-1.15 typical).
- EVERY segment: zoom=none (MANDATORY), crop all zeros (MANDATORY — full frame,
  never crop), non-none color grade, speed != 1.0, sharpness 0.06-0.12 (subtle,
  never harsh). This is mandatory for copyright safety.
- crop: ALWAYS use exactly {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}.
- caption.text: <=5 words, ONLY on narrated segments (fact headline);
  "" on every other segment. Different wording each time.
- narration: value-adding commentary (fact, insight, opinion, humor, or a
  question) — NEVER a description of visible action, never a caption repeat.
  Count: use the NARRATION BUDGET given with the source duration ("asmr" ->
  exactly that many: hook, spread facts, end question; general -> up to
  budget+2 as a storyline). ALL other segments get "" (empty = silent,
  natural audio stays prominent). Never repeat a line in consecutive segments.
- source_captions: PERSISTENT burned-in text/logos/watermarks that belong to the
  ORIGINAL source video (old captions, channel names, "subscribe" overlays,
  watermarks, credit lines). They can appear ANYWHERE in the frame — top,
  middle, or bottom; the position is NEVER fixed. Report each distinct text
  band visible in MOST sampled frames as its vertical extent:
  "top"/"bottom" = frame fractions (0.0 = very top, 1.0 = very bottom),
  TIGHT bounds hugging the text (~0.005 padding only — never overestimate,
  the video is cropped exactly to these values). OUR new captions are added
  later — you only locate the source's own text. Ignore moving objects, subtitles that are
  part of a scene shot (e.g. movie dialogue burned by the same uploader counts
  ONLY if it is clearly a persistent overlay band). [] if nothing is burned in.
  Example: a two-line white credit at the lower area -> [{"top": 0.84, "bottom": 0.91, "kind": "text"}]
  same video with a small logo top-right -> [{"top": 0.04, "bottom": 0.10, "kind": "logo"}]
- title: distinct angle — ideally built around the strongest VERIFIED FACT
  of this video (e.g. "Why Street Vendors Tap Before Slicing", "The Secret
  Behind the Equatorial Crack"); question-led or bold-hook also OK — never
  a paraphrase of the source title.
- description: 2-3 genuine sentences explaining the context/facts (no generic
  spam), ACCURATE to what the frames actually show (never misdescribe a
  visible color/subject, e.g. red interior is not "green hue"). 3-5 real
  hashtags only, EVERY hashtag must match the actual subject
  of this video (watermelon video never gets #coconut). Never mention
  Facebook, reposting, or compilation.
- Do not invent visual facts. Base on frames + audio only.
"""


def _prompt():
    lang = os.getenv("VIDEO_LANG", "English")
    if lang == "English":
        return PROMPT
    return PROMPT.replace("must be in ENGLISH", f"must be in {lang}")


DEAD_CODES = (401, 402, 403, 429)

# Shared free endpoints return these under load; worth another shot before
# falling back to a plan with no narration/captions.
_TRANSIENT_HINTS = (
    "429", "500", "502", "503", "504", "resourceexhausted", "overloaded",
    "rate limit", "rate-limit", "too many requests", "timed out", "timeout",
    "connection", "temporarily unavailable", "capacity",
)


def _is_transient(err):
    s = str(err).lower()
    return any(h in s for h in _TRANSIENT_HINTS)


def _call_with_retry(fn, *args, **kwargs):
    """Retry a backend call on transient failures (429/5xx/timeouts)."""
    tries = int(os.getenv("ANALYZE_RETRIES", "4"))
    last = None
    for i in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            if i == tries - 1 or not _is_transient(e):
                raise
            wait = min(30, 5 * (i + 1))
            print(f"[analyze] transient failure ({e}); retry {i + 2}/{tries} in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
    raise last


def _keys(service, *env_names):
    return apikeys.keys(service, *env_names)


def _api_key():
    pool = _keys("nvidia", "NVIDIA_API_KEYS", "NVIDIA_API_KEY")
    if not pool:
        raise RuntimeError("NVIDIA_API_KEY not set")
    return pool[0]


def _call_nemotron(frames, title_hint="", duration=None, audio_b64=None, correction="", extra=""):
    content = [{"type": "text", "text": _prompt()}]
    if extra:
        content.append({"type": "text", "text": extra})
    if title_hint:
        content.append({"type": "text", "text": f"Source title/context: {title_hint}"})
    if duration:
        content.append({"type": "text", "text": _duration_note(duration)})
    if audio_b64:
        content.append({
            "type": "audio_url",
            "audio_url": {"url": f"data:audio/wav;base64,{audio_b64}"},
        })
        content.append({
            "type": "text",
            "text": "Audio attached. Listen to decide audio_type and narration.",
        })
    content.append({
        "type": "text",
        "text": f"Here are {len(frames)} timestamped frames. Produce the JSON edit plan now.",
    })
    for t, b64 in frames:
        content.append({"type": "text", "text": f"[t={t:.2f}s]"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    if correction:
        content.append({"type": "text", "text": correction})

    pool = _keys("nvidia", "NVIDIA_API_KEYS", "NVIDIA_API_KEY")
    if not pool:
        raise RuntimeError("NVIDIA_API_KEY not set")
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 8192,
        "temperature": 0.7,
        "top_p": 0.95,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    import time
    last = None
    for idx, key in enumerate(pool):
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        for attempt in range(3):
            try:
                r = requests.post(API, headers=headers, json=payload, timeout=300)
                if r.status_code in DEAD_CODES:
                    last = RuntimeError(f"HTTP {r.status_code}")
                    apikeys.report("nvidia", idx, False, f"HTTP {r.status_code}")
                    break
                if r.status_code >= 500:
                    last = RuntimeError(f"HTTP {r.status_code}")
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                apikeys.report("nvidia", idx, True)
                return r.json()["choices"][0]["message"]["content"]
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last = e
                time.sleep(5 * (attempt + 1))
        if isinstance(last, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            raise last
    raise last


def _gemini_keys():
    return _keys("gemini", "GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY")


def _gemini_key():
    pool = _gemini_keys()
    return pool[0] if pool else None


def _openrouter_keys():
    return _keys("openrouter", "OPENROUTER_API_KEYS", "OPENROUTER_API_KEY")


def _openrouter_key():
    pool = _openrouter_keys()
    return pool[0] if pool else None


def _call_gemini(frames, title_hint="", duration=None, audio_b64=None, correction="", extra=""):
    """2nd-choice backend: same multimodal prompt -> Google Gemini generateContent."""
    keys = _gemini_keys()
    if not keys:
        raise RuntimeError("GEMINI_API_KEY / GOOGLE_API_KEY not set")
    parts = [{"text": _prompt()}]
    if extra:
        parts.append({"text": extra})
    if title_hint:
        parts.append({"text": f"Source title/context: {title_hint}"})
    if duration:
        parts.append({"text": _duration_note(duration)})
    if audio_b64:
        parts.append({"inline_data": {"mime_type": "audio/wav", "data": audio_b64}})
        parts.append({"text": "Audio attached. Listen to decide audio_type and narration."})
    parts.append({"text": f"Here are {len(frames)} timestamped frames. Produce the JSON edit plan now."})
    for t, b64 in frames:
        parts.append({"text": f"[t={t:.2f}s]"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
    if correction:
        parts.append({"text": correction})
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.7,
            "topP": 0.95,
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    models = []
    for m in (GEMINI_MODEL, "gemini-flash-latest", "gemini-3.1-flash-lite"):
        if m and m not in models:
            models.append(m)
    url_base = "https://generativelanguage.googleapis.com/v1beta/models/"
    import time
    last = None
    for kidx, key in enumerate(keys):
        exhausted = False
        for model in models:
            url = f"{url_base}{model}:generateContent?key={key}"
            for attempt in range(3):
                try:
                    r = requests.post(url, json=payload, timeout=300)
                    if r.status_code == 400 and "thinkingConfig" in (r.text or ""):
                        payload.get("generationConfig", {}).pop("thinkingConfig", None)
                        continue
                    if r.status_code in DEAD_CODES:
                        last = RuntimeError(f"{model}: HTTP {r.status_code}: {(r.text or '')[:200]}")
                        apikeys.report("gemini", kidx, False, f"HTTP {r.status_code}")
                        if r.status_code == 429 and kidx == len(keys) - 1 and attempt < 2:
                            time.sleep(5 * (attempt + 1))
                            continue
                        exhausted = True
                        break
                    if r.status_code >= 500:
                        last = RuntimeError(f"{model}: HTTP {r.status_code}: {(r.text or '')[:200]}")
                        time.sleep(5 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    data = r.json()
                    cand = (data.get("candidates") or [{}])[0]
                    txt = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
                    if not txt:
                        raise RuntimeError(f"gemini empty response: {str(data)[:300]}")
                    apikeys.report("gemini", kidx, True)
                    return txt
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    last = e
                    time.sleep(5 * (attempt + 1))
                except requests.exceptions.HTTPError as e:
                    last = e
                    break
            if exhausted:
                break
    raise last


OR_FREE_MODELS = [
    "nvidia/nemotron-3.5-super-vl:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]


def _call_openrouter(frames, title_hint="", duration=None, correction="", model=None, extra=""):
    """3rd-choice backend: OpenRouter free video-capable models (image+video input;
    these have NO audio modality so audio_b64 is intentionally not attached)."""
    pool = _openrouter_keys()
    if not pool:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    content = [{"type": "text", "text": _prompt()}]
    if extra:
        content.append({"type": "text", "text": extra})
    if title_hint:
        content.append({"type": "text", "text": f"Source title/context: {title_hint}"})
    if duration:
        content.append({"type": "text", "text": _duration_note(duration)})
    content.append({
        "type": "text",
        "text": ("Audio track is NOT attached for this model. Infer audio_type "
                 "(asmr/vocal/music/sfx/silent) from visuals, motion and source title."),
    })
    content.append({
        "type": "text",
        "text": f"Here are {len(frames)} timestamped frames. Produce the JSON edit plan now.",
    })
    for t, b64 in frames:
        content.append({"type": "text", "text": f"[t={t:.2f}s]"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    if correction:
        content.append({"type": "text", "text": correction})
    payload = {
        "model": model or OR_FREE_MODELS[0],
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 8192,
        "temperature": 0.7,
    }
    import time
    last = None
    for kidx, key in enumerate(pool):
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        for attempt in range(3):
            try:
                r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                                  headers=headers, json=payload, timeout=300)
                if r.status_code in DEAD_CODES:
                    last = RuntimeError(f"{payload['model']}: HTTP {r.status_code}: {(r.text or '')[:200]}")
                    apikeys.report("openrouter", kidx, False, f"HTTP {r.status_code}")
                    if r.status_code == 429 and kidx == len(pool) - 1 and attempt < 2:
                        time.sleep(5 * (attempt + 1))
                        continue
                    break
                if r.status_code >= 500:
                    last = RuntimeError(f"{payload['model']}: HTTP {r.status_code}: {(r.text or '')[:200]}")
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                txt = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                if not txt:
                    raise RuntimeError(f"openrouter empty response: {str(data)[:300]}")
                apikeys.report("openrouter", kidx, True)
                return txt
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last = e
                time.sleep(5 * (attempt + 1))
    raise last


CLASSIFY_PROMPT = (
    "Look at these video frames and return ONLY minified JSON, no explanation: "
    '{"subject":"<main physical subject, 1-2 lowercase words>",'
    '"category":"<exactly one of: coconut,watermelon,food,animals,asmr,other>",'
    '"context":"<what is ACTUALLY happening in <=8 lowercase words, e.g. '
    'fresh coconut water being poured from a cut shell>"}'
)
_CLASSIFY_CATS = {"coconut", "watermelon", "food", "animals", "asmr", "other"}


def _norm_class(raw):
    d = _extract_json(raw)
    cat = str(d.get("category") or "").strip().lower()
    sub = str(d.get("subject") or "").strip().lower()
    ctx = str(d.get("context") or "").strip().lower()
    if cat not in _CLASSIFY_CATS:
        cat = "other"
    if not sub or len(sub) > 40:
        sub = ""
    ctx = " ".join(ctx.split())[:80]
    return {"subject": sub, "category": cat, "context": ctx}


def _classify_nemotron(frames):
    content = [{"type": "text", "text": CLASSIFY_PROMPT}]
    for t, b64 in frames:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 80,
        "temperature": 0,
    }
    r = requests.post(API, headers={"Authorization": f"Bearer {_api_key()}",
                                    "Content-Type": "application/json"},
                      json=payload, timeout=90)
    r.raise_for_status()
    return _norm_class(r.json()["choices"][0]["message"]["content"])


def _classify_gemini(frames):
    key = _gemini_key()
    if not key:
        raise RuntimeError("GEMINI/GOOGLE key not set")
    parts = [{"text": CLASSIFY_PROMPT}]
    for t, b64 in frames:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-flash-latest:generateContent?key=" + key)
    r = requests.post(url, json={
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"maxOutputTokens": 80, "temperature": 0},
    }, timeout=90)
    r.raise_for_status()
    cand = (r.json().get("candidates") or [{}])[0]
    txt = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
    if not txt:
        raise RuntimeError("gemini classify empty")
    return _norm_class(txt)


def _classify_openrouter(frames):
    key = _openrouter_key()
    if not key:
        raise RuntimeError("OPENROUTER key not set")
    content = [{"type": "text", "text": CLASSIFY_PROMPT}]
    for t, b64 in frames:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      json={"model": OR_FREE_MODELS[0],
                            "messages": [{"role": "user", "content": content}],
                            "max_tokens": 80, "temperature": 0}, timeout=90)
    r.raise_for_status()
    txt = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not txt:
        raise RuntimeError("openrouter classify empty")
    return _norm_class(txt)


def _classify_subject(frames):
    """Stage-1 micro analysis: 2-3 frames, no audio -> {subject, category}.
    Provider failover Nemotron -> Gemini -> OpenRouter; None if all fail."""
    if not frames:
        return None
    step = max(1, len(frames) // 3)
    pick = frames[::step][:3]
    for fn in (_classify_nemotron, _classify_gemini, _classify_openrouter):
        try:
            res = fn(pick)
            print(f"[facts] micro {fn.__name__} -> {res}")
            return res
        except Exception as e:
            print(f"[facts] micro {fn.__name__} failed ({e})", file=sys.stderr)
    return None


def _extract_json(text):
    if not text:
        raise RuntimeError("empty model response")
    try:
        return json.loads(text)
    except Exception:
        pass
    t = text.strip()
    if "```" in t:
        parts = t.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                try:
                    return json.loads(p)
                except Exception:
                    continue
    # Balanced-brace extraction: find first { ... } with matching braces.
    # Handles responses where valid JSON is followed by model garbage.
    start = t.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            ch = t[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = t[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
        start = t.find("{", start + 1)
    # Regex fallback
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # Debug: save raw response for inspection
    try:
        import tempfile, time
        dbg = os.path.join(tempfile.gettempdir(), f"nemotron_raw_{int(time.time())}.txt")
        with open(dbg, "w", encoding="utf-8") as f:
            f.write(text or "")
        print(f"[analyze] raw response saved to {dbg} (len={len(text or '')})")
    except Exception:
        pass
    raise RuntimeError("could not parse JSON from model response")


PRESETS = ["vivid", "warm", "cool", "moody"]

# No transformation crop — keep the full frame (subjects stay framed).
# Only the old-caption band (caption_regions -> caption_crop_top/bottom in
# apply.py) is cropped, exactly as tight as the detected text rows.
FIXED_CROP = {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}


def _transform_floor(seg, idx):
    """Enforce transformative minimums on a segment (copyright safety).
    No zoom, no crop (full frame kept). Speed/color/sharpness vary."""
    if abs(seg["speed"] - 1.0) < 0.02:
        seg["speed"] = round(1.08 + (idx % 5) * 0.015, 3)
    # No zoom — always none.
    seg["zoom"] = {"type": "none", "amount": 1.0}
    # No crop — full frame; old text is removed only via caption_regions.
    seg["crop"] = {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}
    if seg["color"]["preset"] == "none":
        seg["color"]["preset"] = PRESETS[idx % len(PRESETS)]
    if seg["color"]["saturation"] < 1.06:
        seg["color"]["saturation"] = 1.12
    if seg["color"]["contrast"] < 1.03:
        seg["color"]["contrast"] = 1.06
    if seg["sharpness"] < 0.06:
        seg["sharpness"] = 0.08


def _fallback_plan(duration, title_hint=""):
    dur = duration or 15.0
    n = _seg_target(dur)
    seg_len = dur / n
    segs = []
    for i in range(n):
        segs.append({
            "index": i,
            "start": round(i * seg_len, 2),
            "end": round((i + 1) * seg_len, 2),
            "action": "keep",
            "speed": 1.0,
            "crop": {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0},
            "zoom": {"type": "none", "amount": 1.0},
            "sharpness": 0.0,
            "color": {"preset": "none", "brightness": 0.0, "contrast": 1.0, "saturation": 1.0},
            "overlay": {"type": "none", "value": ""},
            "caption": {"text": "", "position": "bottom", "style": "outline"},
            "narration": "",
            "voice_style": {"speed": 1.0},
            "notes": "fallback",
        })
    return {
        "audio_type": "music",
        "audio_mode": "lower",
        "audio_volume": 0.55,
        "has_speech": False,
        "video_summary": title_hint or "clip",
        "style": "energetic",
        "title": (title_hint or "Check this out")[:90],
        "description": title_hint or "New clip",
        "hashtags": ["#shorts", "#viral", "#trending"],
        "tags": ["shorts", "viral", "trending", "video", "edit", "content", "watch"],
        "caption_regions": [],
        "segments": segs,
        "global": {"intro_effect": "fade", "ending_effect": "fade", "voice_gain": 1.0},
    }


def _narr_target(duration, audio_type="asmr"):
    """Narrated-segment budget scaled to clip length — works for ANY random
    1-200s source: very short clips get 1-0 lines (a 4s video cannot hold
    even one 10-15 word line), long clips get more."""
    d = float(duration or 30.0)
    if d <= 5:
        return 0
    if (audio_type or "").lower() == "asmr":
        if d <= 8:
            return 1
        if d <= 15:
            return 2
        if d <= 45:
            return 3
        if d <= 90:
            return 4
        if d <= 150:
            return 6
        return 8
    if d <= 10:
        return 1
    if d <= 17:
        return 2
    if d <= 26:
        return 3
    if d <= 36:
        return 4
    if d <= 45:
        return 5
    if d <= 90:
        return 6
    if d <= 150:
        return 8
    return 10


def _seg_target(duration):
    """Segment count target (~5s each) — tiny clips get fewer segments so
    narration windows still fit inside the source."""
    dur = float(duration or 30.0)
    n = int(round(dur / 5.0))
    return max(3 if dur < 15 else 8, min(40, n))


def _duration_note(duration):
    """Duration line sent to the AI: segment count + narration budget."""
    dur = float(duration or 30.0)
    n_seg = _seg_target(dur)
    n = _narr_target(dur, "asmr")
    n2 = n + (2 if dur > 45 else (1 if dur > 25 else 0))
    zero = (" (0 = leave EVERY segment's narration empty for this clip)"
            if n == 0 else "")
    return (
        f"Source duration: {dur:.1f}s. "
        f"SEGMENT TARGET: about {n_seg} segments covering the FULL "
        f"duration (first start=0.0, last end=={dur:.1f}s — NEVER stop early at "
        f"half the clip). NARRATION BUDGET (hard): exactly {n} narrated segments "
        f"if audio_type=asmr, {n} to {n2} if general (hook first, "
        f"question last, rest spread evenly across the whole video){zero}."
    )


def _min_narration(audio_type, duration=None):
    if duration:
        return _narr_target(duration, audio_type)
    return 3 if (audio_type or "").lower() == "asmr" else 5


def _narration_count(plan):
    segs = plan.get("segments") or []
    return sum(1 for s in segs if (s.get("narration") or "").strip())


def _narration_fix_note(plan, duration=None):
    at = (plan.get("audio_type") or "asmr").lower()
    need = _min_narration(at, duration)
    return (
        "CORRECTION: your last plan put narration on too few segments. "
        f"audio_type={at} REQUIRES at least {need} narrated segments "
        "(follow the NARRATION BUDGET given with the source duration), "
        "each narration 10-15 words of factual spoken line. "
        "All other segments must have narration=\"\". "
        "Captions allowed ONLY on narrated segments (short <=5-word fact headline); "
        "all other segments must have caption.text=\"\". "
        "REWRITE the full segments array now and output only the JSON."
    )


def _normalize(plan, duration):
    dur = duration or 0
    segs = plan.get("segments") or []
    clean = []
    for i, s in enumerate(segs):
        try:
            start = max(0.0, float(s.get("start", 0)))
            end = float(s.get("end", start + 1))
            if end <= start:
                end = start + 1.0
            if dur and start >= dur:
                continue
            if dur:
                end = min(end, dur)
            speed = min(2.0, max(0.5, float(s.get("speed", 1.0))))
            crop = s.get("crop") or {}
            zoom = s.get("zoom") or {}
            ztype = zoom.get("type", "none")
            zamt = min(1.4, max(1.0, float(zoom.get("amount", 1.0))))
            sharp = min(0.4, max(0.0, float(s.get("sharpness", 0))))
            color = s.get("color") or {}
            caption = s.get("caption") or {}
            narration = (s.get("narration") or "").strip()
            if not narration:
                caption = {}
            vs = s.get("voice_style") or {}
            vspeed = min(1.05, max(0.85, float(vs.get("speed", 1.0))))
            clean.append({
                "index": len(clean),
                "start": round(start, 3),
                "end": round(end, 3),
                "action": s.get("action", "keep"),
                "speed": round(speed, 3),
                "crop": {
                    "top": min(0.15, max(0.0, float(crop.get("top", 0)))),
                    "bottom": min(0.15, max(0.0, float(crop.get("bottom", 0)))),
                    "left": min(0.15, max(0.0, float(crop.get("left", 0)))),
                    "right": min(0.15, max(0.0, float(crop.get("right", 0)))),
                },
                "zoom": {"type": ztype if ztype in ("in", "out", "none") else "none", "amount": round(zamt, 3)},
                "sharpness": round(sharp, 3),
                "color": {
                    "preset": color.get("preset", "none"),
                    "brightness": min(0.5, max(-0.5, float(color.get("brightness", 0)))),
                    "contrast": min(1.8, max(0.5, float(color.get("contrast", 1.0)))),
                    "saturation": min(1.8, max(0.5, float(color.get("saturation", 1.0)))),
                },
                "overlay": s.get("overlay") or {"type": "none", "value": ""},
                "caption": {
                    "text": (caption.get("text") or "")[:80],
                    "position": caption.get("position", "bottom"),
                    "style": caption.get("style", "outline"),
                },
                "voice_style": {"speed": round(vspeed, 3)},
                "narration": narration[:400],
                "notes": s.get("notes", ""),
            })
        except Exception:
            continue
    if not clean:
        clean = _fallback_plan(dur)["segments"]
    # FULL-SOURCE COVERAGE (issue A): AI plans often cover only the first
    # half of the clip (e.g. 42s of an 82s source) — the upload then lost
    # the entire second half. Force start at 0, close every internal gap,
    # stretch proportionally so the plan spans 0..source duration, and pin
    # the last end to EXACTLY the source (millisecond precision) so the
    # video always ends on the source's real end — any random 1-200s clip.
    if dur and clean:
        clean[0]["start"] = 0.0
        for i in range(1, len(clean)):
            if clean[i]["start"] - clean[i - 1]["end"] > 0.0005:
                clean[i - 1]["end"] = round(clean[i]["start"], 3)
        last_end = clean[-1]["end"]
        if last_end < dur - 0.05 and last_end > 0.5:
            f = dur / last_end
            for s in clean:
                s["start"] = round(s["start"] * f, 3)
                s["end"] = round(s["end"] * f, 3)
            print(f"[analyze] coverage stretched x{f:.3f} "
                  f"({last_end:.1f}s -> {dur:.1f}s)")
        clean[-1]["end"] = round(dur, 3)
        for s in clean:
            if s["end"] > dur:
                s["end"] = round(dur, 3)
    for idx, seg in enumerate(clean):
        _transform_floor(seg, idx)
    plan["segments"] = clean

    audio_type = (plan.get("audio_type") or "").lower()
    if audio_type not in ("vocal", "asmr", "music", "sfx", "silent"):
        audio_type = "vocal" if plan.get("has_speech") else "music"
    plan["audio_type"] = audio_type
    # Original audio is NEVER muted: ASMR 40%, everything else 55%.
    plan["audio_mode"] = "lower"
    plan["audio_volume"] = 0.40 if audio_type == "asmr" else 0.55
    plan["has_speech"] = audio_type == "vocal"
    # HARD TRIM: asmr must stay sparse — if the model over-narrated, keep
    # only the NARRATION BUDGET worth of lines, evenly spread over the clip.
    narr_idx = [i for i, s in enumerate(clean) if s["narration"].strip()]
    n_tgt = _narr_target(dur, audio_type) if dur else (3 if audio_type == "asmr" else 5)
    max_n = 0 if n_tgt == 0 else n_tgt + 1
    if len(narr_idx) > max_n:
        k = min(n_tgt, len(narr_idx))
        if k <= 0:
            keep = set()
        elif k == 1:
            keep = {narr_idx[0]}
        else:
            keep = {narr_idx[round(i * (len(narr_idx) - 1) / (k - 1))]
                    for i in range(k)}
        dropped = 0
        for i in narr_idx:
            if i not in keep:
                clean[i]["narration"] = ""
                clean[i]["caption"]["text"] = ""
                dropped += 1
        if dropped:
            print(f"[analyze] trimmed narration on {dropped} segs "
                  f"(kept {len(keep)} of {len(narr_idx)}, budget={n_tgt})")
    # Narrated segments need room for the spoken line (~10-15 words = ~3s).
    # Steal time from the neighbour segment (keeps timeline contiguous) so
    # the final question/hook never gets clipped in the rendered audio.
    for i, seg in enumerate(clean):
        if not seg["narration"].strip():
            continue
        need = 3.0 - (seg["end"] - seg["start"])
        if need <= 0:
            continue
        if i > 0:
            prev = clean[i - 1]
            take = min(need, max(0.0, (prev["end"] - prev["start"]) - 1.0))
            if take > 0:
                prev["end"] = round(prev["end"] - take, 3)
                seg["start"] = prev["end"]
        if seg["end"] - seg["start"] >= 3.0:
            continue
        if i + 1 < len(clean):
            nxt = clean[i + 1]
            take = min(3.0 - (seg["end"] - seg["start"]),
                       max(0.0, (nxt["end"] - nxt["start"]) - 1.0))
            if take > 0:
                seg["end"] = round(seg["end"] + take, 3)
                nxt["start"] = seg["end"]
    # OUTRO ENFORCEMENT (feedback): a long silent tail after the last
    # voiceover line tanks retention — the closing line must land in the
    # final seconds. If the last narration ends early, MOVE it onto the
    # final segment so the voice runs to the very end of the video.
    if clean and dur:
        nidx = [i for i, s in enumerate(clean) if s["narration"].strip()]
        if nidx:
            last_i = nidx[-1]
            tail = dur - clean[last_i]["end"]
            tgt = clean[-1]
            if tail > 3.5 and tgt is not clean[last_i] \
                    and not tgt["narration"].strip():
                need = 3.0 - (tgt["end"] - tgt["start"])
                if need > 0 and len(clean) > 1:
                    prev = clean[-2]
                    take = min(need, max(0.0, (prev["end"] - prev["start"]) - 1.0))
                    if take > 0:
                        prev["end"] = round(prev["end"] - take, 3)
                        tgt["start"] = prev["end"]
                if tgt["end"] - tgt["start"] >= 2.0:
                    src = clean[last_i]
                    cap = src.get("caption") or {}
                    tgt["narration"] = src["narration"][:400]
                    tgt["caption"] = {
                        "text": (cap.get("text") or "")[:80],
                        "position": cap.get("position", "bottom"),
                        "style": cap.get("style", "outline"),
                    }
                    src["narration"] = ""
                    src["caption"]["text"] = ""
                    print(f"[analyze] outro moved to final segment "
                          f"(was {tail:.1f}s silent tail)")
            elif tail > 3.5 and tgt is clean[last_i]:
                # Final segment IS the outro but ends before video end:
                # stretch it to the end (pull start back for room).
                need = 3.0 - (tgt["end"] - tgt["start"])
                if need > 0 and len(clean) > 1:
                    prev = clean[-2]
                    take = min(need, max(0.0, (prev["end"] - prev["start"]) - 1.0))
                    if take > 0 and not prev["narration"].strip():
                        prev["end"] = round(prev["end"] - take, 3)
                        tgt["start"] = prev["end"]
                tgt["end"] = round(dur, 3)
                print(f"[analyze] outro segment stretched to video end")
    # AI-observed source text (any position) -> regions to remove; [] if none.
    import smart as _smart
    plan["caption_regions"] = _smart.normalize_source_captions(plan.get("source_captions"))
    plan.pop("source_captions", None)
    # Hard strip: cliché/bait words never ship in metadata — specific terms
    # (knife technique, fruit preparation, ...) rank better anyway.
    banned_meta = ("satisfying", "oddly satisfying", "you won't believe",
                   "wait for it", "watch till the end", "shocking", "insane",
                   "pure satisfaction", "asmr lovers", "mind-blowing")

    def _clean_meta(vals):
        out = []
        for v in vals or []:
            s = str(v).strip()
            low = s.lower().lstrip("#")
            if any(b in low for b in banned_meta):
                continue
            out.append(s)
        return out

    plan["hashtags"] = _clean_meta(plan.get("hashtags"))
    plan["tags"] = _clean_meta(plan.get("tags"))

    g = plan.get("global") or {}
    if audio_type == "asmr":
        # ASMR: voiceover at 100% (1.0), original audio at 40%.
        vgain = 1.0
    else:
        # Non-ASMR: original stays at 55%, voiceover boosted to stay clearly audible.
        vgain = min(2.5, max(1.8, float(g.get("voice_gain", 2.0))))
    plan["global"] = {
        "intro_effect": g.get("intro_effect", "fade"),
        "ending_effect": g.get("ending_effect", "fade"),
        "voice_gain": vgain,
    }
    return plan


def analyze_video(path, title_hint="", fps=1.0, max_frames=10, allow_fallback=True):
    dur = probe_duration(path)
    frames = extract_frames(path, fps=fps, max_frames=max_frames)
    if not frames:
        if allow_fallback:
            return _normalize(_fallback_plan(dur, title_hint), dur)
        raise RuntimeError("no frames extracted")
    audio_b64 = extract_audio(path)
    fact_note = ""
    try:
        import facts as _vault
        cls = _classify_subject(frames)
        subj = ""
        cat = ""
        ctx = ""
        if cls:
            subj = (cls.get("subject") or "").strip()
            cat = (cls.get("category") or "").strip()
            ctx = (cls.get("context") or "").strip()
            print(f"[facts] micro subject={subj!r} category={cat!r} "
                  f"context={ctx!r}", file=sys.stderr)
        n_f = _narr_target(dur, "asmr") if dur else 3
        fact_note = _vault.build_note(title_hint=title_hint,
                                      video_key=title_hint or path,
                                      subject=subj, context=ctx, n=n_f)
        if not fact_note and cat and cat != "other":
            fact_note = _vault.build_note(title_hint=title_hint,
                                          video_key=title_hint or path,
                                          subject=cat, context=ctx, n=n_f)
    except Exception as e:
        print(f"[facts] vault skipped ({e})", file=sys.stderr)
    # Near-duplicate title prevention: tell every AI backend which recent
    # titles are already used so each video gets a distinct angle/opening.
    try:
        import metadata as _md
        _recent = _md.load_history(15)
        if _recent:
            fact_note += (
                "\nTITLE RULE: The plan title must NOT repeat or lightly rephrase any "
                "of these recent titles — pick a clearly different angle and different "
                "opening words: " + " | ".join(_recent)
            )
    except Exception:
        pass
    plan = None
    first_err = None
    corr = ""
    if _keys("nvidia", "NVIDIA_API_KEYS", "NVIDIA_API_KEY"):
        for attempt in (0, 1):
            try:
                raw = _call_with_retry(_call_nemotron, frames, title_hint=title_hint, duration=dur,
                                       audio_b64=audio_b64, correction=corr if attempt else "",
                                       extra=fact_note)
                cand = _extract_json(raw)
                n = _narration_count(cand)
                need = _min_narration(cand.get("audio_type"), dur)
                if n < need:
                    print(f"[analyze] nemotron narration {n} < {need}; corrective retry",
                          file=sys.stderr)
                    corr = _narration_fix_note(cand, dur)
                    continue
                plan = cand
                print("[analyze] nemotron ok")
                break
            except Exception as e:
                first_err = f"nemotron: {e}"
                print(f"[analyze] nemotron failed ({e}); trying gemini", file=sys.stderr)
                break
    else:
        print("[analyze] no NVIDIA_API_KEY; trying gemini")
    if plan is None:
        if _gemini_key():
            for attempt in (0, 1):
                try:
                    raw = _call_with_retry(_call_gemini, frames, title_hint=title_hint, duration=dur,
                                           audio_b64=audio_b64, correction=corr if attempt else "",
                                           extra=fact_note)
                    cand = _extract_json(raw)
                    n = _narration_count(cand)
                    need = _min_narration(cand.get("audio_type"), dur)
                    if n < need:
                        print(f"[analyze] gemini narration {n} < {need}; corrective retry",
                              file=sys.stderr)
                        corr = _narration_fix_note(cand, dur)
                        continue
                    plan = cand
                    print("[analyze] gemini ok")
                    break
                except Exception as e:
                    first_err = f"{first_err}; gemini: {e}" if first_err else f"gemini: {e}"
                    print(f"[analyze] gemini failed ({e}); using simple fallback", file=sys.stderr)
                    break
        else:
            print("[analyze] no GEMINI/GOOGLE API key; skipping to openrouter")
    if plan is None and _openrouter_key():
        for or_model in OR_FREE_MODELS:
            for attempt in (0, 1):
                try:
                    raw = _call_with_retry(_call_openrouter, frames, title_hint=title_hint,
                                           duration=dur, correction=corr if attempt else "",
                                           model=or_model, extra=fact_note)
                    cand = _extract_json(raw)
                    n = _narration_count(cand)
                    need = _min_narration(cand.get("audio_type"), dur)
                    if n < need:
                        print(f"[analyze] openrouter {or_model} narration {n} < {need}; "
                              "corrective retry", file=sys.stderr)
                        corr = _narration_fix_note(cand, dur)
                        continue
                    plan = cand
                    print(f"[analyze] openrouter ok ({or_model})")
                    break
                except Exception as e:
                    first_err = f"{first_err}; openrouter({or_model}): {e}" if first_err \
                        else f"openrouter({or_model}): {e}"
                    print(f"[analyze] openrouter {or_model} failed ({e})", file=sys.stderr)
                    break
            if plan is not None:
                break
    if plan is None:
        if allow_fallback:
            return _normalize(_fallback_plan(dur, title_hint), dur)
        raise RuntimeError(f"all AI backends failed ({first_err})")
    plan = _normalize(plan, dur)
    print(f"[analyze] {len(plan['segments'])} segs, audio={plan['audio_type']}, "
          f"mode={plan['audio_mode']}, style={plan.get('style')}, "
          f"narrated={_narration_count(plan)}")
    return plan
