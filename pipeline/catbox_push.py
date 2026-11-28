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
import urllib.parse
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
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "*/*",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.read().decode("utf-8", errors="replace").strip()


def upload(path: Path, userhash: str = "") -> str:
    """Publish to a permanent host; returns a public URL.

    Priority: GitHub Releases (permanent, works from runners) -> tmpfiles.org
    (1h expiry) -> Catbox (blocked on datacenter IPs with 412).
    """
    # 1) GitHub Releases — permanent public asset URL.
    gh_token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
    repo = os.getenv("GH_REPO") or os.getenv("REPO") or ""
    if gh_token and repo:
        import subprocess

        tag = f"nova-delivery-{int(time.time())}"
        cmd = [
            "gh", "release", "create", tag, str(path),
            "--repo", repo,
            "--title", f"NOVA delivery {tag}",
            "--notes", "Auto-generated delivery asset",
        ]
        try:
            env = dict(os.environ)
            env["GH_TOKEN"] = gh_token
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
            if proc.returncode == 0:
                encoded = urllib.parse.quote(path.name)
                return f"https://github.com/{repo}/releases/download/{tag}/{encoded}"
            print(
                f"[upload] release failed: {(proc.stderr or '')[:200]}",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"[upload] release error: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)

    # 2) tmpfiles.org — works from runners but expires after ~1 hour.
    tmp_url = "https://tmpfiles.org/api/v1/upload"
    for attempt in range(4):
        try:
            result = _post_form(
                tmp_url, {}, file_field=("file", path)
            )
            parsed = json.loads(result)
            if parsed.get("status") == "success" and parsed.get("data", {}).get("url"):
                url = parsed["data"]["url"]
                # tmpfiles.org URL -> direct download (/dl/) so yt-dlp/ExoPlayer
                # do not have to parse an HTML preview page.
                return url.replace("https://tmpfiles.org/", "https://tmpfiles.org/dl/", 1)
        except Exception as exc:
            print(f"[upload] tmpfiles attempt {attempt + 1}: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
        if attempt < 3:
            time.sleep(2 * (attempt + 1))

    # Fallback: Catbox (works from residential IPs; may 412 from runners).
    if os.getenv("CATBOX_USE_USERHASH", "0") == "1" and userhash:
        fields = {"reqtype": "fileupload", "userhash": userhash}
    else:
        fields = {"reqtype": "fileupload"}
    last = ""
    for attempt in range(3):
        try:
            result = _post_form(CATBOX_API, fields, file_field=("fileToUpload", path))
            if result.startswith("https://files.catbox.moe/"):
                return result
            last = f"unexpected response: {result[:120]}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {str(exc)[:120]}"
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(last or "upload failed")


def notify(delivery: dict, worker_url: str, token: str) -> dict:
    if not worker_url or not token:
        return {"skipped": "WORKER_URL / INTERNAL_TOKEN missing"}
    # requests is available in both CI and local env; urllib gets a 403 from
    # the Worker's Cloudflare edge (default Python UA / header handling).
    import requests

    resp = requests.post(
        worker_url.rstrip("/") + "/api/internal/deliveries",
        json=delivery,
        headers={"x-token": token},
        timeout=120,
    )
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"worker {resp.status_code}: {resp.text[:200]}")
    return resp.json()


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
