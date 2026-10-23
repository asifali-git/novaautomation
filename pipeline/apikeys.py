"""Shared API-key pool + service config for the pipeline.

Keys come from the hub snapshot written by hub_keys.py (RUNNER_TEMP/nova_hub.json),
falling back to GitHub env/secrets. Order is the panel's priority order.
"""
import json
import os

HUB_FILE = os.getenv("NOVA_HUB_FILE") or os.path.join(
    os.getenv("RUNNER_TEMP") or "/tmp", "nova_hub.json"
)

_cache = {"loaded": False, "data": {}}


def _hub():
    if _cache["loaded"]:
        return _cache["data"]
    data = {}
    try:
        with open(HUB_FILE, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
    _cache["data"] = data if isinstance(data, dict) else {}
    _cache["loaded"] = True
    return _cache["data"]


def _split(value):
    return [k.strip() for k in str(value or "").split(",") if k.strip()]


def keys(service, *env_names):
    """Ordered, de-duplicated key list for `service` (hub first, then env)."""
    out = []
    for k in (_hub().get("keys") or {}).get(service) or []:
        if k and k not in out:
            out.append(k)
    for name in env_names:
        for k in _split(os.getenv(name)):
            if k and k not in out:
                out.append(k)
    return out


def setting(name, default=None):
    """Hub setting first, then upper-case env, then default."""
    val = (_hub().get("settings") or {}).get(name)
    if val in (None, ""):
        val = os.getenv(name.upper())
    return default if val in (None, "") else val


def report(service, index, ok, error=None):
    """Best-effort per-key health report so the panel can show real state."""
    base = (os.getenv("WORKER_URL") or "").rstrip("/")
    token = os.getenv("INTERNAL_TOKEN") or ""
    if not base or not token:
        return
    try:
        import requests

        requests.post(
            f"{base}/api/internal/keys/report",
            headers={"x-token": token},
            json={"service": service, "index": index, "ok": bool(ok), "error": error},
            timeout=10,
        )
    except Exception:
        pass
