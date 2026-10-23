"""Fast poller — yt-dlp checks every 8-12 seconds (randomized).

Runs as a Render web service. A background thread loops yt-dlp checks
against active tasks and registers new videos with the worker.

ROLE: detection only. The worker triggers GitHub Actions (process.yml)
on new videos/manual jobs — this service never calls GitHub for videos.

Also runs a stuck-video reaper: if a process run dies/cancels mid-pipeline
the video stays in a live status forever (worker auto-retry only sweeps
status='failed'). When NO process run is active, any watched video still
in downloading/editing/metadata/uploading is reset to 'detected' and
re-dispatched immediately.

Env vars:
  WORKER_URL, INTERNAL_TOKEN   — worker API access
  GH_TOKEN, GH_REPO            — manual-job backup dispatch + reaper
  POLL_MIN, POLL_MAX           — seconds between polls (default 8 / 12)
"""
import os
import base64
import sys
import re
import json
import time
import random
import tempfile
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

WORKER = os.getenv("WORKER_URL", "").rstrip("/")
TOKEN = os.getenv("INTERNAL_TOKEN", "")
GH_TOKEN = os.getenv("GH_TOKEN", "")
GH_REPO = os.getenv("GH_REPO", "")  # e.g. "asifali-git/novaautomation"
POLL_MIN = int(os.getenv("POLL_MIN", "8"))
POLL_MAX = int(os.getenv("POLL_MAX", "12"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("fastpoll")


def _h():
    return {"x-token": TOKEN, "content-type": "application/json"}


# Edit-plan JSONs posted by the pipeline (dashboard "Download JSON" button).
# Key: "v<video_id>" / "j<manual_job_id>". Mirrored into the repo at
# plans/<key>.json via the GitHub API so downloads survive Render restarts.
_PLAN_STORE = {}


def get_tasks():
    r = requests.get(f"{WORKER}/api/internal/tasks", headers=_h(), timeout=30)
    r.raise_for_status()
    return r.json().get("tasks", [])


def add_video(task_id, video_key, url, title):
    r = requests.post(
        f"{WORKER}/api/internal/videos",
        headers=_h(),
        json={"task_id": task_id, "video_key": video_key, "url": url, "title": title},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def patch_task(task_id, **fields):
    # Internal route (x-token) — public /api/tasks now requires a user session.
    try:
        requests.patch(
            f"{WORKER}/api/internal/tasks/{task_id}",
            headers=_h(),
            json=fields,
            timeout=30,
        ).raise_for_status()
    except Exception:
        pass


def flat_recent(url, cookiefile=None, proxy=None, n=5):
    """Up to n newest entries of a channel/playlist — flat, no download.

    entries[0] is USUALLY newest, but a pinned/featured video can sit on
    top on some platforms. When every id is numeric (Facebook/TikTok/
    Instagram) the largest id is the newest — sort by that. YouTube ids
    are non-numeric strings; the /videos tab is already chronological.
    """
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": n,
        "skip_download": True,
        "socket_timeout": 10,
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if proxy:
        opts["proxy"] = proxy
    if "youtube.com" in (url or "") or "youtu.be" in (url or ""):
        opts["extractor_args"] = {"youtube": {"player_client": ["web"]}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get("entries") or []
    if not entries:
        if info.get("id"):
            return [{
                "id": info.get("id"),
                "title": info.get("title"),
                "url": info.get("webpage_url") or url,
            }]
        return []

    items = [{
        "id": e.get("id"),
        "title": e.get("title"),
        "url": e.get("url") or e.get("webpage_url") or url,
    } for e in entries[:n]]
    ids = [x.get("id") for x in items if x.get("id") is not None]
    if ids and all(str(i).isdigit() for i in ids):
        items.sort(key=lambda x: int(x.get("id") or 0), reverse=True)
    return items


def flat_latest(url, cookiefile=None, proxy=None):
    """Newest single entry (flat_latest = first of flat_recent)."""
    items = flat_recent(url, cookiefile=cookiefile, proxy=proxy, n=5)
    return items[0] if items else None


# --- Platform-aware detection --------------------------------------------------
# Facebook/TikTok need cookies (from Worker D1 accounts); Facebook pages also
# need a scrape (yt-dlp has no FB page-listing extractor anymore).

_COOKIE_TTL = 600
_ck_cache = {}     # (platform, uid) -> {"exp", "items", "file"}
_fb_sess_cache = {}  # uid -> (session, exp)

# Live fetch diagnostics, exposed via /status (Render-side truth).
_FB_DBG = {"attempts": [], "latest": None, "err": None, "ts": 0}

_FB_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Dest": "document",
    "Upgrade-Insecure-Requests": "1",
}


def _fetch_account_cookies(platform, uid):
    """Cookies for a platform from the Worker (task owner scoped)."""
    key = (platform, int(uid or 0))
    now = time.time()
    ent = _ck_cache.get(key)
    if ent and ent["exp"] > now:
        return ent["items"]
    params = f"?platform={platform}" + (f"&user_id={int(uid)}" if uid else "")
    items = None
    try:
        r = requests.get(f"{WORKER}/api/internal/accounts/cookies{params}",
                         headers=_h(), timeout=30)
        if r.status_code == 200:
            data = r.json().get("cookies")
            if isinstance(data, list):
                items = data
            elif isinstance(data, str) and data.strip():
                s = data.lstrip()
                if s.startswith("[") or s.startswith("{"):
                    try:
                        items = json.loads(s)
                    except Exception:
                        items = data
                else:
                    items = data  # Netscape cookie text
    except Exception as e:
        log.warning("cookie fetch %s failed: %s", platform, e)
    _ck_cache[key] = {"exp": now + _COOKIE_TTL, "items": items}
    return items


def _cookie_file(platform, uid):
    """Temp Netscape cookie file for yt-dlp (cached)."""
    items = _fetch_account_cookies(platform, uid)
    key = (platform, int(uid or 0))
    ent = _ck_cache.get(key, {})
    if ent.get("file") and ent.get("exp", 0) > time.time():
        return ent["file"]
    if not items:
        return None
    if isinstance(items, str):
        text = items
    else:
        lines = ["# Netscape HTTP Cookie File"]
        for c in items:
            if not isinstance(c, dict):
                continue
            domain = c.get("domain") or ""
            inc = "TRUE" if domain.startswith(".") else "FALSE"
            exp = int(c.get("expirationDate") or c.get("expiryDate") or 0)
            lines.append("\t".join([
                domain, inc, c.get("path") or "/",
                "TRUE" if c.get("secure") else "FALSE",
                str(exp), str(c.get("name") or ""), str(c.get("value") or ""),
            ]))
        text = "\n".join(lines) + "\n"
    fd, path = tempfile.mkstemp(prefix=f"{platform}_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if key in _ck_cache:
        _ck_cache[key]["file"] = path
    return path


def _fb_env_cookies():
    """Source-scrape cookies from env FB_SOURCE_COOKIES_B64 (base64 of the
    JSON cookie list or Netscape text). Moved OUT of the worker accounts
    store — Connected Accounts now holds only API-backed pages; this secret
    exists only so external FB sources stay scrapeable (Graph can't read
    foreign profiles)."""
    b64 = (os.getenv("FB_SOURCE_COOKIES_B64") or "").strip()
    if not b64:
        return None
    raw = b64
    try:
        raw = base64.b64decode(b64).decode("utf-8", "replace")
    except Exception:
        pass
    s = raw.lstrip()
    if s.startswith("[") or s.startswith("{"):
        try:
            return json.loads(s)
        except Exception:
            return None
    return raw  # Netscape cookie text


def _fb_session(uid=None):
    """requests.Session built from the source-scrape cookies (env)."""
    items = _fb_env_cookies()
    if not items:
        return None
    if isinstance(items, str):
        jar = _cookie_jar(items)
        items = [{"name": k, "value": v, "domain": ".facebook.com"}
                 for k, v in jar.items()]
    key = "env"
    now = time.time()
    ent = _fb_sess_cache.get(key)
    if ent and ent[1] > now:
        return ent[0]
    s = requests.Session()
    s.headers.update(_FB_HEADERS)
    for c in items:
        if not isinstance(c, dict):
            continue
        try:
            s.cookies.set(c.get("name"), c.get("value", ""),
                          domain=c.get("domain") or ".facebook.com",
                          path=c.get("path") or "/")
        except Exception:
            pass
    _fb_sess_cache[key] = (s, now + _COOKIE_TTL)
    return s


def _fb_parse_feed(h):
    """(creation_time, video_id) pairs from the reels feed HTML.

    Each feed card carries creation_time + attachments.media.id (the video).
    Cards are ~40KB; anything much bigger is the playback blob (skipped).
    Order-independent — pick newest by timestamp.
    """
    times = [(m.start(), int(m.group(1))) for m in
             re.finditer(r'"creation_time"\s*:\s*(\d{10})', h)]
    nodes = []
    for i, (pos, t) in enumerate(times):
        end = times[i + 1][0] if i + 1 < len(times) else min(len(h), pos + 200000)
        if end - pos > 200000:
            continue
        seg = h[pos:end]
        vids = []
        for mm in re.finditer(r'"media"\s*:\s*\{[^{}]{0,300}?"id"\s*:\s*"(\d{10,})"', seg):
            if mm.group(1) not in vids:
                vids.append(mm.group(1))
        if len(vids) == 1:
            nodes.append((t, vids[0]))
    return nodes


def _split_seen(seen):
    """Task last_seen_key -> (video_key, created_ts).

    New format: "<video_key>:<created_ts>" (FB creation_time). Legacy
    format was the bare video_key — ts stays None for those.
    """
    if not seen:
        return None, None
    s = str(seen)
    key, sep, ts = s.rpartition(":")
    if sep and ts.isdigit():
        return (key or None), int(ts)
    return s, None


def _fb_new_nodes(nodes, seen_key, seen_ts, cap=5):
    """Feed nodes (created_ts, video_id) that are strictly NEWER than the
    last-seen reel. Returns (new_nodes_oldest_first, newest_of_them).

    Timestamp guard: a partial feed parse (login-wall flap, slow load) can
    briefly hide the true newest card and make an older card LOOK new —
    comparing creation_time instead of identity stops old reels from ever
    being enqueued again. First contact = newest card only (back-catalog
    stays untouched).
    """
    if not nodes:
        return [], None
    if seen_key is None and seen_ts is None:
        newest = max(nodes, key=lambda x: x[0])
        return [newest], newest
    if seen_ts is not None:
        newer = [n for n in nodes if n[0] > seen_ts]
    else:
        # Legacy seen (bare id, no ts): only accept a DIFFERENT newest card.
        newest = max(nodes, key=lambda x: x[0])
        newer = [newest] if str(newest[1]) != str(seen_key) else []
    newer.sort(key=lambda x: x[0])
    newer = newer[-cap:]
    return newer, (newer[-1] if newer else None)


def _cookie_jar(items):
    """Name->value dict from a JSON cookie list or Netscape cookie text."""
    jar = {}
    if isinstance(items, str):
        s = items.lstrip()
        if s.startswith("[") or s.startswith("{"):
            try:
                items = json.loads(s)
            except Exception:
                items = None
        else:
            for line in items.splitlines():
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    jar[parts[5]] = parts[6]
            return jar
    if isinstance(items, list):
        for c in items:
            if isinstance(c, dict) and c.get("name"):
                jar[c["name"]] = c.get("value", "")
    return jar


def _fb_fetch_feed(url, uid):
    """Page name + feed cards from FB.

    Pass 1: curl_cffi with Chrome TLS impersonation — DC IPs get an instant
    login wall from python-requests' fingerprint. Pass 2: plain requests.
    Account proxy (if set in NOVA UI) is used for both passes.
    """
    m = re.search(r"facebook\.com/([^/?#]+)", url)
    if not m:
        raise ValueError(f"not a facebook page url: {url}")
    page = m.group(1)
    if page in ("watch", "reel", "watchparty", "videos", "profile.php",
                "permalink.php", "story.php", "photo.php"):
        raise ValueError("use a page url like facebook.com/<PageName> "
                         "(single-video/profile.php urls unsupported)")
    items = _fb_env_cookies()
    jar = _cookie_jar(items)
    if not jar:
        raise ValueError(
            "FB_SOURCE_COOKIES_B64 missing/empty — FB source-scrape cookies "
            "live in the repo secret now (not in Connected Accounts)")
    proxy = (os.getenv("FB_SOURCE_PROXY") or "").strip() or None
    prox = {"http": proxy, "https": proxy} if proxy else None
    variants = [f"https://{h}/{page}/reels{q}"
                for h in ("www.facebook.com", "m.facebook.com")
                for q in ("", "?_fb_noscript=1")]
    attempts = []

    try:
        from curl_cffi import requests as crequests
        have_cffi = True
    except Exception:
        have_cffi = False

    if have_cffi:
        hdrs = {k: v for k, v in _FB_HEADERS.items()
                if k.lower() != "user-agent"}
        for full in variants:
            t0 = time.time()
            try:
                r = crequests.get(full, headers=hdrs, cookies=jar,
                                  impersonate="chrome131", timeout=60,
                                  allow_redirects=True, proxies=prox)
            except Exception as e:
                attempts.append({"url": full, "fetcher": "cffi",
                                 "err": str(e)[:120],
                                 "sec": round(time.time() - t0, 1)})
                log.warning("fb cffi %s failed: %s", full, e)
                continue
            nodes = _fb_parse_feed(r.text)
            attempts.append({"url": full, "fetcher": "cffi",
                             "status": r.status_code, "len": len(r.text),
                             "final": r.url[:140], "cards": len(nodes),
                             "sec": round(time.time() - t0, 1)})
            log.info("fb cffi %s: %s len=%d cards=%d", full, r.status_code,
                     len(r.text), len(nodes))
            if nodes:
                _FB_DBG.update(attempts=attempts, ts=time.time(), err=None)
                return page, nodes

    s = _fb_session(uid)
    if s is None:
        raise ValueError(
            "FB_SOURCE_COOKIES_B64 missing/empty — FB source-scrape cookies "
            "live in the repo secret now (not in Connected Accounts)")
    if proxy:
        s.proxies = dict(prox)
    for full in variants:
        t0 = time.time()
        try:
            r = s.get(full, timeout=60, allow_redirects=True)
        except Exception as e:
            attempts.append({"url": full, "fetcher": "req",
                             "err": str(e)[:120],
                             "sec": round(time.time() - t0, 1)})
            log.warning("fb req %s failed: %s", full, e)
            continue
        nodes = _fb_parse_feed(r.text)
        attempts.append({"url": full, "fetcher": "req",
                         "status": r.status_code, "len": len(r.text),
                         "final": r.url[:140], "cards": len(nodes),
                         "sec": round(time.time() - t0, 1)})
        log.info("fb req %s: %s len=%d cards=%d", full, r.status_code,
                 len(r.text), len(nodes))
        if nodes:
            _FB_DBG.update(attempts=attempts, ts=time.time(), err=None)
            return page, nodes

    _FB_DBG.update(attempts=attempts, ts=time.time(),
                   err=f"page '{page}': no feed cards ({len(attempts)} attempts)")
    raise ValueError(
        f"facebook page '{page}': reels feed empty after {len(attempts)} "
        f"attempts (login wall / no reels / cookies dead) — see /status")


def fb_latest(url, uid):
    """Newest reel of a FB page — one fetch, creation_time decides latest."""
    page, nodes = _fb_fetch_feed(url, uid)
    nodes.sort(key=lambda x: x[0], reverse=True)
    t, vid = nodes[0]
    _FB_DBG["latest"] = {"id": vid, "created": t}
    log.info("fb page %s: %d feed videos, latest=%s @%d", page, len(nodes),
             vid, t)
    return {"id": vid, "title": None,
            "url": f"https://www.facebook.com/reel/{vid}"}


def trigger_process(video_ids):
    """Fire GitHub Actions process.yml via workflow_dispatch."""
    if not GH_TOKEN or not GH_REPO:
        return
    url = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/process.yml/dispatches"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    payload = {
        "ref": "master",
        "inputs": {"video_ids": ",".join(map(str, video_ids))},
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        log.info("triggered process for videos: %s", video_ids)
    except Exception as e:
        log.warning("failed to trigger process: %s", e)


def trigger_check():
    """Ask a GitHub runner to run check.yml (all-platform check; the FB
    feed is login-walled from Render's IP, the runner's IP fetches it)."""
    if not GH_TOKEN or not GH_REPO:
        log.warning("check skipped: GH_TOKEN/GH_REPO not set")
        return False
    url = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/check.yml/dispatches"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    try:
        r = requests.post(url, headers=headers, json={"ref": "master"},
                          timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("check dispatch failed: %s", e)
        return False


trigger_fbcheck = trigger_check  # old name, kept as alias


def gh_save_plan(key, plan):
    """Persist plan JSON into the repo (plans/<key>.json) for the download button."""
    if not GH_TOKEN or not GH_REPO:
        return False
    import base64
    api = f"https://api.github.com/repos/{GH_REPO}/contents/plans/{key}.json"
    h = {"Authorization": f"token {GH_TOKEN}",
         "Accept": "application/vnd.github+json"}
    try:
        sha = None
        r = requests.get(api, headers=h, timeout=15)
        if r.status_code == 200:
            sha = (r.json() or {}).get("sha")
        body = {
            "message": f"plan {key}",
            "content": base64.b64encode(
                json.dumps(plan, ensure_ascii=False, indent=1).encode("utf-8")
            ).decode("ascii"),
            "branch": "master",
        }
        if sha:
            body["sha"] = sha
        r2 = requests.put(api, headers=h, json=body, timeout=25)
        ok = r2.status_code in (200, 201)
        if not ok:
            log.warning("gh_save_plan %s -> %s %s", key, r2.status_code, r2.text[:200])
        return ok
    except Exception as e:
        log.warning("gh_save_plan %s: %s", key, e)
        return False


_recent_triggers = {}  # key -> monotonic ts (in-process debounce)
_state = {"last_poll": 0, "last_manual_check": 0, "last_manual_found": None,
          "last_reap": 0, "reaped": 0, "watch_count": 0}


# --- Stuck-video reaper -------------------------------------------------------
# If a GH process run dies/cancels mid-pipeline, the video stays in a live
# status forever (worker auto-retry only sweeps status='failed'). Reaper rule:
# NO active process run + video still mid-pipeline => it's stuck => reset.

_WATCH_FILE = os.path.join(tempfile.gettempdir(), "nova_watch_ids.json")
_watch_ids = set()
_last_reap = [0.0]
REAP_INTERVAL = 15  # seconds between reaper passes
REAP_MAX_RETRY = 5


def _load_watch():
    try:
        with open(_WATCH_FILE) as f:
            _watch_ids.update(int(x) for x in json.load(f))
    except Exception:
        pass


def _save_watch():
    try:
        with open(_WATCH_FILE, "w") as f:
            json.dump(sorted(_watch_ids), f)
    except Exception:
        pass


def _bootstrap_watch_from_gh():
    """Cold start: recover recently-processed video ids from process run logs."""
    if not GH_TOKEN or not GH_REPO:
        return
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/actions/workflows/process.yml/runs?per_page=3",
            headers={"Authorization": f"token {GH_TOKEN}",
                     "Accept": "application/vnd.github+json"},
            timeout=20,
        )
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])
    except Exception as e:
        log.warning("watch bootstrap: gh runs failed: %s", e)
        return
    import io as _io
    import zipfile as _zip
    for run in runs:
        try:
            r2 = requests.get(
                f"https://api.github.com/repos/{GH_REPO}/actions/runs/{run['id']}/logs",
                headers={"Authorization": f"token {GH_TOKEN}",
                         "Accept": "application/vnd.github+json"},
                timeout=60,
            )
            if r2.status_code != 200:
                continue
            zf = _zip.ZipFile(_io.BytesIO(r2.content))
            for name in zf.namelist():
                txt = zf.read(name).decode("utf-8", "replace")
                for m in re.finditer(r"Processing video (\d+)", txt):
                    _watch_ids.add(int(m.group(1)))
                for m in re.finditer(r"video (\d+) -> id (\d+)", txt):
                    _watch_ids.add(int(m.group(2)))
        except Exception as e:
            log.warning("watch bootstrap: run %s logs failed: %s", run.get("id"), e)
    if _watch_ids:
        keep = sorted(_watch_ids)[-80:]
        _watch_ids.clear()
        _watch_ids.update(keep)
        _save_watch()
        log.info("watch bootstrap: %d id(s), max=%s", len(_watch_ids), max(_watch_ids))


MID_PIPELINE = ("downloading", "analyzing", "voiceover", "editing",
                "metadata", "uploading")


def _bootstrap_watch_probe():
    """Cold start: probe recent video ids directly against the worker so
    videos orphaned by a cancelled run (missing from logs) are still found."""
    if not WORKER or not TOKEN:
        return
    known = max(_watch_ids) if _watch_ids else 0
    top = 0
    misses = 0
    checked = 0
    for vid in range(1, max(known + 20, 120)):
        if checked >= 300:
            break
        checked += 1
        try:
            r = requests.get(f"{WORKER}/api/internal/videos/{vid}",
                             headers=_h(), timeout=8)
        except Exception:
            continue
        if r.status_code != 200:
            misses += 1
            if vid > known and misses >= 15:
                break
            continue
        misses = 0
        top = vid
        v = r.json().get("video") or {}
        st = v.get("status")
        if st in MID_PIPELINE:
            _watch_ids.add(vid)
            log.warning("watch probe: video %s stuck at %s (retry %s)",
                        vid, st, v.get("retry_count"))
    _save_watch()
    _state["watch_count"] = len(_watch_ids)
    log.info("watch probe: checked %d id(s), top=%d, watching=%d",
             checked, top, len(_watch_ids))


def _process_run_active():
    """True if any process.yml run is queued or in_progress."""
    if not GH_TOKEN or not GH_REPO:
        return False
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/actions/workflows/process.yml/runs?per_page=5",
            headers={"Authorization": f"token {GH_TOKEN}",
                     "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        r.raise_for_status()
        for run in r.json().get("workflow_runs", []):
            if run.get("status") in ("queued", "in_progress"):
                return True
        return False
    except Exception as e:
        log.warning("reaper: active-run check failed: %s", e)
        return True  # be safe — don't reap when unsure


def reap_stuck_videos():
    """Reset videos stuck in a mid-pipeline live status back to 'detected'.

    Only fires when NO process run is active, so it never races a live runner.
    """
    if not WORKER or not TOKEN or not _watch_ids:
        return
    candidates = []
    for vid in list(_watch_ids):
        try:
            r = requests.get(f"{WORKER}/api/internal/videos/{vid}",
                             headers=_h(), timeout=10)
            if r.status_code != 200:
                continue
            v = r.json().get("video") or {}
        except Exception:
            continue
        st = v.get("status")
        if st in ("downloading", "analyzing", "voiceover", "editing",
                  "metadata", "uploading"):
            candidates.append((vid, st, v.get("retry_count") or 0))
        elif st in ("uploaded", "failed", "cancelled", "detected"):
            _watch_ids.discard(vid)  # terminal / already queued — stop watching
    _state["watch_count"] = len(_watch_ids)
    if not candidates:
        _save_watch()
        return
    if _process_run_active():
        log.info("reaper: %d mid-pipeline video(s) but a run is active — waiting",
                 len(candidates))
        return
    for vid, st, rc in candidates:
        if rc >= REAP_MAX_RETRY:
            log.warning("reaper: video %s stuck at %s retry=%d — marking failed",
                        vid, st, rc)
            try:
                requests.patch(f"{WORKER}/api/internal/videos/{vid}",
                               headers=_h(),
                               json={"status": "failed",
                                     "error": f"stuck at {st} (reaper, retries exhausted)"},
                               timeout=15)
            except Exception:
                pass
            _watch_ids.discard(vid)
            continue
        log.warning("reaper: video %s stuck at %s (no active run) -> detected (retry %d)",
                    vid, st, rc + 1)
        try:
            requests.patch(
                f"{WORKER}/api/internal/videos/{vid}",
                headers=_h(),
                json={"status": "detected", "error": "",
                      "dispatched_at": 0, "retry_count": rc + 1},
                timeout=15,
            ).raise_for_status()
        except Exception as e:
            log.warning("reaper: patch video %s failed: %s", vid, e)
            continue
        _state["reaped"] = _state.get("reaped", 0) + 1
        if _should_trigger(f"reap:{vid}", 120):
            trigger_process([vid])
    _save_watch()


def _should_trigger(key, cooldown_s=900):
    """Debounce duplicate GH dispatches: runner claim lags several loops,
    so the same queued job/video would otherwise re-trigger every pass."""
    now = time.monotonic()
    for k in [k for k, ts in _recent_triggers.items() if now - ts > 3600]:
        _recent_triggers.pop(k, None)
    if now - _recent_triggers.get(key, -1e9) < cooldown_s:
        return False
    _recent_triggers[key] = now
    return True


def check_manual_jobs():
    """Check for queued manual upload jobs and trigger processing."""
    if not WORKER or not TOKEN:
        return False
    try:
        r = requests.get(f"{WORKER}/api/internal/manual/next", headers=_h(), timeout=15)
        r.raise_for_status()
        job = r.json().get("job")
        if not job:
            return False

        # Worker dispatches instantly on job create; skip if it already did
        # (dispatched_at recent) — but still fire as backup if it didn't.
        disp = job.get("dispatched_at") or 0
        if disp and (time.time() * 1000 - disp) < 900_000:
            log.info("manual job %s already dispatched by worker; skipping", job["id"])
            return False

        if not _should_trigger(f"manual:{job['id']}", 900):
            log.info("manual job %s triggered recently; waiting for runner claim", job["id"])
            return False

        log.info("MANUAL job %s: %s -> %s", job["id"], job["url"], job.get("dest_platforms"))

        # Trigger GitHub Actions manual process (runner claims the job atomically).
        if not GH_TOKEN or not GH_REPO:
            log.error("GH_TOKEN/GH_REPO not set — cannot dispatch manual job %s (stays queued)", job["id"])
            return False
        url = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/process.yml/dispatches"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        payload = {
            "ref": "master",
            "inputs": {"manual_job_id": str(job["id"])},
        }
        try:
            r2 = requests.post(url, headers=headers, json=payload, timeout=15)
            r2.raise_for_status()
            log.info("triggered manual process for job %s", job["id"])
        except Exception as e:
            log.warning("failed to trigger manual process for job %s: %s (stays queued, will retry)", job["id"], e)
            return False
        return True
    except Exception as e:
        log.warning("manual job check failed: %s", e)
        return False


def _enqueue(tid, key, url, title, new_ids, undispached):
    """add_video + tracking. Returns (status, video_id):
    ("created", id) | ("known", None) | ("failed", None)."""
    try:
        res = add_video(tid, key, url, title)
    except Exception as e:
        log.warning("task %s add_video %s failed: %s", tid, key, e)
        return "failed", None
    if not res.get("created"):
        log.info("task %s video %s already known", tid, key)
        return "known", None
    vid = res["video"]["id"]
    new_ids.append(vid)
    _watch_ids.add(vid)
    _save_watch()
    log.info("task %s video %s -> id %s (worker dispatched=%s)",
             tid, key, vid, res.get("dispatched"))
    if not res.get("dispatched"):
        if undispached is not None:
            undispached.append(vid)
        elif _should_trigger(f"vids:{vid}", 1800):
            trigger_process([vid])
    return "created", vid


_YT_TAB_RE = re.compile(r"/(videos|shorts|streams|playlists|featured)/?(?:[?#]|$)", re.I)


def _yt_tab_url(url):
    """Bare YouTube channel root -> /videos tab (chronological, no featured/pins).

    Playlists, /watch links, youtu.be and already-tabbed URLs pass through.
    """
    u = (url or "").strip()
    low = u.lower()
    if not u or ("youtube.com" not in low and "youtu.be" not in low):
        return u
    if "youtu.be" in low or "/watch" in low or "list=" in low or "/playlist" in low:
        return u
    if _YT_TAB_RE.search(low):
        return u
    return u.split("?")[0].split("#")[0].rstrip("/") + "/videos"


def _yt_cookiefile():
    """Optional YouTube cookies exported by the workflow (COOKIES_FILE)."""
    path = os.getenv("COOKIES_FILE")
    return path if path and os.path.exists(path) else None


_hub_cfg_ts = [0.0]


def _apply_hub_config(force=False):
    """Pull panel settings (YT cookies, poll bounds) from the hub."""
    global POLL_MIN, POLL_MAX
    if not WORKER or not TOKEN:
        return
    if not force and time.time() - _hub_cfg_ts[0] < 300:
        return
    _hub_cfg_ts[0] = time.time()
    try:
        r = requests.get(f"{WORKER}/api/internal/config", headers=_h(), timeout=15)
        if not r.ok:
            return
        cfg = r.json() or {}
    except Exception as e:
        log.warning("hub config fetch failed: %s", e)
        return

    cookies = (cfg.get("yt_cookies") or "").strip()
    if cookies:
        path = os.path.join(tempfile.gettempdir(), "nova_yt_cookies.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(cookies if cookies.endswith("\n") else cookies + "\n")
            os.environ["COOKIES_FILE"] = path
        except Exception as e:
            log.warning("cannot write yt cookies: %s", e)

    for key, lo, hi in (("poll_min", 5, 300), ("poll_max", 5, 600)):
        try:
            n = int(cfg.get(key))
        except (TypeError, ValueError):
            continue
        if lo <= n <= hi:
            if key == "poll_min":
                POLL_MIN = n
            else:
                POLL_MAX = n
    if POLL_MIN > POLL_MAX:
        POLL_MIN, POLL_MAX = POLL_MAX, POLL_MIN


def _check_task(t, new_ids, fetch_fb=True, undispached=None):
    """One task's detection pass (all platforms).

    fetch_fb=True fetches the FB feed here (check.yml on a GH runner).
    False → FB tasks just dispatch check.yml (Render's IP is login-walled).
    undispached: optional list — created-but-not-worker-dispatched video
    ids, batch-fired as ONE process.yml run at the end of check_all_tasks.
    Returns True if any new video was enqueued.
    """
    tid = t["id"]
    url = t["source_url"]
    platform = (t.get("platform") or "").lower()
    uid = t.get("user_id") or 0
    is_fb = platform == "facebook" or "facebook.com" in (url or "")

    if is_fb and not fetch_fb:
        if _should_trigger("check", 60):
            ok = trigger_check()
            log.info("task %s: check dispatch -> %s", tid, ok)
        return False

    seen_key, seen_ts = _split_seen(t.get("last_seen_key"))
    baseline = t.get("baseline_key")
    first = baseline is None and seen_key is None

    nodes = None      # FB: [(created_ts, video_id), ...]
    items = None      # others: [{id, title, url}, ...] newest-first
    try:
        if is_fb:
            page, nodes = _fb_fetch_feed(url, uid)
            if not nodes:
                raise ValueError(f"page '{page}': no feed cards")
        elif platform in ("tiktok", "instagram"):
            items = flat_recent(url, cookiefile=_cookie_file(platform, uid))
        elif platform == "youtube" or "youtube.com" in (url or "") or "youtu.be" in (url or ""):
            items = flat_recent(_yt_tab_url(url), cookiefile=_yt_cookiefile())
        else:
            items = flat_recent(url)
        if t.get("last_error"):
            patch_task(tid, last_error="")
    except Exception as e:
        log.warning("task %s fetch failed: %s", tid, e)
        _state["task_err"] = f"task {tid}: {str(e)[:180]}"
        _state["task_err_ts"] = time.time()
        try:
            patch_task(tid, last_error=str(e)[:200])
        except Exception:
            pass
        return False
    _state["task_err"] = None

    if is_fb:
        picked, newest = _fb_new_nodes(nodes, seen_key, seen_ts)
        if not picked:
            return False
        found = False
        ok_advance = True  # last_seen may advance unless ALL enqueues failed
        for cts, vid in picked:
            st, _v = _enqueue(tid, vid, f"https://www.facebook.com/reel/{vid}",
                              None, new_ids, undispached)
            if st == "created":
                found = True
            elif st == "failed":
                ok_advance = False
        if newest and ok_advance:
            packed = f"{newest[1]}:{newest[0]}"
            if first:
                patch_task(tid, baseline_key=newest[1], last_seen_key=packed)
                log.info("task %s baseline set %s (ts %s)", tid, newest[1], newest[0])
            else:
                patch_task(tid, last_seen_key=packed)
        return found

    if not items or not items[0].get("id"):
        msg = f"no entries returned for {url}"[:200]
        log.warning("task %s: %s", tid, msg)
        _state["task_err"] = f"task {tid}: {msg}"
        _state["task_err_ts"] = time.time()
        try:
            patch_task(tid, last_error=msg)
        except Exception:
            pass
        return False

    if first:
        # First contact: enqueue THE newest video only (back-catalog stays).
        # On failure leave baseline unset → retried on the next pass.
        top = items[0]
        st, _v = _enqueue(tid, str(top["id"]), top.get("url") or url,
                          top.get("title"), new_ids, undispached)
        if st != "failed":
            patch_task(tid, baseline_key=str(top["id"]),
                       last_seen_key=str(top["id"]))
            log.info("task %s baseline set to %s", tid, top["id"])
        return st == "created"

    ids = [str(x.get("id") or "") for x in items]
    if (seen_key and str(seen_key).isdigit()
            and all(i.isdigit() for i in ids if i)):
        # Numeric platform (TikTok/IG): enqueue EVERY id newer than seen.
        newer = [x for x in items
                 if str(x.get("id") or "").isdigit()
                 and int(x["id"]) > int(seen_key)]
        newer.sort(key=lambda x: int(x["id"]))
        newer = newer[-5:]
    else:
        # Non-numeric ids (YouTube): every entry above the last-seen one,
        # because a pinned/featured entry can occupy position 0.
        want = str(seen_key or "")
        if want and want in ids:
            newer = items[:ids.index(want)]
        elif want:
            newer = [items[0]] if ids[0] != want else []
        else:
            newer = [items[0]]
        newer = newer[:5]
    found = False
    ok_advance = True
    for x in newer:
        st, _v = _enqueue(tid, str(x["id"]), x.get("url") or url,
                          x.get("title"), new_ids, undispached)
        if st == "created":
            found = True
        elif st == "failed":
            ok_advance = False
    if newer and ok_advance:
        patch_task(tid, last_seen_key=str(items[0]["id"]))
    return found


def poll_once():
    """Single poll pass across all active tasks. Returns new video IDs.

    Render-side: FB tasks just dispatch check.yml (login-walled IP);
    everything else is fetched here with yt-dlp.
    """
    if not WORKER or not TOKEN:
        log.error("WORKER_URL / INTERNAL_TOKEN not set")
        return []
    try:
        tasks = get_tasks()
    except Exception as e:
        log.error("failed to fetch tasks: %s", e)
        return []
    new_ids = []
    for t in tasks:
        try:
            _check_task(t, new_ids, fetch_fb=False)
        except Exception as e:
            log.error("task %s check crashed: %s", t.get("id"), e)
    return new_ids


def check_all_tasks():
    """Full multi-platform check in ONE pass — run by check.yml on a GitHub
    runner. Covers every connected platform/account/task (new tasks are
    picked up automatically via get_tasks). First add = newest only; after
    that only strictly-new videos are enqueued. All worker-dispatch failures
    are batch-fired as ONE process.yml run at the end.
    Returns the list of new video ids.
    """
    if not WORKER or not TOKEN:
        log.error("WORKER_URL / INTERNAL_TOKEN not set")
        return []
    try:
        tasks = get_tasks()
    except Exception as e:
        log.error("failed to fetch tasks: %s", e)
        return []
    new_ids = []
    undispached = []
    for t in tasks:
        try:
            _check_task(t, new_ids, fetch_fb=True, undispached=undispached)
        except Exception as e:
            log.error("task %s check crashed: %s", t.get("id"), e)
    if undispached:
        log.info("batch-trigger process for undispached videos: %s", undispached)
        trigger_process(undispached)
    log.info("check done: %d task(s), %d new video(s)", len(tasks), len(new_ids))
    return new_ids


def poll_loop():
    """Background loop: poll every 8-15s (randomized)."""
    log.info("fastpoll started — interval %d-%ds", POLL_MIN, POLL_MAX)
    while True:
        try:
            _apply_hub_config()
            # Check manual jobs first
            _state["last_manual_check"] = time.time()
            found = check_manual_jobs()
            _state["last_manual_found"] = found
            # Then check for new videos from tasks
            new = poll_once()
            _state["last_poll"] = time.time()
            if new:
                log.info("found %d new video(s)", len(new))
            # Stuck-video reaper — every ~15s, reset orphaned mid-pipeline videos.
            if time.time() - _last_reap[0] >= REAP_INTERVAL:
                _last_reap[0] = time.time()
                _state["last_reap"] = _last_reap[0]
                reap_stuck_videos()
        except Exception as e:
            log.error("poll error: %s", e)
        time.sleep(random.randint(POLL_MIN, POLL_MAX))


def keepalive_loop():
    """Ping self via PUBLIC url every 8-10 min so Render free tier doesn't sleep.

    Must go through Render's router (RENDER_EXTERNAL_URL / SELF_URL) — a
    localhost ping never counts as traffic and the service sleeps after 15 min.
    """
    port = os.getenv("PORT", "10000")
    public = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    self_url = os.getenv("SELF_URL") or (
        f"{public}/health" if public else f"http://127.0.0.1:{port}/health"
    )
    log.info("keepalive target: %s", self_url)
    while True:
        interval = random.randint(480, 600)  # 8-10 min (sleep threshold ~15 min)
        time.sleep(interval)
        try:
            requests.get(self_url, timeout=15)
            log.info("keepalive ping sent (slept %ds)", interval)
        except Exception as e:
            log.warning("keepalive ping failed: %s", e)


# Minimal HTTP server so Render keeps the instance alive (health check).
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, extra=None, ctype="application/json"):
        """Send a response with CORS (used by the dashboard on pages.dev)."""
        if isinstance(body, str):
            body = body.encode("utf-8")
        elif not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _serve_plan(self, key):
        plan = _PLAN_STORE.get(key)
        if plan is None and GH_REPO:
            try:
                r = requests.get(
                    f"https://raw.githubusercontent.com/{GH_REPO}/master/plans/{key}.json",
                    timeout=15)
                if r.status_code == 200:
                    self._send(200, r.content, {
                        "Content-Disposition":
                            f'attachment; filename="nova_plan_{key}.json"'})
                    return
            except Exception:
                pass
            self._send(404, {"error": "plan not found"})
            return
        if plan is None:
            self._send(404, {"error": "plan not found"})
            return
        self._send(200, json.dumps(plan, ensure_ascii=False, indent=1), {
            "Content-Disposition":
                f'attachment; filename="nova_plan_{key}.json"'})

    def do_GET(self):
        m = re.match(r"^/api/plan/([A-Za-z0-9_-]{1,40})\.json$", self.path)
        if m:
            self._serve_plan(m.group(1))
            return
        if self.path == "/health":
            body = b'{"ok":true}'
            self.send_response(200)
        elif self.path == "/status":
            import json as _json
            body = _json.dumps({
                "service": "fastpoll",
                "ok": True,
                "last_poll": _state.get("last_poll"),
                "last_manual_check": _state.get("last_manual_check"),
                "last_manual_found": _state.get("last_manual_found"),
                "poll_interval": [POLL_MIN, POLL_MAX],
                "gh_repo": GH_REPO or None,
                "task_err": _state.get("task_err"),
                "task_err_ts": _state.get("task_err_ts"),
                "fb": _FB_DBG,
                "reap": {"last": _state.get("last_reap"),
                         "reaped": _state.get("reaped", 0),
                         "watch": _state.get("watch_count", len(_watch_ids))},
                "plan_keys": list(_PLAN_STORE.keys())[-20:],
            }).encode()
            self.send_response(200)
        elif self.path == "/wake":
            # Manual wake: trigger an immediate manual-job check (bypasses sleep).
            try:
                triggered = check_manual_jobs()
                body = ('{"ok":true,"manual_triggered":%s}' % ("true" if triggered else "false")).encode()
            except Exception as e:
                body = ('{"ok":false,"error":"%s"}' % str(e).replace('"', "'")[:200]).encode()
            self.send_response(200)
        else:
            body = b'{"service":"fastpoll","endpoints":["/health","/status","/wake"]}'
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/api/plan", "/api/cancel"):
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 600000:
            self._send(400, {"error": "bad body size"})
            return
        raw = self.rfile.read(length)

        if self.path == "/api/plan":
            if (self.headers.get("x-token") or "") != TOKEN:
                self._send(401, {"error": "bad token"})
                return
            try:
                data = json.loads(raw)
            except Exception:
                self._send(400, {"error": "invalid json"})
                return
            key = str(data.get("key") or "")
            plan = data.get("plan")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", key) or not isinstance(plan, dict):
                self._send(400, {"error": "bad key/plan"})
                return
            _PLAN_STORE[key] = plan
            while len(_PLAN_STORE) > 50:
                _PLAN_STORE.pop(next(iter(_PLAN_STORE)))
            saved = gh_save_plan(key, plan)
            log.info("plan stored %s (%d bytes, gh=%s)", key, len(raw), saved)
            self._send(200, {"ok": True, "key": key, "gh_saved": bool(saved)})
            return

        # /api/cancel — flips status to cancelled; the pipeline checks it
        # at its stage gates (before edit / before upload) and stops.
        # Browser CSRF guard: block cross-site browser POSTs, allow
        # same-origin + curl (no Origin header).
        origin = (self.headers.get("Origin") or "").rstrip("/")
        allowed_origins = ("https://novaautomation.pages.dev",
                           "https://www.novaautomation.pages.dev")
        if origin and origin not in allowed_origins:
            self._send(403, {"error": "origin not allowed"})
            return
        try:
            data = json.loads(raw)
        except Exception:
            self._send(400, {"error": "invalid json"})
            return
        kind = str(data.get("kind") or "")
        try:
            jid = int(data.get("id") or 0)
        except (TypeError, ValueError):
            jid = 0
        if kind not in ("video", "manual") or jid <= 0:
            self._send(400, {"error": "bad kind/id"})
            return
        path = (f"/api/internal/videos/{jid}" if kind == "video"
                else f"/api/internal/manual/{jid}")
        try:
            r = requests.patch(f"{WORKER}{path}", headers=_h(),
                               json={"status": "cancelled"}, timeout=30)
            ok = r.status_code == 200
            detail = None if ok else r.text[:200]
        except Exception as e:
            ok, detail = False, str(e)[:200]
        log.info("cancel %s %s -> ok=%s %s", kind, jid, ok, detail or "")
        self._send(200 if ok else 502,
                   {"ok": bool(ok), "kind": kind, "id": jid, "error": detail})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type, x-token")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass  # silence default access log


def main():
    port = int(os.getenv("PORT", "10000"))
    _apply_hub_config(force=True)
    _load_watch()
    if not _watch_ids:
        _bootstrap_watch_from_gh()
    _bootstrap_watch_probe()
    _state["watch_count"] = len(_watch_ids)
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    ka = threading.Thread(target=keepalive_loop, daemon=True)
    ka.start()
    log.info("HTTP server on :%d (keepalive via public url)", port)
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    if "--check" in sys.argv:
        # One full check pass — entrypoint of check.yml on a GH runner.
        ids = check_all_tasks()
        print(f"check: {len(ids)} new video(s)")
        sys.exit(0)
    main()
