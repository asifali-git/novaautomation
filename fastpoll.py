"""Fast poller — checks for new videos every 8-15 seconds (randomized).

Runs as a Render web service. A background thread loops yt-dlp checks
against active tasks and registers new videos with the worker.
Optionally triggers the GitHub Actions process workflow on new finds.

Env vars:
  WORKER_URL, INTERNAL_TOKEN   — worker API access
  GH_TOKEN, GH_REPO            — trigger process.yml (optional)
  POLL_MIN, POLL_MAX           — seconds between polls (default 8 / 15)
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
POLL_MAX = int(os.getenv("POLL_MAX", "15"))

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
    try:
        requests.patch(
            f"{WORKER}/api/tasks/{task_id}",
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
            patch_task(tid, baseline_key=key, last_seen_key=key)
            log.info("task %s baseline set to %s", tid, key)
            continue

        if key == seen:
            continue

        try:
            res = add_video(tid, key, latest.get("url") or url, latest.get("title"))
            if res.get("created"):
                vid = res["video"]["id"]
                new_ids.append(vid)
                log.info("NEW task %s video %s -> id %s", tid, key, vid)
            patch_task(tid, last_seen_key=key)
        except Exception as e:
            log.warning("task %s add_video failed: %s", tid, e)

    if new_ids:
        trigger_process(new_ids)
    return new_ids


def poll_loop():
    """Background loop: poll every 8-15s (randomized)."""
    log.info("fastpoll started — interval %d-%ds", POLL_MIN, POLL_MAX)
    while True:
        try:
            # Check manual jobs first
            check_manual_jobs()
            # Then check for new videos from tasks
            new = poll_once()
            if new:
                log.info("found %d new video(s)", len(new))
        except Exception as e:
            log.error("poll error: %s", e)
        time.sleep(random.randint(POLL_MIN, POLL_MAX))


def keepalive_loop():
    """Ping self every 10-13 min so Render free tier doesn't sleep."""
    port = os.getenv("PORT", "10000")
    self_url = os.getenv("SELF_URL", f"http://127.0.0.1:{port}/health")
    while True:
        interval = random.randint(600, 780)  # 10-13 minutes
        time.sleep(interval)
        try:
            requests.get(self_url, timeout=10)
            log.info("keepalive ping sent (slept %ds)", interval)
        except Exception as e:
            log.warning("keepalive ping failed: %s", e)


# Minimal HTTP server so Render keeps the instance alive (health check).
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b'{"ok":true}'
            self.send_response(200)
        else:
            body = b'{"service":"fastpoll","endpoints":["/health"]}'
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
    log.info("HTTP server on :%d", port)
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
