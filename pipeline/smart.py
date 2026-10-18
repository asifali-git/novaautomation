"""Smart analysis fallback (no Nemotron dependency).

Runs when the primary AI model fails. Uses LOCAL ffmpeg feature extraction
(audio RMS/spectrum, video motion/brightness) to categorize content, then
generates a content-aware unique plan via OpenRouter or seeded local templates.

Also provides detect_caption_regions() for burned-in source caption removal.

Env: OPENROUTER_API_KEY (optional - enables OpenRouter generation)
     FFMPEG_BIN, FFPROBE_BIN
"""
import os
import subprocess
import json
import zlib
import hashlib

FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")


def probe_duration(path):
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return None


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True)
    return proc.returncode == 0, proc.stdout, proc.stderr


def _sample_frames_gray(path, n=10, width=320):
    """Return list of numpy grayscale frames (small, CPU-light)."""
    import numpy as np
    dur = probe_duration(path) or 0
    if dur <= 0:
        return []
    frames = []
    step = dur / max(1, n)
    for i in range(n):
        t = i * step
        ok, raw, _ = _run([
            FFMPEG, "-y", "-ss", f"{t:.3f}", "-i", path,
            "-frames:v", "1", "-vf", f"scale={width}:-2,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ])
        if not ok or not raw:
            continue
        try:
            h = len(raw) // width
            arr = np.frombuffer(raw[: width * h], dtype=np.uint8).reshape(h, width)
            frames.append(arr.astype(np.float32))
        except Exception:
            continue
    return frames


def detect_caption_regions(path):
    """Detect burned-in captions/watermarks via per-row edge-energy analysis.

    Returns precise bounding boxes (as frame fractions) of static text bands:
      [{"band","top_frac","bottom_frac","action","confidence"}]
    - top/bottom bands  -> action "crop"   (exact rows to remove)
    - mid band           -> action "delogo"
    Precision is ROW-level (not coarse thirds), so only the text is cropped.
    """
    try:
        import numpy as np
    except ImportError:
        return []

    frames = _sample_frames_gray(path, n=12, width=360)
    if len(frames) < 3:
        return []

    h = frames[0].shape[0]
    st = np.stack(frames)                      # N,h,w

    # Per-row horizontal edge energy (text = strong horizontal gradients).
    edge = np.abs(np.diff(st, axis=2)).mean(axis=(0, 2))   # -> h
    # Per-row temporal variance (static text on moving bg = LOW variance).
    tvar = st.var(axis=0).mean(axis=1)                     # -> h

    # Baseline = median of lower-half edge (background texture level).
    base = float(np.median(edge))
    if base <= 0:
        base = 1.0
    # Static-text mask: high edge vs baseline AND low temporal variance.
    tv_base = float(np.median(tvar)) or 1.0
    text_mask = (edge > max(base * 1.5, 5.0)) & (tvar < tv_base * 1.6)

    # Group contiguous text rows (allow small gaps <= 8 rows: two-line captions).
    rows = np.where(text_mask)[0]
    if len(rows) == 0:
        return []
    groups = []
    start = prev = rows[0]
    for r in rows[1:]:
        if r - prev <= 8:
            prev = r
        else:
            groups.append((start, prev))
            start = prev = r
    groups.append((start, prev))

    # Keep groups tall enough to be text (>= 5 rows) and compute confidence.
    regions = []
    for y0, y1 in groups:
        if (y1 - y0) < 6:
            continue
        seg_edge = float(edge[y0:y1 + 1].mean())
        conf = min(1.0, seg_edge / (base * 6.0 + 1e-6))
        if conf < 0.25:
            continue
        top_frac = y0 / h
        bot_frac = (y1 + 1) / h
        mid = (top_frac + bot_frac) / 2.0
        if mid < 0.35:
            band = "top"
        elif mid > 0.65:
            band = "bottom"
        else:
            band = "mid"
        regions.append({
            "band": band,
            "top_frac": round(top_frac, 4),
            "bottom_frac": round(bot_frac, 4),
            "action": "delogo" if band == "mid" else "crop",
            "confidence": round(conf, 3),
        })

    # Sort by position; cap to 2 regions (top + bottom typical).
    regions.sort(key=lambda r: r["top_frac"])
    return regions[:2]


def _band_for(top, bot):
    mid = (top + bot) / 2.0
    if mid < 0.35:
        return "top"
    if mid > 0.65:
        return "bottom"
    return "mid"


def normalize_source_captions(raw):
    """Validate AI-observed [{top,bottom}] source-text extents -> region dicts.

    Position-agnostic: any frame-fraction band (top/mid/bottom) is accepted.
    """
    regions = []
    for r in raw or []:
        try:
            t = float(r.get("top"))
            b = float(r.get("bottom"))
        except Exception:
            continue
        if b < t:
            t, b = b, t
        t = max(0.0, min(1.0, t))
        b = max(0.0, min(1.0, b))
        if b - t < 0.012:          # < ~1% frame height = not a real text band
            continue
        band = _band_for(t, b)
        regions.append({
            "band": band,
            "top_frac": round(t, 4),
            "bottom_frac": round(b, 4),
            "action": "delogo" if band == "mid" else "crop",
            "confidence": 0.9,
        })
    regions.sort(key=lambda r: r["top_frac"])
    return regions[:3]


def merge_caption_regions(ai_regions, cv_regions):
    """AI + CV source-text bands. Precise CV rows WIN on overlap — crop only
    what the old caption actually covers (no over-crop). AI-only bands (CV
    missed, e.g. stylized/logo text) are kept as-is."""
    def _iv(items):
        out = []
        for r in items or []:
            try:
                t = float(r.get("top_frac"))
                b = float(r.get("bottom_frac"))
            except Exception:
                continue
            if b > t:
                out.append([t, b])
        return sorted(out)

    cv = _iv(cv_regions)
    out_iv = list(cv)
    for t, b in _iv(ai_regions):
        if any(t < cb + 0.02 and b > ct - 0.02 for ct, cb in cv):
            continue               # overlaps CV -> precise CV already covers it
        out_iv.append([t, b])      # AI-only -> CV missed it, keep
    out_iv.sort()

    regions = []
    for t, b in out_iv[:3]:
        band = _band_for(t, b)
        regions.append({
            "band": band,
            "top_frac": round(t, 4),
            "bottom_frac": round(b, 4),
            "action": "delogo" if band == "mid" else "crop",
            "confidence": 0.95,
        })
    return regions


def _features(path):
    """Local ffmpeg feature extraction: audio + video. CPU-light, no ML."""
    try:
        import numpy as np
    except ImportError:
        return {"audio_type": "music", "motion": 0.5, "brightness": 0.5,
                "colorfulness": 0.5, "scene_cuts": 0, "subcategory": "static"}

    dur = probe_duration(path) or 0

    # --- AUDIO features ---
    audio_type = "music"
    ok, raw, _ = _run([
        FFMPEG, "-y", "-t", "15", "-i", path,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "s16le", "-",
    ])
    rms = 0.0
    hf_ratio = 0.0
    speech_mod = 0.0
    if ok and raw and len(raw) > 3200:
        samples = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        if len(samples) > 1600:
            rms = float(np.sqrt(np.mean(samples ** 2)))
            # HF ratio via simple spectral estimate
            win = min(4096, len(samples))
            spec = np.abs(np.fft.rfft(samples[:win]))
            freqs = np.fft.rfftfreq(win, 1.0 / 16000)
            total_e = spec.sum() if spec.sum() > 0 else 1.0
            hf_ratio = float(spec[freqs > 3000].sum() / total_e)
            # Speech modulation: energy variance in 300-3400Hz over short windows
            n_win = min(20, len(samples) // 1600)
            if n_win > 2:
                band_energies = []
                for i in range(n_win):
                    chunk = samples[i * 1600:(i + 1) * 1600]
                    cs = np.abs(np.fft.rfft(chunk))
                    cf = np.fft.rfftfreq(len(chunk), 1.0 / 16000)
                    speech_e = cs[(cf >= 300) & (cf <= 3400)].sum()
                    band_energies.append(float(speech_e))
                speech_mod = float(np.var(band_energies)) if band_energies else 0.0

    # Classify audio
    if rms < 0.01:
        audio_type = "silent"
    elif speech_mod > rms * 0.3:
        audio_type = "vocal"
    elif rms < 0.08 and hf_ratio > 0.15:
        audio_type = "asmr"
    elif hf_ratio < 0.05 and rms > 0.05:
        audio_type = "music"
    else:
        audio_type = "sfx"

    # --- VIDEO features ---
    vf = _sample_frames_gray(path, n=10, width=160)
    motion = 0.5
    brightness = 0.5
    colorfulness = 0.5
    scene_cuts = 0

    if len(vf) >= 2:
        diffs = []
        for i in range(1, len(vf)):
            if vf[i].shape == vf[i - 1].shape:
                diffs.append(float(np.abs(vf[i] - vf[i - 1]).mean()))
        if diffs:
            motion = min(1.0, float(np.mean(diffs)) / 50.0)
            # Scene cuts = large diffs
            mean_d = np.mean(diffs)
            std_d = np.std(diffs) if len(diffs) > 1 else 0
            scene_cuts = sum(1 for d in diffs if d > mean_d + 2 * std_d)

        brightness = float(np.mean([f.mean() for f in vf]) / 255.0)

    # Colorfulness from RGB frames
    ok2, raw_rgb, _ = _run([
        FFMPEG, "-y", "-ss", "1", "-i", path, "-frames:v", "1",
        "-vf", "scale=160:-2,format=rgb24", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    if ok2 and raw_rgb:
        try:
            rgb = np.frombuffer(raw_rgb, dtype=np.uint8)
            pixel_count = len(rgb) // 3
            if pixel_count > 100:
                rgb = rgb[: pixel_count * 3].reshape(-1, 3).astype(np.float32)
                mx = rgb.max(axis=1)
                mn = rgb.min(axis=1)
                sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
                colorfulness = float(sat.mean())
        except Exception:
            pass

    # Subcategory
    if motion > 0.6:
        subcategory = "action"
    elif scene_cuts > 4:
        subcategory = "fastcut"
    elif brightness < 0.3:
        subcategory = "dark"
    elif brightness > 0.65 and colorfulness > 0.4:
        subcategory = "bright"
    else:
        subcategory = "static"

    return {
        "audio_type": audio_type,
        "motion": round(motion, 3),
        "brightness": round(brightness, 3),
        "colorfulness": round(colorfulness, 3),
        "scene_cuts": scene_cuts,
        "subcategory": subcategory,
        "rms": round(rms, 4),
    }


PRESETS = ["warm", "cool", "moody", "vintage", "vivid", "noir"]

NARRATION_TPL = {
    "action": [
        "Right here is where things start to shift.",
        "This moment changes the whole direction.",
        "Notice how quickly this unfolds on screen.",
        "This is the part everyone waits for.",
        "The energy picks up dramatically here.",
        "Watch what happens in these next few seconds.",
        "This sequence shows real skill in action.",
        "Everything builds up to this exact point.",
        "The intensity here is hard to miss.",
        "This is where the real story begins.",
    ],
    "static": [
        "Take a moment to absorb what's happening here.",
        "There's more detail in this frame than you'd expect.",
        "This shot tells you a lot if you look closely.",
        "The composition here is actually really thoughtful.",
        "Every element in this frame serves a purpose.",
        "This quiet moment carries real weight.",
        "Notice the subtle details that make this work.",
        "This is the kind of shot that rewards attention.",
        "There's a story being told in these few seconds.",
        "The way this is framed says a lot.",
    ],
    "bright": [
        "The colors in this scene are absolutely striking.",
        "This is what visual quality looks like at its best.",
        "The lighting here makes everything pop.",
        "This frame is genuinely beautiful to look at.",
        "The vibrancy in this shot is incredible.",
        "Pure visual satisfaction right here.",
        "This is the kind of quality that catches your eye.",
        "Everything about this frame just works perfectly.",
        "The visual clarity here is next level.",
        "This is eye candy at its finest.",
    ],
    "dark": [
        "The mood shifts completely in this moment.",
        "There's real tension building right here.",
        "The atmosphere gets heavier with each second.",
        "Notice how the tone takes a different turn.",
        "This part carries a completely different energy.",
        "The shadows here tell their own story.",
        "Something about this moment feels deeply intense.",
        "The mood here is a complete 180 from before.",
        "This is where things get serious.",
        "The darkness in this scene sets it apart.",
    ],
    "fastcut": [
        "The editing rhythm here is absolutely masterful.",
        "Every single cut changes the entire narrative.",
        "These rapid transitions keep you completely hooked.",
        "This sequence moves at incredible speed.",
        "The way these shots flow together is seamless.",
        "Notice how fast the visual story evolves.",
        "These quick cuts create unstoppable momentum.",
        "This is editing at a completely different level.",
        "The pace here never lets up for a second.",
        "Every frame earns its place in this sequence.",
    ],
    "asmr": [
        "The sound design here is incredibly satisfying.",
        "Listen to how crystal clear this audio is.",
        "This texture sounds absolutely perfect.",
        "The audio quality here is genuinely impressive.",
        "Such clean, precise, satisfying sound.",
        "Every detail in this audio stands out.",
        "This is pure sensory satisfaction.",
        "The ambient sounds create the perfect atmosphere.",
        "Close your eyes and just listen to this.",
        "This is what high-quality audio feels like.",
    ],
}

CAPTION_TPL = {
    "action": ["Watch this", "Right here", "No way", "Look at that", "This is wild", "Wait for it"],
    "static": ["Look closer", "Details matter", "Notice this", "So subtle", "Worth watching", "Hidden gem"],
    "bright": ["Stunning", "So vibrant", "Beautiful", "Pure quality", "Incredible", "Next level"],
    "dark": ["Mood shift", "Gets intense", "Building tension", "Different vibe", "Heavy mood", "Dark turn"],
    "fastcut": ["Fast edits", "Non-stop", "Rapid fire", "Seamless", "Lightning fast", "Unstoppable"],
    "asmr": ["Satisfying", "So crisp", "Perfect sound", "Clean audio", "Pure detail", "Sensory"],
}

TITLE_TPL = {
    "action": ["This Moment Changes Everything", "Wait For What Happens Next", "The Part Nobody Expected", "Intense Right Here"],
    "static": ["The Detail Everyone Missed", "Look Closely At This", "A Moment Worth Watching", "Subtle But Powerful"],
    "bright": ["Visually Stunning Moment", "The Colors Are Incredible", "Pure Eye Candy Right Here", "Beautiful To Watch"],
    "dark": ["The Mood Changes Everything", "Dark Turn You Didn't Expect", "Building Tension Here", "A Completely Different Energy"],
    "fastcut": ["The Editing Is Insane", "Every Cut Changes It All", "Non-Stop Visual Energy", "Lightning Fast Edits"],
    "asmr": ["Satisfying Sounds Only", "Pure Sensory Satisfaction", "The Audio Is Incredible", "So Perfect To Hear"],
}


def _seed_for(path, title_hint):
    return zlib.crc32(f"{path}|{title_hint}".encode()) & 0xFFFFFFFF


def _pick(lst, seed, offset=0):
    return lst[(seed + offset) % len(lst)]


def _call_openrouter(feats, dur, seed):
    """Ask OpenRouter for a full edit plan based on local ffmpeg analysis."""
    if not OPENROUTER_KEY:
        return None
    try:
        import requests
        prompt = (
            "You are a short-form video editor for YouTube Shorts/TikTok. "
            "A local ffmpeg analysis of a video is below. Based ONLY on these features, "
            "write a complete edit plan as JSON.\n\n"
            f"FFMPEG ANALYSIS OF THE VIDEO:\n"
            f"- Duration: {dur:.1f} seconds\n"
            f"- Audio type detected: {feats['audio_type']} (RMS loudness={feats.get('rms',0)})\n"
            f"- Motion level: {feats['motion']} (0=completely still, 1=high-speed action)\n"
            f"- Brightness: {feats['brightness']} (0=dark, 1=bright)\n"
            f"- Colorfulness: {feats['colorfulness']} (0=grayscale, 1=very colorful)\n"
            f"- Scene cuts detected: {feats['scene_cuts']}\n"
            f"- Content subcategory: {feats['subcategory']}\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Write narration that DESCRIBES what is likely happening based on the features. "
            "For example, if motion=0.8 and subcategory=action, write lines like "
            "'This is where the action peaks' or 'Things move so fast here'. "
            "If subcategory=bright and colorfulness=0.6, write about the visuals. "
            "DO NOT write generic placeholder text. Every narration line must be UNIQUE "
            "and content-aware.\n"
            "2. Captions (on-screen text): SHORT punchy phrases <=5 words, different each segment.\n"
            "3. Title/description/hashtags/tags: optimized for YouTube Shorts discoverability.\n"
            "4. NO fake laughter, no 'Hahaha', no exaggerated reactions. Clean natural speech.\n"
            "5. EVERY segment MUST have: zoom in or out, color preset (not none), speed 1.02-1.15, "
            "sharpness >= 0.1. This is mandatory for copyright safety.\n\n"
            "Return ONLY a valid JSON object (no markdown, no explanation) with this structure:\n"
            "{\n"
            '  "audio_type": string,\n'
            '  "audio_mode": string,\n'
            '  "audio_volume": number,\n'
            '  "has_speech": boolean,\n'
            '  "video_summary": string,\n'
            '  "style": string,\n'
            '  "title": string,\n'
            '  "description": string,\n'
            '  "hashtags": array of strings,\n'
            '  "tags": array of strings,\n'
            '  "segments": array of {\n'
            '    "index": number, "start": number, "end": number,\n'
            '    "action": "keep",\n'
            '    "speed": number,\n'
            '    "crop": {"top":number,"bottom":number,"left":number,"right":number},\n'
            '    "zoom": {"type":"in" or "out","amount":number},\n'
            '    "sharpness": number,\n'
            '    "color": {"preset":string,"brightness":number,"contrast":number,"saturation":number},\n'
            '    "caption": {"text":string,"position":"bottom" or "top","style":"outline"},\n'
            '    "narration": string,\n'
            '    "voice_style": {"speed":number},\n'
            '    "notes": string\n'
            "  },\n"
            '  "global": {"intro_effect":"fade","ending_effect":"fade","voice_gain":number}\n'
            "}\n\n"
            f"Generate 6-{max(8, int(dur/2))} segments covering 0 to {dur:.1f}s contiguously. "
            "audio_mode must always be 'lower' (NEVER mute the original audio). "
            "audio_volume: 0.35 for asmr, 0.55 otherwise."
        )
        r = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
                "temperature": 0.8,
            },
            timeout=120,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        # Parse JSON
        try:
            plan = json.loads(text)
        except Exception:
            import re as _re
            m = _re.search(r"\{.*\}", text, _re.DOTALL)
            if m:
                plan = json.loads(m.group(0))
            else:
                return None
        # Validate: reject generic/placeholder narration (model copied example text)
        segs = plan.get("segments") or []
        if not segs:
            return None
        narrs = [(s.get("narration") or "").strip().lower() for s in segs]
        bad_patterns = ["clean natural", "natural spoken", "spoken line", "clean spoken",
                        "this is a", "example", "placeholder", "lorem"]
        generic_count = sum(1 for n in narrs if n and any(p in n for p in bad_patterns))
        if generic_count > len(narrs) * 0.4:
            print(f"[smart] OpenRouter returned generic narration ({generic_count}/{len(narrs)}); using seeded")
            return None
        # Reject if >60% of non-empty narrations are identical
        non_empty = [n for n in narrs if n]
        if non_empty:
            from collections import Counter
            most_common_count = Counter(non_empty).most_common(1)[0][1]
            if most_common_count > len(non_empty) * 0.6:
                print("[smart] OpenRouter narration too repetitive; using seeded")
                return None
        return plan
    except Exception as e:
        print(f"[smart] OpenRouter failed: {e}")
        return None


def _seeded_plan(feats, dur, seed, title_hint=""):
    """Build a unique seeded plan from local analysis (no AI needed)."""
    n = max(5, min(10, int(dur / 2.5) + 3))
    seg_len = dur / n
    subcat = feats.get("subcategory", "static")
    audio_type = feats.get("audio_type", "music")

    nar_bank = NARRATION_TPL.get(subcat, NARRATION_TPL["static"])
    cap_bank = CAPTION_TPL.get(subcat, CAPTION_TPL["static"])
    title_bank = TITLE_TPL.get(subcat, TITLE_TPL["static"])

    segs = []
    used_narrs = set()
    used_caps = set()
    for i in range(n):
        # Unique per-segment values seeded by (seed + i)
        s = seed + i * 7919
        speed = round(1.02 + (s % 10) * 0.013, 3)
        zoom_type = "in" if (s // 3) % 2 == 0 else "out"
        zoom_amt = round(1.05 + (s % 6) * 0.012, 3)
        preset = PRESETS[(s // 5) % len(PRESETS)]
        # Bright content: avoid neon (washes out highlights)
        if feats.get("brightness", 0.5) > 0.6 and preset == "neon":
            preset = "warm"
        # No crop — full frame kept (subject stays visible).
        crop_amt = 0.0
        sharp = round(0.05 + (s % 3) * 0.03, 3)

        # Narration: seeded pick, avoid ALL previously used (not just previous)
        narr = ""
        for off in range(len(nar_bank)):
            cand = _pick(nar_bank, s, i + off)
            if cand not in used_narrs:
                narr = cand
                used_narrs.add(cand)
                break
        if not narr:
            narr = _pick(nar_bank, s, i)

        # Caption: every other segment, unique across segments
        cap_text = ""
        if i % 2 == 0:
            for off in range(len(cap_bank)):
                cand = _pick(cap_bank, s, i + off)
                if cand not in used_caps:
                    cap_text = cand
                    used_caps.add(cand)
                    break

        segs.append({
            "index": i,
            "start": round(i * seg_len, 2),
            "end": round((i + 1) * seg_len, 2),
            "action": "keep",
            "speed": speed,
            "crop": {"top": crop_amt, "bottom": 0.0, "left": 0.0, "right": 0.0},
            "zoom": {"type": zoom_type, "amount": zoom_amt},
            "sharpness": sharp,
            "color": {"preset": preset, "brightness": 0.0, "contrast": 1.05, "saturation": 1.10},
            "overlay": {"type": "none", "value": ""},
            "caption": {"text": cap_text, "position": "bottom", "style": "box"},
            "narration": narr,
            "voice_style": {"speed": 1.0},
            "notes": f"seeded {subcat}",
        })

    title = _pick(title_bank, seed)
    if title_hint:
        title = title_hint[:90]

    is_asmr = audio_type == "asmr"
    return {
        "audio_type": audio_type,
        "audio_mode": "lower",
        "audio_volume": 0.35 if is_asmr else 0.55,
        "has_speech": audio_type == "vocal",
        "video_summary": f"{subcat} content, {dur:.0f}s",
        "style": subcat,
        "title": title[:90],
        "description": f"Unique {subcat} clip. {title_hint or 'Watch this moment unfold.'}",
        "hashtags": ["#shorts", f"#{subcat}", "#viral", "#trending", "#fyp"],
        "tags": ["shorts", subcat, "viral", "trending", "video", "edit", "content", "watch", "moment", "fyp"],
        "caption_regions": [],
        "segments": segs,
        "global": {"intro_effect": "fade", "ending_effect": "fade", "voice_gain": 1.0 if is_asmr else 1.4},
    }


def _simple_plan(dur):
    """Deterministic visual-only plan. Same template for ALL videos.
    No narration, no captions, no AI, no zoom. Just transformative edits:
    crop + speed + color + sharpness. Copyright-safe.
    AUDIO IS PRESERVED (not muted) — original voice/music stays.
    Transforms are intentionally obvious (not subtle) to bypass reuse detection."""
    n = max(4, min(8, int(dur / 3) + 2))
    seg_len = dur / n
    segs = []
    for i in range(n):
        # Fixed alternating pattern — same every video, every run.
        # No zoom. Obvious crop + speed + sharpness only.
        speed = [1.12, 1.10, 1.15, 1.11, 1.13, 1.10, 1.14, 1.12][i % 8]
        # Only bright/natural presets — NO moody/vintage (they look B&W).
        preset = ["warm", "cool", "vivid", "warm", "cool", "vivid", "warm", "cool"][i % 8]
        sharp = 0.12
        # No crop — full frame kept; only old-caption rows are cropped later.
        crop_top = 0.0
        crop_bot = 0.0
        crop_left = 0.0
        crop_right = 0.0
        segs.append({
            "index": i,
            "start": round(i * seg_len, 2),
            "end": round((i + 1) * seg_len, 2),
            "action": "keep",
            "speed": speed,
            "crop": {"top": crop_top, "bottom": crop_bot, "left": crop_left, "right": crop_right},
            "zoom": {"type": "none", "amount": 1.0},
            "sharpness": sharp,
            "color": {"preset": preset, "brightness": 0.0, "contrast": 1.06, "saturation": 1.12},
            "overlay": {"type": "none", "value": ""},
            "caption": {"text": "", "position": "bottom", "style": "outline"},
            "narration": "",
            "voice_style": {"speed": 1.0},
            "notes": "simple-visual-only",
        })
    return {
        "audio_type": "music",
        "audio_mode": "keep",       # keep original audio (not mute)
        "audio_volume": 1.0,        # full volume
        "has_speech": False,
        "video_summary": "simple visual transform (no AI analysis)",
        "style": "transform",
        "title": "",
        "description": "",
        "hashtags": [],
        "tags": [],
        "segments": segs,
        "caption_regions": [],
        "global": {"intro_effect": "fade", "ending_effect": "fade", "voice_gain": 1.0},
    }


def analyze_video(path, title_hint=""):
    """Fallback: NO AI, NO OpenRouter, NO voiceover, NO captions.
    Returns a simple deterministic visual-only plan (zoom/speed/color/sharpness/crop).
    NEVER raises."""
    dur = probe_duration(path) or 15.0
    print("[smart] simple visual-only fallback (no AI)")
    plan = _simple_plan(dur)
    print(f"[smart] {len(plan['segments'])} segs, visual-only template")
    return plan
