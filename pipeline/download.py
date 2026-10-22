"""Download a video with yt-dlp. Supports FB, Instagram, TikTok, YouTube, Twitter.

YouTube: if YT_API_URL + YT_API_KEY are set, download goes through the
FastAPI yt download service (ytapi_server.py on a residential-IP machine).
Everything else keeps the direct yt-dlp path.
"""
import os
import glob
import sys
import yt_dlp
import requests


def _cookies_opt():
    """Optional cookies file (base64-decoded to a temp path by the workflow)."""
    path = os.getenv("COOKIES_FILE")
    if path and os.path.exists(path):
        return {"cookiefile": path}
    return {}


def _is_youtube(url):
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def _download_via_api(url, out_dir, max_secs=None):
    """Try FastAPI yt download service. Returns local file path or None."""
    api_url = os.getenv("YT_API_URL", "").strip()
    api_key = os.getenv("YT_API_KEY", "").strip()
    if not api_url or not api_key:
        return None
    body = {"url": url}
    if max_secs:
        body["max_secs"] = int(max_secs)
    try:
        r = requests.post(
            api_url.rstrip("/") + "/download",
            json=body,
            headers={"X-API-Key": api_key},
            timeout=600,
        )
        if r.status_code == 200 and r.content:
            path = os.path.join(out_dir, "src.mp4")
            with open(path, "wb") as f:
                f.write(r.content)
            print(f"[download] via API ok ({len(r.content)} bytes)", file=sys.stderr)
            return path
        print(f"[download] API status={r.status_code} body={r.text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[download] API call failed: {e}", file=sys.stderr)
    return None


def _yt_attempts(url):
    """YouTube: ordered (extra_opts, fmt, use_cookies) attempts.
    web+cookies is the only combo YouTube accepts from datacenter IPs
    (ytprobe: all no-cookie clients -> bot-check; non-web clients+cookies
    -> zero formats). visionos/android no-cookie kept as home-IP fallbacks."""
    u = (url or "").lower()
    if "youtube.com" in u or "youtu.be" in u:
        return [
            ({"extractor_args": {"youtube": {"player_client": ["web"]}}}, "bv*+ba/b", True),
            ({"extractor_args": {"youtube": {"player_client": ["visionos"]}}},
             "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b", False),
            ({"extractor_args": {"youtube": {"player_client": ["android"]}}}, "b[ext=mp4]/b", False),
        ]
    return [({}, "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b", True)]


def download(url, out_dir="work", max_secs=None):
    """Download `url` into out_dir. Returns (file_path, info_dict)."""
    os.makedirs(out_dir, exist_ok=True)
    # YouTube: prefer FastAPI service (residential IP) when configured.
    if _is_youtube(url) and os.getenv("YT_API_URL", "").strip():
        p = _download_via_api(url, out_dir, max_secs=max_secs)
        if p and os.path.exists(p) and os.path.getsize(p) > 0:
            return p, {"id": None, "title": None, "webpage_url": url}
    base = {
        "outtmpl": os.path.join(out_dir, "src.%(ext)s"),
        "merge_output_format": "mp4",
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
    for extra, fmt, use_cookies in _yt_attempts(url):
        opts = dict(base)
        opts.update(extra)
        opts["format"] = fmt
        if use_cookies:
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
            print(f"[download] yt attempt failed (cookies={use_cookies}, {extra}): {e}", file=sys.stderr)
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
