"""Facebook Page video upload via the Graph API (no browser/cookies).

Used by BOTH the automation pipeline and Quick Upload when a task's
dest_platforms include "facebook". Requires two env vars (repo secrets,
wired in process.yml for both the automation and manual jobs):
  FB_PAGE_TOKEN  — never-expiring Page access token
  FB_PAGE_ID     — numeric Page id

Reels (<=180s) go through /video_reels (lands in the Reels tab); anything
longer — or a rejected reel upload — falls back to /videos.

CLI (e2e test, never prints the token):
  python pipeline/upload_fb.py <video> [--published false]
  python pipeline/upload_fb.py --delete <video_id>
"""
import json
import os
import subprocess
import sys

import requests

GRAPH = f"https://graph.facebook.com/{os.getenv('GRAPH_VERSION', 'v23.0')}"


def _creds():
    tok = (os.getenv("FB_PAGE_TOKEN") or "").strip()
    pid = (os.getenv("FB_PAGE_ID") or "").strip()
    if not tok or not pid:
        raise RuntimeError("FB_PAGE_TOKEN / FB_PAGE_ID not set")
    return tok, pid


def _duration(path):
    """Video length in seconds via ffprobe (None if unavailable)."""
    try:
        r = subprocess.run(
            [os.getenv("FFPROBE_BIN", "ffprobe"), "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20)
        return float(r.stdout.strip())
    except Exception:
        return None


def _post_video(endpoint, path, tok, desc, published):
    with open(path, "rb") as f:
        r = requests.post(
            f"{GRAPH}/{endpoint}",
            data={"access_token": tok,
                  "description": desc or "",
                  "published": "true" if published else "false"},
            files={"source": (os.path.basename(path), f, "video/mp4")},
            timeout=900)
    try:
        return r.status_code, (r.json() if r.content else {})
    except Exception:
        return r.status_code, {"raw": (r.text or "")[:300]}


def upload(video_path, description="", published=True, is_reel=None):
    """Upload to the configured Page. Returns
    {"success", "id", "url", "kind", "duration"} — never echoes the token."""
    tok, pid = _creds()
    if not os.path.isfile(video_path):
        raise FileNotFoundError(video_path)
    dur = _duration(video_path)
    if is_reel is None:
        is_reel = bool(dur is not None and dur <= 180)

    kind = "reel" if is_reel else "video"
    endpoint = f"{pid}/video_reels" if is_reel else f"{pid}/videos"
    code, res = _post_video(endpoint, video_path, tok, description, published)
    if is_reel and code >= 400:
        # video_reels is picky (codec/length/state); /videos always works.
        print(f"[fb] video_reels rejected (HTTP {code}); "
              f"falling back to /videos", file=sys.stderr)
        code, res = _post_video(f"{pid}/videos", video_path, tok,
                                description, published)
        kind = "video"
    vid = str(res.get("id") or "")
    if code >= 400 or not vid:
        err = res.get("error")
        if isinstance(err, dict):
            err = err.get("message") or err
        raise RuntimeError(f"facebook upload failed HTTP {code}: "
                           f"{str(err)[:300]}")
    url = res.get("link") or f"https://www.facebook.com/{pid}/videos/{vid}"
    print(f"[fb] uploaded ({kind}, published={published}): {url}")
    return {"success": True, "id": vid, "url": url, "kind": kind,
            "duration": dur}


def delete(video_id):
    """Remove a video from the Page (e2e cleanup)."""
    tok, _pid = _creds()
    r = requests.delete(f"{GRAPH}/{video_id}",
                        params={"access_token": tok}, timeout=60)
    try:
        ok = r.status_code == 200 and bool((r.json() or {}).get("success"))
    except Exception:
        ok = False
    print(f"[fb] delete {video_id} -> ok={ok}")
    return ok


if __name__ == "__main__":
    args = sys.argv[1:]
    published = True
    do_delete = None
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--published":
            i += 1
            published = (i < len(args) and args[i].lower() != "false")
        elif a == "--delete":
            i += 1
            do_delete = args[i] if i < len(args) else None
        else:
            rest.append(a)
        i += 1
    if do_delete:
        sys.exit(0 if delete(do_delete) else 1)
    if not rest:
        print("Usage: python pipeline/upload_fb.py <video> "
              "[--published false] [--delete ID]")
        sys.exit(2)
    out = upload(rest[0],
                 description="NOVA e2e test" if not published else "",
                 published=published)
    print(json.dumps(out))
