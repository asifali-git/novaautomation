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
import requests

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

HOOK (mandatory — the FIRST narrated segment):
- Open with a hook: a direct question, a bold surprising claim, a myth-bust,
  or a sharp comparison. Use a DIFFERENT hook style than the obvious default
  for this subject — do NOT open every video with "Did you know...".
- The LAST narrated line: preferably a question or verdict that invites
  comments ("Would you drink it straight from the shell?").

CLICHÉ / ENGAGEMENT-BAIT — BANNED in narration, title, and description:
"satisfying", "oddly satisfying", "you won't believe", "wait for it",
"watch till the end", "shocking", "insane", "pure satisfaction",
"ASMR lovers", "mind-blowing". Say the actual thing instead.

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
- Narration count depends on audio_type (decide after listening):
  * "asmr": ONLY 3-4 narrated segments in the entire video. ALL other
    segments: narration "" (empty). The natural cutting/scraping/splash
    audio must dominate (~80-90% of the runtime) — ASMR viewers came for
    the sound, not a talking robot.
  * "vocal" / "music" / "sfx" / "silent" (general/informative videos):
    5-8 narrated segments forming a storyline — hook -> build -> expert
    insight -> payoff/conclusion. Remaining segments "" so audio breathes.
- Do NOT narrate every segment in either mode.
- Each spoken line: 10-15 words (under 3 seconds of speech).
- Arc (~60s): hook/surprising fact early -> breathing room -> insight
  mid-video -> educational fact near the end -> final question/conclusion
  (ASMR uses the sparse subset of this arc; general videos cover it fully).
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
- segments cover timeline contiguously (first start=0, each start=previous end).
- 8 to 16 short segments.
- speed: 1.0=normal, >1=faster. Vary per segment (1.08-1.15 typical).
- EVERY segment: zoom=none (MANDATORY), crop all zeros (MANDATORY — full frame,
  never crop), non-none color grade, speed != 1.0, sharpness 0.06-0.12 (subtle,
  never harsh). This is mandatory for copyright safety.
- crop: ALWAYS use exactly {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}.
- caption.text: <=5 words, ONLY on narrated segments (fact headline);
  "" on every other segment. Different wording each time.
- narration: value-adding commentary (fact, insight, opinion, humor, or a
  question) — NEVER a description of visible action, never a caption repeat.
  Count per audio_type: "asmr" -> 3-4 narrated segments only; general
  (vocal/music/sfx/silent) -> 5-8 narrated segments as a storyline. ALL
  other segments get "" (empty = silent, natural audio stays prominent).
  Never repeat a line in consecutive segments.
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
- title: distinct angle (fact-led, question-led, or bold hook) — never a
  paraphrase of the source title.
- description: 2-3 genuine sentences explaining the context/facts (no generic
  spam). 3-5 real hashtags only. Never mention Facebook, reposting, or
  compilation.
- Do not invent visual facts. Base on frames + audio only.
"""


def _api_key():
    k = os.getenv("NVIDIA_API_KEY")
    if not k:
        raise RuntimeError("NVIDIA_API_KEY not set")
    return k


def _call_nemotron(frames, title_hint="", duration=None, audio_b64=None):
    content = [{"type": "text", "text": PROMPT}]
    if title_hint:
        content.append({"type": "text", "text": f"Source title/context: {title_hint}"})
    if duration:
        content.append({"type": "text", "text": f"Source duration: {duration:.1f}s"})
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

    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
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
    for attempt in range(3):
        try:
            r = requests.post(API, headers=headers, json=payload, timeout=300)
            if r.status_code >= 500:
                last = RuntimeError(f"HTTP {r.status_code}")
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise last


def _gemini_key():
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def _call_gemini(frames, title_hint="", duration=None, audio_b64=None):
    """2nd-choice backend: same multimodal prompt -> Google Gemini generateContent."""
    key = _gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY / GOOGLE_API_KEY not set")
    parts = [{"text": PROMPT}]
    if title_hint:
        parts.append({"text": f"Source title/context: {title_hint}"})
    if duration:
        parts.append({"text": f"Source duration: {duration:.1f}s"})
    if audio_b64:
        parts.append({"inline_data": {"mime_type": "audio/wav", "data": audio_b64}})
        parts.append({"text": "Audio attached. Listen to decide audio_type and narration."})
    parts.append({"text": f"Here are {len(frames)} timestamped frames. Produce the JSON edit plan now."})
    for t, b64 in frames:
        parts.append({"text": f"[t={t:.2f}s]"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
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
    for model in models:
        url = f"{url_base}{model}:generateContent?key={key}"
        for attempt in range(3):
            try:
                r = requests.post(url, json=payload, timeout=300)
                if r.status_code == 400 and "thinkingConfig" in (r.text or ""):
                    payload.get("generationConfig", {}).pop("thinkingConfig", None)
                    continue
                if r.status_code == 429 or r.status_code >= 500:
                    last = RuntimeError(f"{model}: HTTP {r.status_code}: {(r.text or '')[:200]}")
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                cand = (data.get("candidates") or [{}])[0]
                txt = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
                if not txt:
                    raise RuntimeError(f"gemini empty response: {str(data)[:300]}")
                return txt
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last = e
                time.sleep(5 * (attempt + 1))
            except requests.exceptions.HTTPError as e:
                last = e
                break
    raise last


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
    n = 8
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
    # AI-observed source text (any position) -> regions to remove; [] if none.
    import smart as _smart
    plan["caption_regions"] = _smart.normalize_source_captions(plan.get("source_captions"))
    plan.pop("source_captions", None)

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
    raw = None
    first_err = None
    if os.getenv("NVIDIA_API_KEY"):
        try:
            raw = _call_nemotron(frames, title_hint=title_hint, duration=dur, audio_b64=audio_b64)
            print("[analyze] nemotron ok")
        except Exception as e:
            first_err = f"nemotron: {e}"
            print(f"[analyze] nemotron failed ({e}); trying gemini", file=sys.stderr)
    else:
        print("[analyze] no NVIDIA_API_KEY; trying gemini")
    if raw is None:
        if _gemini_key():
            try:
                raw = _call_gemini(frames, title_hint=title_hint, duration=dur, audio_b64=audio_b64)
                print("[analyze] gemini ok")
            except Exception as e:
                first_err = f"{first_err}; gemini: {e}" if first_err else f"gemini: {e}"
                print(f"[analyze] gemini failed ({e}); using simple fallback", file=sys.stderr)
        else:
            print("[analyze] no GEMINI/GOOGLE API key; using simple fallback")
    if raw is None:
        if allow_fallback:
            return _normalize(_fallback_plan(dur, title_hint), dur)
        raise RuntimeError(f"all AI backends failed ({first_err})")
    plan = _extract_json(raw)
    plan = _normalize(plan, dur)
    print(f"[analyze] {len(plan['segments'])} segs, audio={plan['audio_type']}, "
          f"mode={plan['audio_mode']}, style={plan.get('style')}")
    return plan
