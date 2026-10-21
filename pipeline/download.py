"""Download a video with yt-dlp. Supports FB, Instagram, TikTok, YouTube, Twitter."""
import os
import glob
import sys
import yt_dlp


def _cookies_opt():
    """Optional cookies file (base64-decoded to a temp path by the workflow)."""
    path = os.getenv("COOKIES_FILE")
    if path and os.path.exists(path):
        return {"cookiefile": path}
    return {}


def _yt_attempts(url):
    """YouTube: ordered extra_opts attempts. Both skip cookies — the browser
    cookie export is partial (missing session cookies) and actively breaks
    web/visionos clients with 'page needs to be reloaded'. visionos gives
    full DASH formats; android falls back to progressive 360p."""
    u = (url or "").lower()
    if "youtube.com" in u or "youtu.be" in u:
        return [
            {"extractor_args": {"youtube": {"player_client": ["visionos"]}}},
            {"extractor_args": {"youtube": {"player_client": ["android"]}}},
        ]
    return [{}]


def download(url, out_dir="work", max_secs=None):
    """Download `url` into out_dir. Returns (file_path, info_dict)."""
    os.makedirs(out_dir, exist_ok=True)
    base = {
        "outtmpl": os.path.join(out_dir, "src.%(ext)s"),
        "merge_output_format": "mp4",
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        # Prefer a single progressive file when possible (lower peak RAM).
        "prefer_free_formats": False,
    }
    last_err = None
    info = None
    for extra in _yt_attempts(url):
        opts = dict(base)
        opts.update(extra)
        if not extra:
            opts.update(_cookies_opt())
        if max_secs:
            opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [(0, max_secs)])
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            last_err = None
            break
        except Exception as e:
            last_err = e
            print(f"[download] yt attempt failed ({extra}): {e}", file=sys.stderr)
    if last_err is not None:
        raise last_err

    # Find the produced file.
    candidates = glob.glob(os.path.join(out_dir, "src.*"))
    # Prefer mp4, ignore part/tmp files.
    candidates = [c for c in candidates if not c.endswith((".part", ".ytdl"))]
    if not candidates:
        raise RuntimeError("download produced no file")
    candidates.sort(key=lambda p: (0 if p.endswith(".mp4") else 1, -os.path.getsize(p)))
    return candidates[0], info


def flat_latest(url):
    """Return the latest entry (id, title, url) without downloading. For polling."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": 5,
        "skip_download": True,
    }
    opts.update(_cookies_opt())
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries") or []
    if not entries:
        # Single video page.
        if info.get("id"):
            return {"id": info.get("id"), "title": info.get("title"), "url": info.get("webpage_url") or url}
        return None
    e = entries[0]
    return {
        "id": e.get("id"),
        "title": e.get("title"),
        "url": e.get("url") or e.get("webpage_url") or url,
    }
