"""Facebook Page video upload via the Graph API (no browser/cookies).

Used by BOTH the automation pipeline and Quick Upload when a task's
dest_platforms include "facebook".

Credential registry (first source wins):
  FB_PAGES_JSON env  — {"user_token": "...", "pages": [{id,name,token}, ...]}
                       (repo secret; all connected FB pages)
  local fb asif ali.txt — same data on the dev machine (gitignored)
  FB_PAGE_TOKEN / FB_PAGE_ID env pair — single default page (back-compat)

Page selection: upload(..., page=<page_id or page_name>) picks a registry
page; None falls back to FB_PAGE_ID, else the first registry page.

Reels (<=180s) go through /video_reels (lands in the Reels tab); anything
longer — or a rejected reel upload — falls back to /videos.

CLI (e2e test, never prints the token):
  python pipeline/upload_fb.py <video> [--published false] [--page ID_OR_NAME]
  python pipeline/upload_fb.py --delete <video_id> [--page ID_OR_NAME]
"""
import json
import os
import re
import subprocess
import sys

import requests

GRAPH = f"https://graph.facebook.com/{os.getenv('GRAPH_VERSION', 'v23.0')}"


def _pages_from_file():
    """(user_token, pages) parsed from the local fb asif ali.txt store."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "fb asif ali.txt")
    if not os.path.isfile(path):
        return None, []
    raw = open(path, encoding="utf-8", errors="replace").read()
    um = re.search(r"^FB_USER_TOKEN=(\S+)", raw, re.M)
    user_tok = um.group(1) if um else None
    pages = []
    for m in re.finditer(r"\{[^{}]*\"data\"\s*:\s*\[", raw):
        start = m.start()
        depth = 0
        for i in range(start, min(len(raw), start + 20000)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        j = json.loads(raw[start:i + 1])
                    except Exception:
                        j = None
                    if (isinstance(j, dict) and isinstance(j.get("data"), list)
                            and j["data"] and "access_token" in j["data"][0]):
                        pages = [
                            {"id": str(p.get("id") or ""),
                             "name": str(p.get("name") or ""),
                             "token": str(p.get("access_token") or "")}
                            for p in j["data"] if p.get("access_token")
                        ]
                    break
        if pages:
            break
    return user_tok, pages


def fb_registry():
    """{"user_token": str|None, "pages": [{id,name,token}]} — never logged."""
    raw = (os.getenv("FB_PAGES_JSON") or "").strip().strip('"').strip("'")
    if raw:
        try:
            reg = json.loads(raw)
            pages = [
                {"id": str(p.get("id") or ""),
                 "name": str(p.get("name") or ""),
                 "token": str(p.get("token") or p.get("access_token") or "")}
                for p in (reg.get("pages") or [])
                if (p.get("token") or p.get("access_token")) and p.get("id")
            ]
            if pages:
                return {"user_token": (reg.get("user_token") or None),
                        "pages": pages}
        except Exception:
            pass
    try:
        user_tok, pages = _pages_from_file()
        if pages:
            return {"user_token": user_tok, "pages": pages}
    except Exception:
        pass
    tok = (os.getenv("FB_PAGE_TOKEN") or "").strip()
    pid = (os.getenv("FB_PAGE_ID") or "").strip()
    if tok and pid:
        return {"user_token": None,
                "pages": [{"id": pid,
                           "name": (os.getenv("FB_PAGE_NAME") or "default"),
                           "token": tok}]}
    return {"user_token": None, "pages": []}


def creds_available():
    return bool(fb_registry()["pages"])


def _creds(page_sel=None):
    """(page_token, page_id) — page_sel = page id or page name (case-insensitive)."""
    pages = fb_registry()["pages"]
    if not pages:
        raise RuntimeError("no Facebook API creds — set FB_PAGES_JSON "
                           "(or FB_PAGE_TOKEN/FB_PAGE_ID)")
    if page_sel is None or not str(page_sel).strip():
        want = (os.getenv("FB_PAGE_ID") or "").strip()
        if want:
            for p in pages:
                if p["id"] == want:
                    return p["token"], p["id"]
        return pages[0]["token"], pages[0]["id"]
    sel = str(page_sel).strip()
    sel_l = sel.casefold()
    for p in pages:
        if p["id"] == sel or (p.get("name") or "").casefold() == sel_l:
            return p["token"], p["id"]
    names = ", ".join((p.get("name") or p["id"]) for p in pages)
    raise RuntimeError(f"unknown Facebook page {sel!r} — available: {names}")


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


def upload(video_path, description="", published=True, is_reel=None,
           page=None):
    """Upload to a registry Page (page = id/name, None = default).
    Returns {"success", "id", "url", "kind", "duration"} — never echoes tokens."""
    tok, pid = _creds(page)
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
    print(f"[fb] uploaded page={pid} ({kind}, published={published}): {url}")
    return {"success": True, "id": vid, "url": url, "kind": kind,
            "duration": dur, "page_id": pid}


def delete(video_id, page=None):
    """Remove a video from the Page (e2e cleanup)."""
    tok, _pid = _creds(page)
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
    page = None
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
        elif a == "--page":
            i += 1
            page = args[i] if i < len(args) else None
        else:
            rest.append(a)
        i += 1
    if do_delete:
        sys.exit(0 if delete(do_delete, page=page) else 1)
    if not rest:
        print("Usage: python pipeline/upload_fb.py <video> "
              "[--published false] [--page ID_OR_NAME] [--delete ID]")
        sys.exit(2)
    out = upload(rest[0],
                 description="NOVA e2e test" if not published else "",
                 published=published, page=page)
    print(json.dumps(out))
