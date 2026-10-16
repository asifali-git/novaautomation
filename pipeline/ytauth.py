"""YouTube OAuth refresh + resumable upload."""
import os
import time
import json
import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"


def _client_creds():
    # Optional per-login override (multi-project quota); fall back to env.
    cid = os.getenv("YT_CLIENT_ID")
    sec = os.getenv("YT_CLIENT_SECRET")
    return cid, sec


def use_login(login_id, base_url, x_token):
    """Point YT_* env at a specific worker login (multi-account uploads)."""
    r = requests.get(
        f"{str(base_url).rstrip('/')}/api/internal/logins/{login_id}",
        headers={"x-token": x_token},
        timeout=30,
    )
    r.raise_for_status()
    tok = ((r.json().get("login") or {}).get("token")) or {}
    if tok.get("refresh_token"):
        os.environ["YT_REFRESH_TOKEN"] = tok["refresh_token"]
    if tok.get("client_id"):
        os.environ["YT_CLIENT_ID"] = tok["client_id"]
    if tok.get("client_secret"):
        os.environ["YT_CLIENT_SECRET"] = tok["client_secret"]
    print(f"[yt] using YouTube login {login_id}")


def refresh_access_token():
    cid, sec = _client_creds()
    refresh = os.getenv("YT_REFRESH_TOKEN")
    if not refresh:
        raise RuntimeError("YT_REFRESH_TOKEN not set")
    if not cid or not sec:
        raise RuntimeError("YT_CLIENT_ID / YT_CLIENT_SECRET not set")
    r = requests.post(
        TOKEN_URL,
        data={
            "refresh_token": refresh,
            "client_id": cid,
            "client_secret": sec,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def upload_video(file_path, title, description="", tags=None, privacy="public", category="22", access_token=None):
    """Resumable upload. Returns video id."""
    token = access_token or refresh_access_token()
    tags = tags or []
    meta = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:30],
            "categoryId": category,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    size = os.path.getsize(file_path)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(size),
        "X-Upload-Content-Type": "video/mp4",
    }
    r = requests.post(UPLOAD_URL, headers=headers, data=json.dumps(meta), timeout=60)
    r.raise_for_status()
    upload_url = r.headers.get("Location")
    if not upload_url:
        raise RuntimeError(f"No resumable session: {r.status_code} {r.text}")

    def put(start, end):
        with open(file_path, "rb") as f:
            f.seek(start)
            chunk = f.read(end - start + 1)
        h = {
            "Authorization": f"Bearer {token}",
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {start}-{end}/{size}",
        }
        return requests.put(upload_url, headers=h, data=chunk, timeout=300)

    chunk_size = 8 * 1024 * 1024
    start = 0
    while start < size:
        end = min(start + chunk_size - 1, size - 1)
        resp = put(start, end)
        if resp.status_code in (200, 201):
            body = resp.json()
            return body.get("id")
        if resp.status_code == 308:
            start = end + 1
            continue
        raise RuntimeError(f"Upload failed {resp.status_code}: {resp.text[:300]}")
    raise RuntimeError("Upload incomplete")


def get_channel_title(access_token=None):
    token = access_token or refresh_access_token()
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if r.ok:
        items = r.json().get("items") or []
        if items:
            return items[0]["snippet"]["title"]
    return None
