"""YouTube download API server (production).

Deploy on Render (or any server). GitHub Actions calls:
  POST {YT_API_URL}/download
  Header: X-API-Key: {YT_API_KEY}
  Body:   {"url": "...", "max_secs": 60}
"""
import glob
import os
import secrets
import sys
import tempfile

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import yt_dlp

# ffmpeg for merges + download_ranges (Render apt-installed; local imageio fallback).
try:
    import imageio_ffmpeg
    _ffdir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    os.environ["PATH"] = _ffdir + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

API_KEY_FILE = os.path.join(os.path.dirname(__file__), "ytapi_key.txt")
PORT = int(os.getenv("PORT", os.getenv("YTAPI_PORT", "8787")))

app = FastAPI(title="Nova YT Download API")


def _load_or_create_key() -> str:
    env_key = os.getenv("YT_API_KEY", "").strip()
    if env_key:
        return env_key
    if os.path.exists(API_KEY_FILE):
        key = open(API_KEY_FILE, encoding="utf-8").read().strip()
        if key:
            return key
    key = secrets.token_urlsafe(32)
    with open(API_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key)
    return key


API_KEY = _load_or_create_key()


class DLReq(BaseModel):
    url: str
    max_secs: int | None = None


def _check_key(x_api_key: str):
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")


@app.get("/health")
def health():
    return {"ok": True, "service": "nova-yt-download"}


@app.post("/download")
def download(req: DLReq, x_api_key: str = Header(default="")):
    _check_key(x_api_key)
    u = (req.url or "").strip()
    if "youtube.com" not in u and "youtu.be" not in u:
        raise HTTPException(status_code=400, detail="only youtube urls supported")

    out_dir = tempfile.mkdtemp(prefix="ytapi_")
    opts = {
        "outtmpl": os.path.join(out_dir, "src.%(ext)s"),
        "merge_output_format": "mp4",
        "format": "bv*+ba/b",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "prefer_free_formats": False,
        # visionos gives up to 1080p60 on residential IPs without cookies.
        "extractor_args": {"youtube": {"player_client": ["visionos"]}},
    }
    if req.max_secs:
        opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [(0, req.max_secs)])

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(u, download=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"download failed: {e}")

    files = [f for f in glob.glob(os.path.join(out_dir, "src.*"))
             if not f.endswith((".part", ".ytdl"))]
    if not files:
        raise HTTPException(status_code=500, detail="no output file")
    files.sort(key=lambda p: (0 if p.endswith(".mp4") else 1, -os.path.getsize(p)))
    return FileResponse(files[0], media_type="video/mp4", filename="src.mp4")


if __name__ == "__main__":
    print(f"[ytapi] key file: {API_KEY_FILE}", file=sys.stderr)
    print(f"[ytapi] listening on 0.0.0.0:{PORT}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
