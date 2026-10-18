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
import time
import random
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


def flat_latest(url):
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
        try:
            latest = flat_latest(url)
        except Exception as e:
            log.warning("task %s fetch failed: %s", tid, e)
            continue

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
