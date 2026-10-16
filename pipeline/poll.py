"""Poll active tasks, detect new videos, register them with the worker.

First poll for a task only records the baseline (no back-catalog upload).
Later polls register anything newer than the last seen key.
"""
import os
import sys
import json
import requests
import download as dl

WORKER = os.getenv("WORKER_URL", "").rstrip("/")
TOKEN = os.getenv("INTERNAL_TOKEN", "")


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
    r = requests.patch(
        f"{WORKER}/api/internal/tasks/{task_id}",
        headers=_h(),
        json=fields,
        timeout=30,
    )
    try:
        r.raise_for_status()
    except Exception:
        pass


def main():
    if not WORKER or not TOKEN:
        raise RuntimeError("WORKER_URL and INTERNAL_TOKEN required")

    tasks = get_tasks()
    print(f"[poll] {len(tasks)} active task(s)")
    new_count = 0
    new_ids = []

    for t in tasks:
        tid = t["id"]
        url = t["source_url"]
        try:
            latest = dl.flat_latest(url)
        except Exception as e:
            print(f"[poll] task {tid} fetch failed: {e}", file=sys.stderr)
            continue
        if not latest or not latest.get("id"):
            print(f"[poll] task {tid} no latest entry")
            continue

        key = latest["id"]
        seen = t.get("last_seen_key")
        baseline = t.get("baseline_key")

        if baseline is None and seen is None:
            # First poll: record baseline, skip back-catalog.
            patch_task(tid, baseline_key=key, last_seen_key=key)
            print(f"[poll] task {tid} baseline set to {key}")
            continue

        if key == seen:
            continue

        # New video.
        res = add_video(tid, key, latest.get("url") or url, latest.get("title"))
        if res.get("created"):
            new_count += 1
            new_ids.append(res["video"]["id"])
            print(f"[poll] NEW task {tid} video {key} -> id {res['video']['id']}")
        else:
            print(f"[poll] task {tid} video {key} already known")
        patch_task(tid, last_seen_key=key)

    print(f"[poll] {new_count} new video(s): {new_ids}")

    # Check for queued manual upload jobs (Quick Upload) — read-only peek.
    manual_id = ""
    try:
        r = requests.get(f"{WORKER}/api/internal/manual/next", headers=_h(), timeout=15)
        r.raise_for_status()
        job = r.json().get("job")
        if job:
            manual_id = str(job["id"])
            print(f"[poll] queued manual job: {manual_id} -> {job['url']}")
    except Exception as e:
        print(f"[poll] manual job check failed: {e}", file=sys.stderr)

    # Emit for the workflow (GITHUB_OUTPUT).
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"new_count={new_count}\n")
            f.write(f"video_ids={','.join(map(str, new_ids))}\n")
            f.write(f"manual_job_id={manual_id}\n")


if __name__ == "__main__":
    main()
