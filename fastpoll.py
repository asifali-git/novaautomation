"""Fast poller — yt-dlp checks every 8-12 seconds (randomized).

Runs as a Render web service. A background thread loops yt-dlp checks
against active tasks and registers new videos with the worker.

ROLE: detection only. The worker triggers GitHub Actions (process.yml)
on new videos/manual jobs — this service never calls GitHub for videos.

Env vars:
  WORKER_URL, INTERNAL_TOKEN   — worker API access
  GH_TOKEN, GH_REPO            — backup dispatch for manual jobs only
                                 (worker is primary; skipped if dispatched)
  POLL_MIN, POLL_MAX           — seconds between polls (default 8 / 12)
"""
import os
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


def flat_latest(url, cookiefile=None, proxy=None):
    """Lightweight yt-dlp check — flat extract, no download."""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": 5,
        "skip_download": True,
        "socket_timeout": 10,
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if proxy:
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get("entries") or []
    if not entries:
        if info.get("id"):
            return {
                "id": info.get("id"),
                "title": info.get("title"),
                "url": info.get("webpage_url") or url,
            }
        return None

    e = entries[0]
    return {
        "id": e.get("id"),
        "title": e.get("title"),
        "url": e.get("url") or e.get("webpage_url") or url,
    }


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


def _fb_session(uid):
    """requests.Session with the owner's Facebook cookies."""
    items = _fetch_account_cookies("facebook", uid)
    if not items or isinstance(items, str):
        return None
    key = int(uid or 0)
    now = time.time()
    ent = _fb_sess_cache.get(key)
    if ent and ent[1] > now:
        return ent[0]
    s = requests.Session()
    s.headers.update(_FB_HEADERS)
    for c in items:
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


def _fb_fetch_feed(url, uid):
    """Page name + feed cards from FB. Tries www/m × plain/noscript SSR."""
    m = re.search(r"facebook\.com/([^/?#]+)", url)
    if not m:
        raise ValueError(f"not a facebook page url: {url}")
    page = m.group(1)
    if page in ("watch", "reel", "watchparty", "videos", "profile.php",
                "permalink.php", "story.php", "photo.php"):
        raise ValueError("use a page url like facebook.com/<PageName> "
                         "(single-video/profile.php urls unsupported)")
    s = _fb_session(uid)
    if s is None:
        raise ValueError("no facebook cookies connected — add a Facebook "
                         "account first (Connect)")
    attempts = []
    for host in ("www.facebook.com", "m.facebook.com"):
        for suffix in ("", "?_fb_noscript=1"):
            full = f"https://{host}/{page}/reels{suffix}"
            t0 = time.time()
            try:
                r = s.get(full, timeout=60, allow_redirects=True)
                h = r.text
            except Exception as e:
                attempts.append({"url": full, "err": str(e)[:120],
                                 "sec": round(time.time() - t0, 1)})
                log.warning("fb fetch %s failed: %s", full, e)
                continue
            nodes = _fb_parse_feed(h)
            attempts.append({"url": full, "status": r.status_code,
                             "len": len(h), "final": r.url[:140],
                             "cards": len(nodes),
                             "sec": round(time.time() - t0, 1)})
            log.info("fb %s: %s len=%d cards=%d", full, r.status_code,
                     len(h), len(nodes))
            if nodes:
                _FB_DBG.update(attempts=attempts, ts=time.time(), err=None)
                return page, nodes
    _FB_DBG.update(attempts=attempts, ts=time.time(),
                   err=f"page '{page}': no feed cards in reels response")
    raise ValueError(
        f"facebook page '{page}': reels feed empty after {len(attempts)} "
        f"attempts (wall/timeout on this IP, or no reels) — see /status")


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


_recent_triggers = {}  # key -> monotonic ts (in-process debounce)
_state = {"last_poll": 0, "last_manual_check": 0, "last_manual_found": None}


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


def poll_once():
    """Single poll pass across all active tasks. Returns new video IDs."""
    if not WORKER or not TOKEN:
        log.error("WORKER_URL / INTERNAL_TOKEN not set")
        return []

    try:
        tasks = get_tasks()
    except Exception as e:
        log.error("failed to fetch tasks: %s", e)
        return []

    if not tasks:
        return []

    new_ids = []
    for t in tasks:
        tid = t["id"]
        url = t["source_url"]
        platform = (t.get("platform") or "").lower()
        uid = t.get("user_id") or 0
        try:
            if platform == "facebook" or "facebook.com" in url:
                latest = fb_latest(url, uid)
            elif platform in ("tiktok", "instagram"):
                latest = flat_latest(url, cookiefile=_cookie_file(platform, uid))
            else:
                latest = flat_latest(url)
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
            continue
        _state["task_err"] = None

        if not latest or not latest.get("id"):
            continue

        key = latest["id"]
        seen = t.get("last_seen_key")
        baseline = t.get("baseline_key")

        if baseline is None and seen is None:
            # First time we see this source: enqueue THE latest video so it
            # goes through the full pipeline (download+edit+upload), then set
            # it as baseline so the older back-catalog is never picked up.
            # On failure leave baseline unset → retried on the next loop.
            try:
                res = add_video(tid, key, latest.get("url") or url, latest.get("title"))
                if res.get("created"):
                    vid = res["video"]["id"]
                    new_ids.append(vid)
                    log.info("task %s first poll -> latest video %s id %s (dispatched=%s)",
                             tid, key, vid, res.get("dispatched"))
                    if not res.get("dispatched") and _should_trigger(f"vids:{vid}", 1800):
                        trigger_process([vid])
                else:
                    log.info("task %s first poll: latest %s already known", tid, key)
                patch_task(tid, baseline_key=key, last_seen_key=key)
                log.info("task %s baseline set to %s", tid, key)
            except Exception as e:
                log.warning("task %s first-poll enqueue failed (will retry): %s", tid, e)
            continue

        if key == seen:
            continue

        try:
            res = add_video(tid, key, latest.get("url") or url, latest.get("title"))
            if res.get("created"):
                vid = res["video"]["id"]
                new_ids.append(vid)
                log.info("NEW task %s video %s -> id %s (worker dispatched=%s)",
                         tid, key, vid, res.get("dispatched"))
                # Fallback only: worker is the primary GH triggerer. If its
                # dispatch failed, fire from here so the video isn't stranded.
                if not res.get("dispatched") and _should_trigger(f"vids:{vid}", 1800):
                    trigger_process([vid])
            patch_task(tid, last_seen_key=key)
        except Exception as e:
            log.warning("task %s add_video failed: %s", tid, e)

    return new_ids


def poll_loop():
    """Background loop: poll every 8-15s (randomized)."""
    log.info("fastpoll started — interval %d-%ds", POLL_MIN, POLL_MAX)
    while True:
        try:
            # Check manual jobs first
            _state["last_manual_check"] = time.time()
            found = check_manual_jobs()
            _state["last_manual_found"] = found
            # Then check for new videos from tasks
            new = poll_once()
            _state["last_poll"] = time.time()
            if new:
                log.info("found %d new video(s)", len(new))
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
    def do_GET(self):
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

    def log_message(self, *args):
        pass  # silence default access log


def main():
    port = int(os.getenv("PORT", "10000"))
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    ka = threading.Thread(target=keepalive_loop, daemon=True)
    ka.start()
    log.info("HTTP server on :%d (keepalive via public url)", port)
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
