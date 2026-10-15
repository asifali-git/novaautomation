"""Manual upload pipeline — download, edit, generate metadata, upload.

Called by GitHub Actions when a manual job is queued (Quick Upload).

Flow:
  1. Claim job atomically (queued -> processing)
  2. Download best quality video via yt-dlp
  3. Edit based on destinations:
     - YouTube selected  -> FULL AI pipeline (Nemotron analyze -> caption-region
                            detection -> Kokoro voiceover -> fit segments ->
                            ffmpeg apply: crop/speed/color/sharpness/captions/narration)
     - Social only       -> simple fallback (smart.analyze_video: crop+speed+color+sharpness)
  4. Metadata from AI plan (fallback: OpenRouter metadata.py)
  5. Upload to selected platforms
  6. Report results

Env:
  WORKER_URL, INTERNAL_TOKEN    worker API
  NVIDIA_API_KEY                Nemotron omni key (AI analysis)
  OPENROUTER_API_KEY            metadata fallback + smart seeded plans
  KOKORO_DIR                    kokoro model cache dir
  YT_*                          YouTube upload
  PRIVACY                       public|unlisted|private
  MAX_SECS                      cap clip length (default 60)
"""
import os
import sys
import json
import time
import traceback
import subprocess

import requests

import analyze as an
import smart
import apply as ap
import voice as vc
import metadata as md
import ytauth as yt
import upload_social as us

WORKER = os.getenv("WORKER_URL", "").rstrip("/")
TOKEN = os.getenv("INTERNAL_TOKEN", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
NVIDIA_KEY = os.getenv("NVIDIA_API_KEY", "")
PRIVACY = os.getenv("PRIVACY", "public")
MAX_SECS = float(os.getenv("MAX_SECS", "60"))


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


def update_job(job_id, **fields):
    try:
        worker_patch(f"/api/internal/manual/{job_id}", fields)
    except Exception as e:
        print(f"[warn] failed to update job {job_id}: {e}")


def download_best(url, out_dir="work"):
    """Download best quality video via yt-dlp."""
    os.makedirs(out_dir, exist_ok=True)
    import yt_dlp

    opts = {
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "max_filesize": 500 * 1024 * 1024,
        "socket_timeout": 30,
        "retries": 3,
    }

    cookies_file = os.getenv("COOKIES_FILE", "")
    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        if not filepath.endswith(".mp4"):
            base = os.path.splitext(filepath)[0]
            if os.path.exists(base + ".mp4"):
                filepath = base + ".mp4"

    return filepath, info


# ── AI pipeline (YouTube) ──────────────────────────────────────────────────

def make_plan(src, title_hint):
    """Nemotron AI analysis -> fallback to smart simple plan."""
    try:
        plan = an.analyze_video(src, title_hint=title_hint)
        print(f"[plan] AI: {len(plan['segments'])} segs, audio={plan.get('audio_type')}, "
              f"mode={plan.get('audio_mode')}, style={plan.get('style')}")
        return plan, True
    except Exception as e:
        print(f"[plan] nemotron failed ({e}); using simple fallback", file=sys.stderr)
        plan = smart.analyze_video(src, title_hint=title_hint)
        print(f"[plan] simple: {len(plan['segments'])} segs, visual-only")
        return plan, False


def detect_captions(src, plan):
    """Detect burned-in source captions/watermarks for removal."""
    try:
        regions = smart.detect_caption_regions(src)
        plan["caption_regions"] = regions
        print(f"[captions] detected {len(regions)} region(s): "
              f"{[(r['band'], r['action']) for r in regions]}")
    except Exception as e:
        print(f"[captions] detection failed: {e}", file=sys.stderr)
        plan["caption_regions"] = []


def make_voices(plan, work, used_ai=True):
    """Kokoro TTS narration. Only for AI path (fallback has no narration)."""
    wavs = {}
    if not used_ai:
        return wavs
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
    """Extend segment durations so narration plays at natural speed."""
    if not voice_wavs:
        return
    ffprobe = os.getenv("FFPROBE_BIN", "ffprobe")
    segs = plan["segments"]
    if src_duration is None:
        src_duration = max(float(s.get("end", 0)) for s in segs) if segs else 30.0

    extensions = {}
    for seg in segs:
        idx = seg.get("index")
        if idx not in voice_wavs:
            continue
        wav_path = voice_wavs[idx]
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", wav_path],
                capture_output=True, text=True, timeout=10)
            wav_dur = float(r.stdout.strip())
        except Exception:
            continue
        speed = abs(float(seg.get("speed", 1.0)) or 1.0)
        if speed < 0.01:
            speed = 1.0
        needed_src = (wav_dur + 0.15) * speed
        current_src = float(seg["end"]) - float(seg["start"])
        if needed_src > current_src:
            extensions[idx] = min(needed_src, current_src * 2.2)

    last_with_wav = max(
        (s.get("index") for s in segs if s.get("index") in voice_wavs),
        default=None)

    t = 0.0
    for seg in segs:
        idx = seg.get("index")
        current_src = float(seg["end"]) - float(seg["start"])
        needed = extensions.get(idx, current_src)
        remaining = src_duration - t
        actual = min(needed, remaining)
        if actual < 0.5:
            actual = 0.5
        seg["start"] = round(t, 3)
        seg["end"] = round(t + actual, 3)
        t = seg["end"]
    print(f"[fit] total timeline: {t:.2f}s (source={src_duration:.2f}s)")


def build_meta(plan, info):
    """Build upload metadata from AI plan (fallback: OpenRouter)."""
    meta = {
        "title": (plan.get("title") or info.get("title") or "Short")[:95],
        "description": (plan.get("description") or info.get("description") or ""),
        "tags": plan.get("tags") or [],
        "hashtags": plan.get("hashtags") or [],
    }
    # Append narration to description for context/SEO.
    narrs = [s["narration"] for s in plan["segments"] if (s.get("narration") or "").strip()]
    if narrs:
        meta["description"] = (meta["description"] + "\n\n" + " ".join(narrs))[:4800]
    if meta["hashtags"]:
        meta["description"] = (meta["description"] + "\n\n" + " ".join(meta["hashtags"]))[:4800]
    # If plan gave no title, use OpenRouter metadata.
    if not plan.get("title") and OPENROUTER_KEY:
        try:
            m = md.make_metadata(meta["title"], info.get("description") or "", meta["tags"])
            meta.update(m)
        except Exception as e:
            print(f"[warn] metadata fallback failed: {e}", file=sys.stderr)
    return meta


# ── Upload ─────────────────────────────────────────────────────────────────

def upload_youtube(video_path, meta):
    """Upload to YouTube via OAuth."""
    access = yt.refresh_access_token()
    hashtags = " ".join(meta.get("hashtags", [])[:10])
    desc = meta["description"]
    if hashtags and hashtags not in desc:
        desc = desc + "\n\n" + hashtags
    video_id = yt.upload_video(
        video_path,
        title=meta["title"][:100],
        description=desc[:5000],
        tags=meta["tags"],
        privacy=PRIVACY,
        access_token=access,
    )
    return f"https://youtube.com/watch?v={video_id}"


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    forced_id = os.getenv("MANUAL_JOB_ID", "")

    if forced_id:
        try:
            r = requests.post(
                f"{WORKER}/api/internal/manual/{forced_id}/claim",
                headers=_h(), timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[manual] failed to claim job {forced_id}: {e}")
            return
        if not data.get("claimed"):
            status = (data.get("job") or {}).get("status", "unknown")
            print(f"[manual] job {forced_id} not claimable (status: {status})")
            return
        job = data.get("job")
    else:
        print("[manual] checking for queued jobs...")
        data = worker_get("/api/internal/manual/next")
        job = data.get("job")
        if not job:
            print("[manual] no queued jobs")
            return
        r = requests.post(
            f"{WORKER}/api/internal/manual/{job['id']}/claim",
            headers=_h(), timeout=30)
        r.raise_for_status()
        if not r.json().get("claimed"):
            print(f"[manual] job {job['id']} claimed by another runner; skipping")
            return

    job_id = job["id"]
    url = job["url"]
    dest_str = job.get("dest_platforms", "youtube")
    dest_platforms = [p.strip() for p in dest_str.split(",") if p.strip()]

    print(f"[manual] job {job_id}: {url}")
    print(f"[manual] destinations: {dest_platforms}")

    update_job(job_id, status="downloading")
    work_dir = f"manual_{job_id}"
    os.makedirs(work_dir, exist_ok=True)

    try:
        # 1. Download
        src_path, info = download_best(url, out_dir=work_dir)
        title_hint = info.get("title", "")
        desc_hint = info.get("description", "")
        size_kb = os.path.getsize(src_path) // 1024
        print(f"[download] {src_path} ({size_kb} KB)")
        print(f"[download] title: {title_hint}")

        # 2. Analyze -> edit plan
        update_job(job_id, status="analyzing", title=title_hint[:200])
        needs_ai = "youtube" in dest_platforms
        plan, used_ai = make_plan(src_path, title_hint)

        # 2b. Detect burned-in captions for removal (crop/delogo).
        detect_captions(src_path, plan)

        # 2c. Kokoro voiceover (AI path only).
        voice_wavs = {}
        if used_ai:
            update_job(job_id, status="voiceover")
            voice_wavs = make_voices(plan, work_dir, used_ai=used_ai)
            # Extend segments so narration plays at natural speed.
            if voice_wavs:
                try:
                    src_dur = an.probe_duration(src_path)
                except Exception:
                    src_dur = None
                fit_segments_to_voices(plan, voice_wavs, src_duration=src_dur)

        # 3. Apply edits via ffmpeg (crop/speed/color/sharpness/captions/narration).
        update_job(job_id, status="editing")
        print(f"[edit] applying plan ({len(plan['segments'])} segs, "
              f"{len(voice_wavs)} voice tracks, "
              f"{len(plan.get('caption_regions') or [])} caption regions)...")
        final_path = ap.apply_plan(src_path, plan, workdir=work_dir, voice_wavs=voice_wavs)
        final_kb = os.path.getsize(final_path) // 1024
        print(f"[edit] done: {final_path} ({final_kb} KB)")

        # 4. Metadata from AI plan.
        update_job(job_id, status="generating_metadata")
        meta = build_meta(plan, info)
        print(f"[meta] title: {meta['title']}")
        if meta.get("hashtags"):
            print(f"[meta] hashtags: {' '.join(meta['hashtags'][:5])}")

        # 5. Upload to platforms.
        update_job(job_id, status="uploading")
        results = {}

        if "youtube" in dest_platforms:
            try:
                yt_url = upload_youtube(final_path, meta)
                results["youtube"] = {"success": True, "url": yt_url}
                print(f"[upload] youtube: {yt_url}")
            except Exception as e:
                results["youtube"] = {"success": False, "error": str(e)}
                print(f"[upload] youtube failed: {e}")

        social_dests = [p for p in dest_platforms if p in ("tiktok", "instagram", "facebook")]
        if social_dests:
            os.environ["VIDEO_DEST_PLATFORMS"] = ",".join(social_dests)
            try:
                caption = f"{meta['title']}\n\n{meta['description']}\n\n{' '.join(meta.get('hashtags', [])[:10])}"
                social_results = us.upload_all(final_path, meta["title"], caption)
                for plat, ok in social_results.items():
                    results[plat] = {"success": bool(ok)}
                    print(f"[upload] {plat}: {'ok' if ok else 'failed'}")
            except Exception as e:
                print(f"[upload] social failed: {e}")
                for p in social_dests:
                    results[p] = {"success": False, "error": str(e)}

        # 6. Report.
        update_job(job_id, status="done", result=results, title=meta["title"][:200])
        print(f"[manual] job {job_id} complete!")
        print(json.dumps(results, indent=2))

    except Exception as e:
        traceback.print_exc()
        update_job(job_id, status="failed", error=str(e)[:500])
        print(f"[manual] job {job_id} failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
