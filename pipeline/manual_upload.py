"""Manual upload pipeline — download, edit, generate metadata, upload.

Called by Render fastpoll or GitHub Actions when a manual job is queued.

Flow:
  1. Pick up next queued manual job from worker
  2. Download best quality video via yt-dlp
  3. Edit based on destinations:
     - YouTube selected → full AI pipeline (analyze + narrate + apply)
     - TikTok/Insta/FB only → smart trim + vertical crop + captions
  4. Generate metadata via OpenRouter (title, desc, tags, hashtags)
  5. Upload to selected platforms
  6. Report results

Env:
  WORKER_URL, INTERNAL_TOKEN    worker API
  NVIDIA_API_KEY                AI edit planning (optional)
  OPENROUTER_API_KEY            metadata generation (optional)
  YT_*                          YouTube upload
  PRIVACY                       public|unlisted|private
"""
import os
import sys
import json
import time
import traceback
import subprocess

import requests

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
        "max_filesize": 500 * 1024 * 1024,  # 500MB cap
        "socket_timeout": 30,
        "retries": 3,
    }

    # Use cookies if available
    cookies_file = os.getenv("COOKIES_FILE", "")
    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        # Ensure mp4 extension after merge
        if not filepath.endswith(".mp4"):
            base = os.path.splitext(filepath)[0]
            if os.path.exists(base + ".mp4"):
                filepath = base + ".mp4"

    return filepath, info


def generate_metadata(title_hint, description_hint, dest_platforms):
    """Generate platform-specific metadata via OpenRouter."""
    meta = {
        "title": title_hint[:100] if title_hint else "Check this out!",
        "description": description_hint[:500] if description_hint else "",
        "tags": [],
        "hashtags": [],
    }

    if not OPENROUTER_KEY:
        # Fallback: basic hashtags from title
        words = [w for w in (title_hint or "").split() if len(w) > 2][:5]
        meta["hashtags"] = [f"#{w.lower().replace(' ', '')}" for w in words]
        meta["tags"] = words
        return meta

    try:
        platforms_str = ", ".join(dest_platforms)
        prompt = f"""Analyze this video metadata and generate optimized content for social media upload.

Video title: {title_hint}
Video description: {description_hint}
Upload platforms: {platforms_str}

Return a JSON object with:
- "title": catchy, SEO-optimized title (max 100 chars)
- "description": engaging description with line breaks (max 3000 chars for YouTube, 2200 for others)
- "tags": array of 10-15 relevant SEO tags
- "hashtags": array of 10-15 trending hashtags (with # prefix)

Make it viral-worthy, use relevant trending hashtags. Return ONLY the JSON object."""

        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "meta-llama/llama-3.1-8b-instruct:free",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1000,
                "temperature": 0.7,
            },
            timeout=30,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]

        # Extract JSON from response
        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            ai_meta = json.loads(json_match.group())
            meta["title"] = ai_meta.get("title", meta["title"])[:100]
            meta["description"] = ai_meta.get("description", meta["description"])[:3000]
            meta["tags"] = ai_meta.get("tags", [])[:15]
            meta["hashtags"] = ai_meta.get("hashtags", [])[:15]
    except Exception as e:
        print(f"[meta] AI generation failed: {e}")

    return meta


def social_edit(src_path, out_path, target="vertical"):
    """Simple edit for social media: crop to vertical, trim, add basic styling.
    
    Uses ffmpeg directly — no AI pipeline needed.
    """
    import subprocess

    # Get duration
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", src_path],
        capture_output=True, text=True
    )
    duration = float(json.loads(probe.stdout).get("format", {}).get("duration", "30"))

    # Trim to max length
    trim_duration = min(duration, MAX_SECS)

    if target == "vertical":
        # Crop to 9:16 vertical, scale to 1080x1920
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "setsar=1"
        )
    else:
        # Keep original aspect, scale to 1080p
        vf = "scale=1080:-2:force_original_aspect_ratio=decrease"

    cmd = [
        "ffmpeg", "-y",
        "-i", src_path,
        "-t", str(trim_duration),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[edit] ffmpeg error: {result.stderr[:500]}")
        # Fallback: just copy with trim
        cmd2 = [
            "ffmpeg", "-y",
            "-i", src_path,
            "-t", str(trim_duration),
            "-c", "copy",
            out_path
        ]
        subprocess.run(cmd2, capture_output=True)

    return out_path


def upload_youtube(video_path, meta):
    """Upload to YouTube via OAuth."""
    import ytauth as yt

    access = yt.refresh_access_token()
    video_id = yt.upload_video(
        video_path,
        title=meta["title"],
        description=meta["description"] + "\n\n" + " ".join(meta.get("hashtags", [])[:10]),
        tags=meta["tags"],
        privacy=PRIVACY,
        access_token=access,
    )
    return f"https://youtube.com/watch?v={video_id}"


def main():
    # Check if specific job ID is given via env
    forced_id = os.getenv("MANUAL_JOB_ID", "")

    if forced_id:
        # Atomically claim: queued -> processing (only one runner wins)
        try:
            r = requests.post(
                f"{WORKER}/api/internal/manual/{forced_id}/claim",
                headers=_h(),
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[manual] failed to claim job {forced_id}: {e}")
            return
        if not data.get("claimed"):
            status = (data.get("job") or {}).get("status", "unknown")
            print(f"[manual] job {forced_id} not claimable (status: {status}) — another runner has it or it's done")
            return
        job = data.get("job")
    else:
        print("[manual] checking for queued jobs...")
        data = worker_get("/api/internal/manual/next")
        job = data.get("job")
        if not job:
            print("[manual] no queued jobs")
            return
        # Claim it
        r = requests.post(
            f"{WORKER}/api/internal/manual/{job['id']}/claim",
            headers=_h(),
            timeout=30,
        )
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
        print(f"[download] {src_path} ({os.path.getsize(src_path) // 1024} KB)")
        print(f"[download] title: {title_hint}")

        # 2. Determine edit mode
        update_job(job_id, status="editing", title=title_hint)

        needs_ai = "youtube" in dest_platforms
        final_path = src_path

        if needs_ai:
            # Full AI pipeline for YouTube
            print("[edit] running AI pipeline for YouTube...")
            try:
                import analyze as an
                import smart
                import apply as ap
                import voice as vc

                plan, used_ai = an.make_plan(src_path, title_hint) if hasattr(an, 'make_plan') else (None, False)
                if plan is None:
                    # Use smart module
                    plan = smart.make_plan(src_path, title_hint)
                    used_ai = False

                voice_wavs = {}
                if used_ai and NVIDIA_KEY:
                    try:
                        voice_wavs = vc.make_voices(plan, work_dir)
                    except Exception:
                        pass

                final_path = ap.apply_plan(src_path, plan, workdir=work_dir, voice_wavs=voice_wavs)
                print(f"[edit] AI pipeline done: {final_path}")
            except Exception as e:
                print(f"[edit] AI pipeline failed, falling back to simple edit: {e}")
                final_path = social_edit(src_path, os.path.join(work_dir, "output.mp4"), target="vertical")
        else:
            # Simple social edit for TikTok/Insta/FB
            print("[edit] running social edit pipeline...")
            final_path = social_edit(src_path, os.path.join(work_dir, "output.mp4"), target="vertical")
            print(f"[edit] social edit done: {final_path}")

        # 3. Generate metadata
        update_job(job_id, status="generating_metadata")
        meta = generate_metadata(title_hint, desc_hint, dest_platforms)
        print(f"[meta] title: {meta['title']}")
        print(f"[meta] hashtags: {' '.join(meta['hashtags'][:5])}")

        # 4. Upload to platforms
        update_job(job_id, status="uploading")
        results = {}

        # YouTube
        if "youtube" in dest_platforms:
            try:
                yt_url = upload_youtube(final_path, meta)
                results["youtube"] = {"success": True, "url": yt_url}
                print(f"[upload] youtube: {yt_url}")
            except Exception as e:
                results["youtube"] = {"success": False, "error": str(e)}
                print(f"[upload] youtube failed: {e}")

        # Social platforms via cookies
        social_dests = [p for p in dest_platforms if p in ("tiktok", "instagram", "facebook")]
        if social_dests:
            os.environ["VIDEO_DEST_PLATFORMS"] = ",".join(social_dests)
            try:
                import upload_social as us
                caption = f"{meta['title']}\n\n{meta['description']}\n\n{' '.join(meta['hashtags'][:10])}"
                social_results = us.upload_all(final_path, meta["title"], caption)
                for plat, ok in social_results.items():
                    results[plat] = {"success": bool(ok)}
                    print(f"[upload] {plat}: {'ok' if ok else 'failed'}")
            except Exception as e:
                print(f"[upload] social failed: {e}")
                for p in social_dests:
                    results[p] = {"success": False, "error": str(e)}

        # 5. Report
        update_job(job_id, status="done", result=results, title=meta["title"])
        print(f"[manual] job {job_id} complete!")
        print(json.dumps(results, indent=2))

    except Exception as e:
        traceback.print_exc()
        update_job(job_id, status="failed", error=str(e))
        print(f"[manual] job {job_id} failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
