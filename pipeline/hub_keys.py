"""Snapshot API keys + service config from the hub for the pipeline run.

Writes RUNNER_TEMP/nova_hub.json (read by apikeys.py) and masks every key in
the workflow log. Any failure is non-fatal: the pipeline falls back to the
GitHub secrets it already has.
"""
import json
import os
import sys
import urllib.request

KEY_ENV = {
    "FASTSAVER_API_KEYS": "fastsaver",
    "YT_API_KEYS": "ytapi",
    "NVIDIA_API_KEYS": "nvidia",
    "GEMINI_API_KEYS": "gemini",
    "OPENROUTER_API_KEYS": "openrouter",
}


def main():
    base = (os.getenv("WORKER_URL") or "").rstrip("/")
    token = os.getenv("INTERNAL_TOKEN") or ""
    if not base or not token:
        print("hub keys: WORKER_URL/INTERNAL_TOKEN missing, using env fallback", file=sys.stderr)
        return 0

    req = urllib.request.Request(f"{base}/api/internal/keys/env", headers={"x-token": token})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except Exception as e:
        print(f"hub keys: skipped ({e})", file=sys.stderr)
        return 0

    env = data.get("env") or {}
    by_service = {}
    for name, service in KEY_ENV.items():
        values = [k.strip() for k in str(env.get(name) or "").split(",") if k.strip()]
        if values:
            by_service[service] = values

    path = os.getenv("NOVA_HUB_FILE") or os.path.join(
        os.getenv("RUNNER_TEMP") or "/tmp", "nova_hub.json"
    )
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"keys": by_service, "settings": data.get("settings") or {}}, f)
    except Exception as e:
        print(f"hub keys: cannot write {path} ({e})", file=sys.stderr)
        return 0

    for values in by_service.values():
        for key in values:
            print(f"::add-mask::{key}")
    total = sum(len(v) for v in by_service.values())
    print(f"hub keys: {total} key(s) from {len(by_service)} service(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
