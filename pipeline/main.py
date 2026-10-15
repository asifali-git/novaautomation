"""Pipeline orchestrator. Runs inside GitHub Actions (or locally).

Flow: fetch task -> download -> analyze (Nemotron -> smart fallback) -> caption-region
      detection -> voiceover (Kokoro) -> apply edits (ffmpeg per JSON plan)
      -> metadata -> upload -> report.

Env:
  WORKER_URL, INTERNAL_TOKEN   worker API for state
  VIDEO_ID                     process a specific video id (else auto-fetch next)
  ANALYZE=true|false           use AI edit-plan (default true; Nemotron -> smart fallback)
  NVIDIA_API_KEY               Nemotron omni key (build.nvidia.com)
  VOICEOVER=true|false         add Kokoro narration from plan (default true)
  MAX_SECS                     cap clip length (default 30)
  PRIVACY                      public|unlisted|private (default public)
  OPENROUTER_API_KEY           metadata fallback if plan lacks it
  YT_REFRESH_TOKEN/CLIENT_*    YouTube upload
  COOKIES_FILE                 optional cookies for source download
"""
import os
import sys
import json
import traceback
import requests

import download as dl
import edit as ed
import voice as vc
import metadata as md
import ytauth as yt
import analyze as an
import smart
import apply as ap
import upload_social as us

WORKER = os.getenv("WORKER_URL", "").rstrip("/")
TOKEN = os.getenv("INTERNAL_TOKEN", "")
MAX_SECS = float(os.getenv("MAX_SECS", "30"))
ANALYZE = os.getenv("ANALYZE", "true").lower() in ("1", "true", "yes")
VOICEOVER = os.getenv("VOICEOVER", "true").lower() in ("1", "true", "yes")
PRIVACY = os.getenv("PRIVACY", "public")


def _h():
    return {"x-token": TOKEN, "content-type": "application/json"}


def worker_get(path):
    r = requests.get(f"{WORKER}{path}", headers=_h(), timeout=30)
    r.raise_for_status()
    return r.json()


def worker_patch(path, body):
    r = requests.patch(f"{WORKER}{path}", headers=_h(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def set_status(video_id, **fields):
    print(f"[state] video {video_id} -> {fields.get('status', '')} {fields.get('error', '') or ''}")
    try:
        worker_patch(f"/api/internal/videos/{video_id}", fields)
    except Exception as e:
        print(f"[warn] state update failed: {e}", file=sys.stderr)


def fetch_target():
    if os.getenv("VIDEO_ID"):
        vid = int(os.getenv("VIDEO_ID"))
        data = worker_get(f"/api/internal/videos/{vid}")
        return data.get("video")
    data = worker_get("/api/internal/videos/next")
    return data.get("video")


def make_plan(src, title_hint, vid):
    """Returns (plan, used_ai). used_ai=True when Nemotron path succeeded."""
    if not ANALYZE:
        print("[analyze] disabled; simple visual-only plan")
        return smart.analyze_video(src, title_hint=title_hint), False
    set_status(vid, status="analyzing")
    try:
        plan = an.analyze_video(src, title_hint=title_hint)
        return plan, True
    except Exception as e:
        print(f"[analyze] nemotron failed ({e}); using simple fallback", file=sys.stderr)
        return smart.analyze_video(src, title_hint=title_hint), False


def make_voices(plan, work, vid, used_ai=True):
    """Synthesize Kokoro narration. Only for AI path (fallback has no narration).
    Splits text into sentences + adds tiny pauses for natural human-like flow."""
    wavs = {}
    if not VOICEOVER or not used_ai:
        return wavs
    set_status(vid, status="voiceover")
    for seg in plan["segments"]:
        text = (seg.get("narration") or "").strip()
        if not text:
            continue
        try:
            vs = seg.get("voice_style") or {}
            speed = float(vs.get("speed", 1.0))
            out = os.path.join(work, f"narr_{seg['index']:02d}.wav")
            vc.synthesize(text, out_path=out, speed=speed)
            wavs[seg["index"]] = out
            print(f"[voice] seg {seg['index']} spd={speed}: {text[:55]}")
        except Exception as e:
            print(f"[warn] voice seg {seg['index']} failed: {e}", file=sys.stderr)
    return wavs


def fit_segments_to_voices(plan, voice_wavs, src_duration=None):
    """Extend segment durations so narration plays at natural speed (no atempo crush).
    Also renormalizes segments to stay contiguous on the timeline.
    src_duration: max source seconds — total timeline NEVER exceeds this."""
    if not voice_wavs:
        return
    ffprobe = os.getenv("FFPROBE_BIN", "ffprobe")
    segs = plan["segments"]
    if src_duration is None:
        src_duration = max(float(s.get("end", 0)) for s in segs) if segs else 30.0

    # 1. First pass: compute needed extension for each segment.
    extensions = {}  # idx -> needed duration (source seconds)
    for seg in segs:
        idx = seg.get("index")
        if idx not in voice_wavs:
            continue
        wav_path = voice_wavs[idx]
        try:
            import subprocess
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", wav_path],
                capture_output=True, text=True, timeout=10)
            wav_dur = float(r.stdout.strip())
        except Exception as e:
            print(f"[fit] seg {idx}: ffprobe failed ({e}); skip", file=sys.stderr)
            continue
        speed = abs(float(seg.get("speed", 1.0)) or 1.0)
        if speed < 0.01:
            speed = 1.0
        needed_src = (wav_dur + 0.15) * speed
        current_src = float(seg["end"]) - float(seg["start"])
        if needed_src > current_src:
            extensions[idx] = min(needed_src, current_src * 2.2)

    # 2. Apply extensions with CUMULATIVE cap at src_duration.
    #    LAST segment gets priority — steal time from earlier if needed so
    #    the final narration never cuts mid-word.
    last_with_wav = max(usable_indices) if (usable_indices := [s.get("index") for s in segs if s.get("index") in voice_wavs]) else None

    t = 0.0
    for seg in segs:
        idx = seg.get("index")
        current_src = float(seg["end"]) - float(seg["start"])
        needed = extensions.get(idx, current_src)
        remaining = src_duration - t

        # For the last segment with narration: guarantee full needed time.
        if idx == last_with_wav and idx in extensions:
            # Steal from remaining budget — give it what it needs.
            actual = min(needed, remaining)
        else:
            actual = min(needed, remaining)

        if actual < 0.5:
            actual = 0.5
        seg["start"] = round(t, 3)
        seg["end"] = round(t + actual, 3)
        if idx in extensions and actual < extensions[idx]:
            print(f"[fit] seg {idx}: capped {current_src:.2f}s -> {actual:.2f}s "
                  f"(needed {extensions[idx]:.2f}s, remaining {remaining:.2f}s)")
        elif idx in extensions:
            print(f"[fit] seg {idx}: extended {current_src:.2f}s -> {actual:.2f}s")
        t = seg["end"]

    # 3. If last narration segment was still capped, steal time from earlier segments.
    if last_with_wav is not None and last_with_wav in extensions:
        last_seg = None
        for s in segs:
            if s.get("index") == last_with_wav:
                last_seg = s
                break
        if last_seg:
            last_needed = extensions[last_with_wav]
            last_actual = float(last_seg["end"]) - float(last_seg["start"])
            if last_actual < last_needed * 0.95:
                deficit = last_needed - last_actual
                earlier = [s for s in segs if s.get("index") != last_with_wav]
                stealable = sum(max(0, (float(s["end"]) - float(s["start"])) - 0.8) for s in earlier)
                if stealable >= deficit * 0.5:
                    for s in earlier:
                        d = float(s["end"]) - float(s["start"])
                        extra = max(0, d - 0.8)
                        give = min(extra, deficit * (extra / stealable if stealable > 0 else 0))
                        s["end"] = round(float(s["end"]) - give, 3)
                        deficit -= give
                    # Renormalize: all segments except last_with_wav, then append last.
                    t = 0.0
                    for s in segs:
                        if s.get("index") == last_with_wav:
                            continue
                        d = float(s["end"]) - float(s["start"])
                        s["start"] = round(t, 3)
                        s["end"] = round(t + d, 3)
                        t = s["end"]
                    last_seg["start"] = round(t, 3)
                    last_seg["end"] = round(t + min(last_needed, src_duration - t), 3)
                    t = last_seg["end"]
                    print(f"[fit] last seg {last_with_wav}: stole time -> "
                          f"{last_seg['end']-last_seg['start']:.2f}s")

    total = t
    print(f"[fit] total timeline: {total:.2f}s (source={src_duration:.2f}s)")


def build_meta(plan, info):
    meta = {
        "title": (plan.get("title") or info.get("title") or "Short")[:95],
        "description": (plan.get("description") or info.get("description") or ""),
        "tags": plan.get("tags") or info.get("tags") or [],
    }
    # Narration appended to description for context/SEO.
    narrs = [s["narration"] for s in plan["segments"] if (s.get("narration") or "").strip()]
    if narrs:
        meta["description"] = (meta["description"] + "\n\n" + " ".join(narrs))[:4800]
    # Append hashtags if present.
    hashtags = plan.get("hashtags")
    if hashtags:
        meta["description"] = (meta["description"] + "\n\n" + " ".join(hashtags))[:4800]
    # If the plan gave no metadata, fall back to OpenRouter.
    if not plan.get("title") and os.getenv("OPENROUTER_API_KEY"):
        try:
            m = md.make_metadata(info.get("title") or meta["title"],
                                 info.get("description") or "",
                                 info.get("tags") or [])
            meta.update(m)
        except Exception as e:
            print(f"[warn] metadata fallback failed: {e}", file=sys.stderr)
    return meta


def run():
    if not WORKER or not TOKEN:
        raise RuntimeError("WORKER_URL and INTERNAL_TOKEN required")

    video = fetch_target()
    if not video or not video.get("url"):
        print("[done] nothing to process")
        return 0

    vid = video["id"]
    url = video["url"]
    title = video.get("title") or "Short"
    work = "work"
    os.makedirs(work, exist_ok=True)

    set_status(vid, status="downloading")
    src, info = dl.download(url, out_dir=work, max_secs=MAX_SECS)
    print(f"[download] {src} ({os.path.getsize(src)//1024} KB)")
    title_hint = info.get("title") or title
    set_status(vid, status="editing", title=title_hint)

    # 1. Analyze -> JSON edit plan (Nemotron -> simple visual fallback).
    plan, used_ai = make_plan(src, title_hint, vid)
    print(f"[plan] {len(plan['segments'])} segs, ai={used_ai}, "
          f"audio={plan.get('audio_type')}, mode={plan.get('audio_mode')}")

    # 1b. Detect burned-in source captions so apply.py can crop/delogo them.
    try:
        plan["caption_regions"] = smart.detect_caption_regions(src)
        print(f"[captions] detected {len(plan['caption_regions'])} region(s)")
    except Exception:
        plan["caption_regions"] = []
        print("[captions] detection failed; none injected")

    # 2. Kokoro narration (AI path only; fallback = visual-only, no voice).
    voice_wavs = make_voices(plan, work, vid, used_ai=used_ai)

    # 2b. Extend segment durations so narration plays at natural speed (no crush).
    #     Cap at source video duration so segments never go beyond available footage.
    if voice_wavs:
        try:
            src_dur = an.probe_duration(src)
        except Exception:
            src_dur = None
        fit_segments_to_voices(plan, voice_wavs, src_duration=src_dur)

    # 3. ffmpeg applies the plan (segments, captions, narration, effects).
    set_status(vid, status="editing")
    out_path = ap.apply_plan(src, plan, workdir=work, voice_wavs=voice_wavs)
    print(f"[apply] {out_path} ({os.path.getsize(out_path)//1024} KB)")

    # 4. Metadata (plan first, OpenRouter fallback).
    set_status(vid, status="metadata")
    meta = build_meta(plan, info)
    print(f"[meta] {meta['title']}")

    # 5. Upload.
    set_status(vid, status="uploading")
    access = yt.refresh_access_token()
    video_id = yt.upload_video(
        out_path,
        title=meta["title"],
        description=meta["description"],
        tags=meta["tags"],
        privacy=PRIVACY,
        access_token=access,
    )
    print(f"[upload] youtube id {video_id}")

    # 5b. Social media upload (TikTok / Instagram / Facebook via cookies).
    social_results = {}
    if us.ENABLE_TIKTOK or us.ENABLE_INSTAGRAM or us.ENABLE_FACEBOOK:
        set_status(vid, status="uploading", upload_ref=json.dumps(
            {"provider": "youtube", "id": video_id, "social": "pending"}))
        try:
            social_results = us.upload_all(
                out_path, meta["title"], meta["description"])
            print(f"[social] results: {social_results}")
        except Exception as e:
            print(f"[social] upload failed: {e}", file=sys.stderr)
            social_results = {"error": str(e)}

    upload_ref = {"provider": "youtube", "id": video_id}
    if social_results:
        upload_ref["social"] = social_results

    set_status(
        vid,
        status="uploaded",
        meta=json.dumps(meta),
        upload_ref=json.dumps(upload_ref),
        duration=ed.probe_duration(out_path),
    )
    print("[done] success")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception:
        print("[fatal]", traceback.format_exc(), file=sys.stderr)
        try:
            vid = int(os.getenv("VIDEO_ID")) if os.getenv("VIDEO_ID") else None
            if vid is None:
                v = fetch_target()
                vid = v["id"] if v else None
            if vid:
                set_status(vid, status="failed", error=traceback.format_exc()[-500:])
        except Exception:
            pass
        sys.exit(1)
