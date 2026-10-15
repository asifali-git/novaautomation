"""Video analysis via NVIDIA Nemotron 3 Nano Omni (omni-modal).

Samples frames + audio, sends to hosted NIM, gets back a structured JSON
"edit plan" (segments, transforms, narration, caption, title/desc/hashtags/tags)
that the ffmpeg/Kokoro backend (apply.py + voice.py) executes.

Transformative floor is enforced so content is always altered enough to
reduce reused-content / copyright risk, even if the model returns sparse data.

Env: NVIDIA_API_KEY  (free key from build.nvidia.com)
"""
import os
import io
import base64
import json
import subprocess
import re
import requests

FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")
MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
API = "https://integrate.api.nvidia.com/v1/chat/completions"


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

CRITICAL - VARIETY: Do NOT reuse the same effect/caption/narration for every video.
Each video must feel UNIQUE. A nature clip, action scene, cartoon, talking head,
product demo each get different treatment.

CRITICAL - TRANSFORMATIVE EDITS (copyright/reused-content safety):
Every segment MUST get real visual changes so the output is sufficiently distinct
from the source. Never leave a segment with speed=1.0, color=preset none.
Always apply: crop (fixed heavy reframe), color grade, speed variation,
sharpness. This is MANDATORY per segment.
NO ZOOM — zoom is always "none". Use crop for recomposition instead.

AUDIO AWARENESS - decide "audio_type" by LISTENING:
  "vocal"  = spoken words: human/AI speech, movie dialogue, dubbing, narration, singing
  "asmr"   = ASMR: tapping, scratching, whispering-ambience, mouth sounds
  "music"  = background music/song only, no meaningful speech
  "sfx"    = sound effects / ambient noise only
  "silent" = no meaningful audio

Then set audio_mode + audio_volume (NEVER mute the original audio):
  "asmr" -> audio_mode "lower", audio_volume 0.35
  else   -> audio_mode "lower", audio_volume 0.55

VOICEOVER RULES:
- "vocal": original audio stays at 55% + add OUR narration on top. Write clean
  natural commentary for most segments - a real person calmly
  describing/reacting. Varied, never repeat the same line twice.
- "asmr": ADD soft gentle narration ON TOP of lowered ASMR audio. Sparse.
- "music"/"sfx"/"silent": punchy natural narration, not every segment.

NARRATION MUST BE CLEAN:
No fake interjections ("Hahaha!", "Oh no no no--", "YO DID YOU SEE THAT?!").
No screams, no exaggerated reactions. Just real natural spoken words.

UNIQUENESS (MANDATORY):
Every video gets UNIQUE narration and captions. Even if two videos show similar
content (e.g. two coconut-opening clips), describe ONLY what YOU see in THESE
specific frames — the exact angle, lighting, hand position, knife movement,
background details. Never use generic template phrases. Each caption must be
different from any caption you might give to a similar video. Base every word
on the actual frames provided, not on assumptions about what "usually" happens.

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
  "segments": [
    {
      "index": 0,
      "start": 0.0,
      "end": 3.0,
      "action": "keep"|"remove"|"highlight",
      "speed": 1.10,
      "crop": {"top": 0.08, "bottom": 0.06, "left": 0.04, "right": 0.04},
      "zoom": {"type": "none", "amount": 1.0},
      "sharpness": 0.18,
      "color": {"preset": "vivid"|"warm"|"cool"|"moody"|"noir"|"vintage"|"neon", "brightness": 0.0, "contrast": 1.06, "saturation": 1.12},
      "overlay": {"type": "none", "value": ""},
      "caption": {"text": "<=5 words", "position": "top"|"center"|"bottom", "style": "outline"|"box"|"bold"},
      "narration": "clean natural spoken line",
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
- EVERY segment: zoom=none (MANDATORY), fixed crop (top 0.08, bottom 0.06, left 0.04, right 0.04),
  non-none color grade, speed != 1.0, sharpness >= 0.12. This is mandatory for copyright safety.
- crop: ALWAYS use exactly {"top": 0.08, "bottom": 0.06, "left": 0.04, "right": 0.04}.
- caption.text: <=5 words, ONE per segment, different wording each.
- narration: clean natural spoken line. Empty string = silent segment.
  Never repeat the same line in consecutive segments.
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

# Fixed crop (same as fallback) — applied to ALL segments regardless of AI output.
FIXED_CROP = {"top": 0.08, "bottom": 0.06, "left": 0.04, "right": 0.04}


def _transform_floor(seg, idx):
    """Enforce transformative minimums on a segment (copyright safety).
    No zoom (zoompan removed). Fixed crop like fallback. Speed/color/sharpness vary."""
    if abs(seg["speed"] - 1.0) < 0.02:
        seg["speed"] = round(1.08 + (idx % 5) * 0.015, 3)
    # No zoom — always none.
    seg["zoom"] = {"type": "none", "amount": 1.0}
    # Fixed crop (same as fallback).
    seg["crop"] = dict(FIXED_CROP)
    if seg["color"]["preset"] == "none":
        seg["color"]["preset"] = PRESETS[idx % len(PRESETS)]
    if seg["color"]["saturation"] < 1.06:
        seg["color"]["saturation"] = 1.12
    if seg["color"]["contrast"] < 1.03:
        seg["color"]["contrast"] = 1.06
    if seg["sharpness"] < 0.12:
        seg["sharpness"] = 0.18


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
            sharp = min(1.0, max(0.0, float(s.get("sharpness", 0))))
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
    # Original audio is NEVER muted: ASMR 35%, everything else 55%.
    plan["audio_mode"] = "lower"
    plan["audio_volume"] = 0.35 if audio_type == "asmr" else 0.55
    plan["has_speech"] = audio_type == "vocal"
    plan["caption_regions"] = []

    g = plan.get("global") or {}
    if audio_type == "asmr":
        # ASMR: voiceover at 100% (1.0), original audio at 35%.
        vgain = 1.0
    else:
        # Non-ASMR: original stays at 55%, voiceover boosted to stay audible over it.
        vgain = min(2.0, max(1.3, float(g.get("voice_gain", 1.4))))
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
    if not os.getenv("NVIDIA_API_KEY"):
        if allow_fallback:
            print("[analyze] no NVIDIA_API_KEY; using fallback plan")
            return _normalize(_fallback_plan(dur, title_hint), dur)
        raise RuntimeError("NVIDIA_API_KEY not set")
    audio_b64 = extract_audio(path)
    raw = _call_nemotron(frames, title_hint=title_hint, duration=dur, audio_b64=audio_b64)
    plan = _extract_json(raw)
    plan = _normalize(plan, dur)
    print(f"[analyze] {len(plan['segments'])} segs, audio={plan['audio_type']}, "
          f"mode={plan['audio_mode']}, style={plan.get('style')}")
    return plan
