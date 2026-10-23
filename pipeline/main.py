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
  MAX_SECS                     shorts limit: longer sources are skipped (default 60)
  PRIVACY                      public|unlisted|private (default public)
  OPENROUTER_API_KEY           metadata fallback if plan lacks it
  YT_REFRESH_TOKEN/CLIENT_*    YouTube upload
  COOKIES_FILE                 optional cookies for source download
"""
import os
import sys
import json
import time
import traceback
import requests

import download as dl
import apikeys
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
try:
    MAX_SECS = float(apikeys.setting("max_secs", os.getenv("MAX_SECS", "60")) or 60)
except (TypeError, ValueError):
    MAX_SECS = 60.0
ANALYZE = os.getenv("ANALYZE", "true").lower() in ("1", "true", "yes")
VOICEOVER = os.getenv("VOICEOVER", "true").lower() in ("1", "true", "yes")
PRIVACY = os.getenv("PRIVACY", "public")

VOICE_LANG = {
    "a": "English", "b": "English", "e": "Spanish", "f": "French",
    "h": "Hindi", "i": "Italian", "j": "Japanese", "p": "Portuguese",
    "z": "Chinese (Mandarin)",
}
DEFAULT_VOICE = "af_heart"


def apply_voice_setting(voice):
    voice = (voice or "").strip() or DEFAULT_VOICE
    os.environ["KOKORO_VOICE"] = voice
    lang = VOICE_LANG.get(voice[:1], "English")
    os.environ["VIDEO_LANG"] = lang
    print(f"[voice] tts={voice} lang={lang}")


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


def is_cancelled(video_id):
    """True if the user cancelled this video in the panel (cancel is sticky)."""
    try:
        v = worker_get(f"/api/internal/videos/{video_id}").get("video") or {}
        return v.get("status") == "cancelled"
    except Exception as e:
        print(f"[warn] cancel check failed: {e}", file=sys.stderr)
        return False


def post_plan(kind, id_, plan):
    """Send the edit-plan JSON to fastpoll for the dashboard's JSON download."""
    try:
        base = os.getenv("FASTPOLL_URL", "https://novapoll.onrender.com").rstrip("/")
        requests.post(
            f"{base}/api/plan",
            headers={"x-token": TOKEN, "Content-Type": "application/json"},
            json={"key": f"{kind[0]}{id_}", "kind": kind, "id": id_, "plan": plan},
            timeout=10)
        print(f"[plan] {kind}{id_} plan posted (download button)")
    except Exception as e:
        print(f"[plan] post failed: {e}", file=sys.stderr)


def upload_with_retry(fn, *, attempts=3, backoff=15, label="upload"):
    """Run fn() up to `attempts` times with linear backoff. Returns result or raises last error."""
    last = None
    for i in range(1, attempts + 1):
        try:
            out = fn()
            if i > 1:
                print(f"[retry] {label} succeeded on attempt {i}")
            return out
        except Exception as e:
            last = e
            print(f"[retry] {label} attempt {i}/{attempts} failed: {e}", file=sys.stderr)
            if i < attempts:
                time.sleep(backoff * i)
    raise last


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


def _wav_duration(path):
    """Wav length in seconds — Python wave header first (no subprocess, can't
    time out), ffprobe fallback for non-PCM files."""
    try:
        import wave as _w
        with _w.open(path, "rb") as w:
            fr = w.getframerate() or 1
            return w.getnframes() / float(fr)
    except Exception:
        pass
    import subprocess
    try:
        r = subprocess.run(
            [os.getenv("FFPROBE_BIN", "ffprobe"), "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return None


def fit_segments_to_voices(plan, voice_wavs, src_duration=None):
    """Extend segment durations so narration plays at natural speed (no atempo crush).
    Also renormalizes segments to stay contiguous on the timeline.
    src_duration: max source seconds — total timeline NEVER exceeds this."""
    if not voice_wavs:
        return
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
        wav_dur = _wav_duration(wav_path)
        if not wav_dur or wav_dur <= 0:
            print(f"[fit] seg {idx}: wav probe failed; skip", file=sys.stderr)
            continue
        speed = abs(float(seg.get("speed", 1.0)) or 1.0)
        if speed < 0.01:
            speed = 1.0
        needed_src = (wav_dur + 0.15) * speed
        current_src = float(seg["end"]) - float(seg["start"])
        if needed_src > current_src:
            extensions[idx] = min(needed_src, current_src * 2.2)

    # 2. Budget allocation: narration segments get their full needed length
    #    FIRST; shortfall is taken from SILENT segments, then non-final
    #    narrated ones. The final narration NEVER gets cut — a cut-off
    #    sentence sounds like a corrupted render (user feedback: "Would"...
    #    silence..."you drink it...").
    last_with_wav = max((s.get("index") for s in segs
                         if s.get("index") in voice_wavs), default=None)
    alloc = {}
    for seg in segs:
        idx = seg.get("index")
        cur = float(seg["end"]) - float(seg["start"])
        alloc[idx] = extensions.get(idx, cur)
    deficit = sum(alloc.values()) - src_duration
    if deficit > 0.01:
        # Take from SILENT segments first (floor 0.6s each).
        for seg in segs:
            if deficit <= 0.01:
                break
            idx = seg.get("index")
            if idx in voice_wavs:
                continue
            take = min(max(0.0, alloc[idx] - 0.6), deficit)
            alloc[idx] -= take
            deficit -= take
    if deficit > 0.01:
        # Then non-final narrated segments (floor 1.0s) — their voice still
        # plays in full at mix time (slight overlap >> cut words).
        for seg in segs:
            if deficit <= 0.01:
                break
            idx = seg.get("index")
            if idx not in voice_wavs or idx == last_with_wav:
                continue
            take = min(max(0.0, alloc[idx] - 1.0), deficit)
            alloc[idx] -= take
            deficit -= take
    if deficit > 0.01:
        print(f"[fit] WARNING: {deficit:.2f}s over budget even after cuts",
              file=sys.stderr)
    # HARD CAP (issue C): 0.5s floors can still leave the timeline past the
    # source EOF — segments then render short video + full padded audio and
    # the A/V streams drift for the rest of the clip. Compress uniformly.
    if src_duration and sum(alloc.values()) > src_duration + 0.01:
        f = src_duration / sum(alloc.values())
        print(f"[fit] compressing timeline x{f:.3f} to fit source")
        for idx in list(alloc.keys()):
            alloc[idx] = alloc[idx] * f

    t = 0.0
    for seg in segs:
        idx = seg.get("index")
        cur = float(seg["end"]) - float(seg["start"])
        actual = alloc.get(idx, cur)
        if actual < 0.5:
            actual = 0.5
        seg["start"] = round(t, 3)
        seg["end"] = round(t + actual, 3)
        if idx in extensions and abs(actual - cur) > 0.01:
            print(f"[fit] seg {idx}: {cur:.2f}s -> {actual:.2f}s (voice need)")
        t = seg["end"]

    total = t
    # Final guarantee: floors above (0.5s min) may re-overflow the cap on
    # many tiny segments — one last proportional squeeze.
    if src_duration and total > src_duration + 0.01:
        f = src_duration / total
        print(f"[fit] final squeeze x{f:.3f} ({total:.2f}s -> {src_duration:.2f}s)")
        t = 0.0
        for seg in segs:
            span = (float(seg["end"]) - float(seg["start"])) * f
            seg["start"] = round(t, 3)
            seg["end"] = round(t + span, 3)
            t = seg["end"]
        total = t
    # End-adjust: pin the timeline to the EXACT source end (millisecond
    # precision) so the video never stops fractionally early — works for
    # any random clip length.
    if src_duration and segs and total < src_duration - 0.01:
        segs[-1]["end"] = round(src_duration, 3)
        total = src_duration
        print(f"[fit] last segment pinned to exact source end ({src_duration:.3f}s)")
    print(f"[fit] total timeline: {total:.2f}s (source={src_duration:.2f}s)")


def build_meta(plan, info, video_key=None):
    title = (plan.get("title") or info.get("title") or "").strip()
    if not title or title.lower() in ("short", "shorts"):
        # Never ship the same generic title for every video — use the
        # per-video key so AI-plan fallbacks stay unique across a batch.
        title = f"Clip {video_key}" if video_key else "Short"
    meta = {
        "title": title[:95],
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
    if not plan.get("title") and apikeys.keys("openrouter", "OPENROUTER_API_KEYS", "OPENROUTER_API_KEY"):
        try:
            m = md.make_metadata(info.get("title") or meta["title"],
                                 info.get("description") or "",
                                 info.get("tags") or [])
            meta.update(m)
        except Exception as e:
            print(f"[warn] metadata fallback failed: {e}", file=sys.stderr)
    # Re-guard after fallback: OpenRouter can also return a generic title.
    t = (meta.get("title") or "").strip()
    if not t or t.lower() in ("short", "shorts"):
        meta["title"] = (f"Clip {video_key}" if video_key else "Short")[:95]
    # Near-duplicate guard: never ship the same/paraphrased title twice.
    try:
        meta["title"] = md.ensure_unique_title(
            meta["title"],
            description=meta.get("description", ""),
            tags=meta.get("tags"),
            video_key=video_key,
        )
    except Exception as e:
        print(f"[warn] title dedup failed: {e}", file=sys.stderr)
    return meta


def build_meta_raw(info, title_hint, video_key=None):
    """Raw (no-edit) mode: fresh title/description/tags from source text via
    OpenRouter — no video analysis, ready for instant upload."""
    meta = {
        "title": (title_hint or "Short")[:95],
        "description": (info.get("description") or ""),
        "tags": list(info.get("tags") or []),
    }
    if apikeys.keys("openrouter", "OPENROUTER_API_KEYS", "OPENROUTER_API_KEY"):
        try:
            m = md.make_metadata(meta["title"], info.get("description") or "", meta["tags"])
            meta.update(m)
            print(f"[meta] raw-mode metadata via OpenRouter")
        except Exception as e:
            print(f"[warn] raw metadata failed ({e}); using source title", file=sys.stderr)
    try:
        meta["title"] = md.ensure_unique_title(
            meta["title"],
            description=meta.get("description", ""),
            tags=meta.get("tags"),
            video_key=video_key,
        )
    except Exception as e:
        print(f"[warn] title dedup failed: {e}", file=sys.stderr)
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
    src_key = str(video.get("video_key") or vid)
    work = "work"
    os.makedirs(work, exist_ok=True)

    # Fetch task's dest_platforms for social upload selection.
    dest_platforms = ""
    task_uid = 0
    skip_edit = False
    task = None
    try:
        task_data = worker_get(f"/api/internal/tasks")
        tasks = task_data.get("tasks", [])
        task = next((t for t in tasks if t["id"] == video.get("task_id")), None)
        if task and task.get("dest_platforms"):
            dest_platforms = task["dest_platforms"]
        if task and task.get("user_id"):
            task_uid = int(task["user_id"])
        # Hijack: task.dest_provider 'youtube|noedit' = raw upload (no AI edit).
        if task and "noedit" in (task.get("dest_provider") or ""):
            skip_edit = True
    except Exception as e:
        print(f"[warn] failed to fetch task dest_platforms: {e}", file=sys.stderr)
    apply_voice_setting((task or {}).get("voice"))
    if task_uid:
        os.environ["PIPELINE_USER_ID"] = str(task_uid)
    os.environ["VIDEO_DEST_PLATFORMS"] = dest_platforms
    print(f"[social] dest platforms: {dest_platforms or '(none)'}")
    print(f"[mode] {'raw upload (no edit)' if skip_edit else 'edit pipeline'}")

    set_status(vid, status="downloading")
    src, info = dl.download(url, out_dir=work)
    print(f"[download] {src} ({os.path.getsize(src)//1024} KB)")

    # Shorts/Reels only: reject long source videos instead of clipping them.
    try:
        src_dur = an.probe_duration(src)
    except Exception:
        src_dur = None
    if src_dur and MAX_SECS and src_dur > MAX_SECS + 1:
        msg = f"skipped: {src_dur:.0f}s source is over the {MAX_SECS:.0f}s shorts limit"
        print(f"[skip] {msg}")
        set_status(vid, status="skipped", error=msg, duration=src_dur)
        return 0

    title_hint = (info.get("title") or "").strip() or (title or "").strip()
    if not title_hint or title_hint.lower() in ("short", "shorts"):
        # Same-title bug fix: every FB enqueue used to arrive as "Short" →
        # AI plan titles collapsed into near-identical names. Give each
        # video a unique, key-based hint instead.
        title_hint = f"Clip {src_key}"
    if is_cancelled(vid):
        print("[cancel] cancelled by user before edit — stopping")
        return 0

    if skip_edit:
        # Raw mode: fresh AI title/desc/tags, upload original as-is.
        set_status(vid, status="metadata", title=title_hint)
        meta = build_meta_raw(info, title_hint, video_key=src_key)
        print(f"[meta] {meta['title']}")
        out_path = src
        plan = None
    else:
        set_status(vid, status="editing", title=title_hint)

        # 1. Analyze -> JSON edit plan (Nemotron -> simple visual fallback).
        plan, used_ai = make_plan(src, title_hint, vid)
        print(f"[plan] {len(plan['segments'])} segs, ai={used_ai}, "
              f"audio={plan.get('audio_type')}, mode={plan.get('audio_mode')}")

        # 1b. Merge AI-observed source text with CV detection (any position),
        #     so old captions/watermarks are cropped/delogoed wherever they sit.
        try:
            plan["caption_regions"] = smart.merge_caption_regions(
                plan.get("caption_regions") or [],
                smart.detect_caption_regions(src))
            print(f"[captions] {len(plan['caption_regions'])} region(s): "
                  f"{[(r['band'], r['top_frac'], r['bottom_frac']) for r in plan['caption_regions']]}")
        except Exception:
            plan.setdefault("caption_regions", [])
            print("[captions] detection failed; keeping plan regions", file=sys.stderr)

        # 1c. Share the plan JSON with fastpoll (dashboard download button).
        post_plan("video", vid, plan)

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
        meta = build_meta(plan, info, video_key=src_key)
        print(f"[meta] {meta['title']}")

    # 5. Upload (dest may pin a channel: "youtube:<login_id>").
    if is_cancelled(vid):
        print("[cancel] cancelled by user before upload — stopping")
        return 0
    set_status(vid, status="uploading")
    pinned = False
    for part in (dest_platforms or "").split(","):
        part = part.strip()
        if part.startswith("youtube:"):
            try:
                yt.use_login(part.split(":", 1)[1], WORKER, TOKEN)
                pinned = True
            except Exception as e:
                print(f"[warn] yt login {part} failed ({e}); using default", file=sys.stderr)
            break
    if not pinned and task_uid:
        # Plain "youtube" dest → this task owner's login only (never another user's channel).
        try:
            e = worker_get(f"/api/internal/logins/env?user_id={task_uid}")
            has_rt = bool(e.get("YT_REFRESH_TOKEN"))
        except Exception as e:
            print(f"[warn] yt env for user {task_uid} failed ({e}); using default", file=sys.stderr)
            has_rt = None  # fetch failed → keep default env
        if has_rt:
            os.environ["YT_REFRESH_TOKEN"] = e["YT_REFRESH_TOKEN"]
            if e.get("YT_CLIENT_ID"):
                os.environ["YT_CLIENT_ID"] = e["YT_CLIENT_ID"]
            if e.get("YT_CLIENT_SECRET"):
                os.environ["YT_CLIENT_SECRET"] = e["YT_CLIENT_SECRET"]
            print(f"[yt] using login {e.get('login_id')} for user {task_uid}")
        elif has_rt is False:
            raise RuntimeError("no YouTube account connected for this user")
    access = yt.refresh_access_token()

    def _do_yt_upload():
        attempt = _do_yt_upload.n = getattr(_do_yt_upload, "n", 0) + 1
        set_status(vid, status="uploading", attempts=attempt)
        # Fresh access token per attempt (first attempt reuses the cached one).
        tok = access if attempt == 1 else yt.refresh_access_token()
        return yt.upload_video(
            out_path,
            title=meta["title"],
            description=meta["description"],
            tags=meta["tags"],
            privacy=PRIVACY,
            access_token=tok,
        )

    video_id = upload_with_retry(_do_yt_upload, attempts=3, backoff=20, label="youtube upload")
    if not video_id:
        raise RuntimeError("youtube upload returned no video id")
    print(f"[upload] youtube id {video_id}")

    # 5b. Social media upload (TikTok / Instagram / Facebook via cookies).
    social_results = {}
    if dest_platforms:
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
