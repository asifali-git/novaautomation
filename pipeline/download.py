"""Download a video with yt-dlp. Supports FB, Instagram, TikTok, YouTube, Twitter.

YouTube: uses FastSaver API (FASTSAVER_API_KEY) — no bot-check, works from
any IP. Falls back to yt-dlp only if the key is missing. Everything else keeps
the direct yt-dlp path.
"""
import os
import glob
import sys
import yt_dlp
import requests
import apikeys

FASTSAVER_BASE = "https://api.fastsaver.io/v1"
FS_DEAD_CODES = (401, 402, 403, 429)


def _cookies_opt():
    """Optional cookies file (base64-decoded to a temp path by the workflow)."""
    path = os.getenv("COOKIES_FILE")
    if path and os.path.exists(path):
        return {"cookiefile": path}
    return {}


def _is_youtube(url):
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def _fastsaver_keys():
    return apikeys.keys("fastsaver", "FASTSAVER_API_KEYS", "FASTSAVER_API_KEY")


def _quality_ladder():
    """Start at the configured quality, then fall back to lower ones."""
    ladder = [1080, 720, 480, 360]
    want = apikeys.setting("video_quality", "")
    if want:
        try:
            q = int(want)
        except (TypeError, ValueError):
            return [f"{n}p" for n in ladder]
        ladder = ([q] + [n for n in ladder if n < q])[:4]
    return [f"{n}p" for n in ladder]


def _download_fastsaver(url, out_dir):
    """YouTube via FastSaver API. Rotates to the next key on quota/auth errors."""
    keys = _fastsaver_keys()
    if not keys:
        return None
    for idx, key in enumerate(keys):
        headers = {"X-Api-Key": key}
        for quality in _quality_ladder():
            try:
                r = requests.post(
                    f"{FASTSAVER_BASE}/youtube/download",
                    json={"url": url, "format": quality},
                    headers=headers,
                    timeout=120,
                )
                if r.status_code in FS_DEAD_CODES:
                    print(
                        f"[download] fastsaver key {idx + 1}/{len(keys)}: HTTP {r.status_code} -> next key",
                        file=sys.stderr,
                    )
                    apikeys.report("fastsaver", idx, False, f"HTTP {r.status_code}")
                    break
                data = r.json() if r.content else {}
                if not data.get("ok") or not data.get("download_url"):
                    print(f"[download] fastsaver {quality}: {data.get('detail', r.status_code)}", file=sys.stderr)
                    continue
                path = os.path.join(out_dir, "src.mp4")
                with requests.get(data["download_url"], stream=True, timeout=600, allow_redirects=True) as dl:
                    dl.raise_for_status()
                    with open(path, "wb") as f:
                        for chunk in dl.iter_content(1 << 20):
                            f.write(chunk)
                sz = os.path.getsize(path)
                print(f"[download] fastsaver ok {quality} ({sz} bytes)", file=sys.stderr)
                apikeys.report("fastsaver", idx, True)
                return path
            except Exception as e:
                print(f"[download] fastsaver {quality} failed: {e}", file=sys.stderr)
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
    # YouTube: FastSaver API (no bot-check from any IP).
    if _is_youtube(url) and apikeys.setting("download_provider", "fastsaver") != "ytdlp":
        p = _download_fastsaver(url, out_dir)
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
