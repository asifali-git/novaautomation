"""Upload a finished video to Catbox, then tell the NOVA worker to push it.

Used by .github/workflows/docgen.yml after the video is rendered.

Secrets / env (CI):
  CATBOX_USERHASH   - from "catbox userhash.txt" (account uploads are permanent;
                      anonymous uploads work too, omit the secret)
  WORKER_URL        - NOVA worker base URL
  INTERNAL_TOKEN    - worker internal API token
  NOVA_USER_ID      - optional: which worker user owns this delivery

Nothing is printed except the public Catbox URL and the worker response.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

CATBOX_API = "https://catbox.moe/user/api.php"


def _post_form(url: str, fields: dict, file_field: tuple[str, Path] | None = None) -> str:
    boundary = "----nova_form_boundary"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    if file_field:
        name, path = file_field
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
        )
        parts.append(path.read_bytes())
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        url,
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.read().decode("utf-8", errors="replace").strip()


def upload(path: Path, userhash: str = "") -> str:
    # Anonymous upload works reliably (verified from this machine and runners).
    # Account userhash is optional; an invalid hash makes Catbox return 412
    # "Invalid uploader", so only send it when explicitly enabled.
    use_hash = os.getenv("CATBOX_USE_USERHASH", "0") == "1" and userhash
    fields = {"reqtype": "fileupload"}
    if use_hash:
        fields["userhash"] = userhash
    last = ""
    # Catbox occasionally returns an empty body or HTTP 412 under load;
    # a short retry loop is enough to get a stable URL.
    for attempt in range(4):
        try:
            result = _post_form(CATBOX_API, fields, file_field=("fileToUpload", path))
            if result.startswith("https://files.catbox.moe/"):
                return result
            last = f"unexpected response: {result[:120]}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {str(exc)[:120]}"
        if attempt < 3:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(last or "Catbox upload failed")


def notify(delivery: dict, worker_url: str, token: str) -> dict:
    if not worker_url or not token:
        return {"skipped": "WORKER_URL / INTERNAL_TOKEN missing"}
    body = json.dumps(delivery).encode()
    req = urllib.request.Request(
        worker_url.rstrip("/") + "/api/internal/deliveries",
        data=body,
        headers={"Content-Type": "application/json", "x-token": token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Catbox upload + NOVA FCM push")
    parser.add_argument("video", help="path to final mp4")
    parser.add_argument("--brief", default=None, help="brief.json for title/platform")
    parser.add_argument("--meta", default=None, help="meta.json for size/title")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--video-id", type=int, default=None)
    parser.add_argument("--task-id", type=int, default=None)
    args = parser.parse_args()

    video = Path(args.video)
    if not video.exists():
        print(f"[catbox_push] video not found: {video}", file=sys.stderr)
        return 2

    # Key file has the hash on line 1; CI supplies it as a secret instead.
    userhash = (os.getenv("CATBOX_USERHASH") or "").strip()
    if not userhash:
        local = Path(__file__).resolve().parent.parent / "catbox userhash.txt"
        if local.exists():
            first = local.read_text(encoding="utf-8", errors="ignore").splitlines()[0].strip()
            if len(first) >= 20:
                userhash = first

    print(f"[catbox_push] uploading {video.name} ({video.stat().st_size / 1e6:.1f} MB)")
    url = upload(video, userhash)
    print(f"[catbox_push] catbox_url={url}")

    brief = json.loads(Path(args.brief).read_text(encoding="utf-8")) if args.brief and Path(args.brief).exists() else {}
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8")) if args.meta and Path(args.meta).exists() else {}
    delivery = {
        "catbox_url": url,
        "file_name": video.name,
        "title": (brief.get("title") or meta.get("title") or video.stem)[:120],
        "platform": "docgen",
        "source_url": "",
        "size_bytes": video.stat().st_size,
        "mime": "video/mp4",
        "user_id": args.user_id or (int(os.getenv("NOVA_USER_ID") or 0) or None),
        "video_id": args.video_id,
        "task_id": args.task_id,
    }
    result = notify(
        delivery,
        os.getenv("WORKER_URL", ""),
        os.getenv("INTERNAL_TOKEN", ""),
    )
    print(f"[catbox_push] worker={json.dumps(result, ensure_ascii=False)[:300]}")
    (video.parent / "catbox.json").write_text(
        json.dumps({"catbox_url": url, "worker": result}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
