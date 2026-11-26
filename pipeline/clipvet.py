"""Clip vetting for the NOVA documentary path (free stock, Option B).

Problem this solves
-------------------
Pixabay search terms are only a text match. "pouring espresso" can return a
strawberry milkshake, and MoneyPrinterTurbo would happily put it under a line
about midnight prayer. QC on the finished video catches it too late.

What this does instead
----------------------
1. search Pixabay once per shot.visual (ordered, portrait, >= clip length)
2. download a few candidates per shot
3. pull one representative frame out of each candidate
4. ask NVIDIA Nemotron Super VL whether the frame actually shows the sentence
5. keep the best matching clip per shot, in script order

The result is a plain list of local mp4 paths, handed to MoneyPrinterTurbo via
--video-source local --video-materials, so the stock engine never has to guess.

NVIDIA is used here for video analysis -> JSON only, which is the routing the
project pinned. No text work happens in this module.

Usage:
    python pipeline/clipvet.py --brief pipeline/docs/<slug>/brief.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import imageio_ffmpeg
except Exception:  # ffmpeg from PATH then
    imageio_ffmpeg = None

PIXABAY_KEY_FILE = ROOT / "pixabay api key.txt"
PIXABAY_URL = "https://pixabay.com/api/videos/"

# Pixabay has far more landscape than portrait footage, so a term can easily
# have zero portrait clips. Falling back to a high-res landscape clip and
# cropping it beats leaving a hole in the timeline.
ASPECT_MIN = {
    "9:16": {"portrait": (1080, 1920), "landscape": (1920, 1080), "square": (1080, 1080)},
    "16:9": {"portrait": (1920, 1080), "landscape": (1920, 1080), "square": (1080, 1080)},
    "1:1": {"portrait": (1080, 1080), "landscape": (1080, 1080), "square": (1080, 1080)},
}


class VetError(RuntimeError):
    pass


def pixabay_key() -> str:
    for name in ("PIXABAY_API_KEYS", "PIXABAY_API_KEY"):
        for value in (os.getenv(name) or "").split(","):
            value = value.strip()
            if value:
                return value
    if PIXABAY_KEY_FILE.exists():
        # The key file has the key on line 1 and sometimes a URL on line 2.
        for line in PIXABAY_KEY_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if len(line) >= 20 and " " not in line and "/" not in line:
                return line
    raise VetError("no Pixabay key (env PIXABAY_API_KEYS or 'pixabay api key.txt')")


def ffmpeg_exe() -> str:
    if imageio_ffmpeg is not None:
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    found = shutil.which("ffmpeg")
    if not found:
        raise VetError("ffmpeg not available (pip install imageio-ffmpeg)")
    return found


# ---------------------------------------------------------------------------
# Pixabay search + download
# ---------------------------------------------------------------------------

def search_pixabay(term: str, aspect: str = "9:16", min_duration: int = 5,
                   per_page: int = 50) -> list[dict]:
    """Candidates for one term, native-orientation first then a crop fallback.

    Never raises on HTTP errors - an empty list just means "no usable clip".
    """
    needs = ASPECT_MIN.get(aspect, ASPECT_MIN["9:16"])
    try:
        resp = requests.get(
            PIXABAY_URL,
            params={"q": term, "video_type": "all", "per_page": per_page, "key": pixabay_key()},
            timeout=(30, 60),
        )
    except requests.RequestException as exc:
        print(f"[clipvet] search failed for {term!r}: {exc}")
        return []
    if resp.status_code != 200:
        print(f"[clipvet] search {term!r}: HTTP {resp.status_code}")
        return []
    try:
        hits = (resp.json() or {}).get("hits") or []
    except ValueError:
        return []

    native: list[dict] = []
    croppable: list[dict] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        duration = hit.get("duration")
        if not isinstance(duration, (int, float)) or not math.isfinite(duration):
            continue
        if duration < min_duration:
            continue
        for kind, video in (hit.get("videos") or {}).items():
            if not isinstance(video, dict):
                continue
            try:
                w, h = int(video["width"]), int(video["height"])
            except (KeyError, TypeError, ValueError):
                continue
            url = video.get("url")
            if not isinstance(url, str) or not url:
                continue
            orientation = "portrait" if h > w else ("landscape" if w > h else "square")
            entry = {
                "url": url,
                "duration": duration,
                "width": w,
                "height": h,
                "orientation": orientation,
                "needs_crop": orientation != aspect,
                "term": term,
                "rendition": kind,
                "asset_id": str(hit.get("id") or ""),
                "page": hit.get("pageURL") or "",
            }
            if orientation == aspect:
                if w >= needs["portrait"][0] and h >= needs["portrait"][1]:
                    native.append(entry)
            elif w >= needs[orientation][0] and h >= needs[orientation][1]:
                # Cropping discards pixels, so only keep generous sources.
                croppable.append(entry)

    # Native clips first; the crop pool is a last resort, biggest first.
    croppable.sort(key=lambda c: c["width"] * c["height"], reverse=True)
    return native + croppable


def download(url: str, dest: Path, timeout: int = 180) -> Path | None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 50_000:
        return dest
    try:
        with requests.get(url, stream=True, timeout=(30, timeout)) as resp:
            if resp.status_code != 200:
                return None
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if chunk:
                        fh.write(chunk)
    except requests.RequestException:
        return None
    if dest.stat().st_size < 50_000:
        dest.unlink(missing_ok=True)
        return None
    return dest


def probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except Exception:
        return 0.0
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out.stderr or "")
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def fit_aspect(video: Path, aspect: str, out_mp4: Path) -> Path | None:
    """Centre-crop a clip to the target aspect when Pixabay had no native fit.

    Cropping is the only honest way to use landscape stock in a 9:16 short;
    stretching would distort the subject. Re-encoded at a modest CRF so the
    file stays small and the VL frame matches what the final video shows.
    """
    dims = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
    width, height = dims.get(aspect, dims["9:16"])
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    crop = f"crop='min(iw,ih*{width}/{height})':'min(ih,iw*{height}/{width})'"
    try:
        subprocess.run(
            [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video),
             "-vf", f"{crop},scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
             "-an", str(out_mp4)],
            check=True, timeout=900,
        )
    except Exception as exc:
        print(f"[clipvet] crop failed for {video.name} ({str(exc)[:100]})")
        return None
    return out_mp4 if out_mp4.exists() and out_mp4.stat().st_size > 50_000 else None


def middle_frame(video: Path, out_jpg: Path) -> Path | None:
    """One JPEG from the middle of the clip - good enough for a relevance call."""
    duration = probe_duration(video)
    target = max(0.5, duration / 2)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", f"{target:.2f}",
             "-i", str(video), "-frames:v", "1", "-vf", "scale=640:-2", str(out_jpg)],
            check=True, timeout=120,
        )
    except Exception:
        return None
    return out_jpg if out_jpg.exists() and out_jpg.stat().st_size > 2000 else None


# ---------------------------------------------------------------------------
# NVIDIA VL relevance verdict
# ---------------------------------------------------------------------------

VERDICT_SCHEMA = {
    "verdicts": [
        {"index": "<number as shown in the label>",
         "relevance": "<0-10: 10 = exactly the scene, 5-7 = usable generic B-roll for it, "
                      "0-2 = clearly a different subject>",
         "reason": "<=12 words"}
    ]
}

# A stock clip rarely depicts a sentence literally. Generic B-roll that fits the
# mood still works, so only a very low score should ever reject a clip.
MIN_RELEVANCE = 3


def judge(frames: list[tuple[str, Path]], *, key: str | None = None) -> dict[int, dict]:
    """Score each frame 0-10 against its sentence. Returns {index: verdict}.

    One call per shot keeps latency predictable. Frames are labelled in the
    prompt so the model cannot mix them up.
    """
    if not frames:
        return {}
    from nova_nvidia import MODEL_VL, VISION_URL, _media_block, _parse_json, _post

    content: list[dict] = []
    lines = ["For each numbered image below, score how well it works as background footage "
             "for the scene named in its label."]
    lines.append("Judge only subject relevance - ignore lighting, resolution and camera work.")
    lines.append("Generic B-roll that fits the mood still scores high. Only score low when the "
                 "subject is clearly unrelated to the named scene.")
    lines.append("")
    for index, (label, frame) in enumerate(frames, start=1):
        lines.append(f"Image {index}: scene -> {label}")
        content.append(_media_block(frame))
    lines.append("")
    lines.append(f"Return ONLY valid JSON matching: {json.dumps(VERDICT_SCHEMA, ensure_ascii=False)}")
    content.append({"type": "text", "text": "\n".join(lines)})

    data = _post(VISION_URL, {
        "model": MODEL_VL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 900,
        "temperature": 0.2,
        "top_p": 0.95,
        "chat_template_kwargs": {"enable_thinking": False},
    }, key=key)
    message = (data.get("choices") or [{}])[0].get("message") or {}
    parsed = _parse_json((message.get("content") or "").strip())

    out: dict[int, dict] = {}
    for verdict in (parsed.get("verdicts") or []):
        if not isinstance(verdict, dict):
            continue
        try:
            index = int(verdict.get("index"))
        except (TypeError, ValueError):
            continue
        relevance = _confidence(verdict.get("relevance") or verdict.get("match"))
        out[index] = {
            "relevance": relevance,
            "match": relevance >= MIN_RELEVANCE,
            "reason": str(verdict.get("reason") or "")[:80],
        }
    return out


def _confidence(value) -> int:
    if isinstance(value, (int, float)):
        return max(0, min(10, int(value)))
    digits = re.findall(r"\d+", str(value or ""))
    return max(0, min(10, int(digits[0]))) if digits else 5


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def vet_shots(
    shots: list[dict],
    out_dir: Path,
    *,
    aspect: str = "9:16",
    clip_duration: int = 5,
    candidates_per_shot: int = 3,
    needed_seconds: float = 0.0,
) -> dict:
    """Pick one verified clip per shot, in order.

    Returns {"clips": [Path...], "report": [...], "total_seconds": float}.
    A shot whose candidates all fail the verdict keeps its best candidate
    rather than leaving a hole in the timeline.
    """
    work = Path(tempfile.mkdtemp(prefix="nova_clipvet_")) if str(out_dir) == "-" else Path(out_dir) / "clips"
    work.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    picked: list[dict] = []

    for position, shot in enumerate(shots, start=1):
        sentence = str(shot.get("sentence") or "").strip()
        visual = str(shot.get("visual") or "").strip()
        if not visual:
            continue
        candidates = search_pixabay(visual, aspect=aspect, min_duration=clip_duration)
        if not candidates:
            print(f"[clipvet] {position}. {visual!r}: no pixabay candidates")
            report.append({"position": position, "visual": visual, "picked": None,
                           "reason": "no candidates"})
            continue
        candidates = candidates[:candidates_per_shot]

        saved: list[dict] = []
        for rank, cand in enumerate(candidates, start=1):
            path = work / f"shot{position:02d}_c{rank}.mp4"
            file_path = download(cand["url"], path)
            if not file_path:
                continue
            # Crop to the output aspect first, so the frame the VL model judges
            # is the same framing the final video will show.
            if cand.get("needs_crop"):
                cropped = fit_aspect(file_path, aspect, work / f"shot{position:02d}_c{rank}_fit.mp4")
                if cropped:
                    file_path.unlink(missing_ok=True)
                    file_path = cropped
            frame = middle_frame(file_path, work / f"shot{position:02d}_c{rank}.jpg")
            cand["path"] = file_path
            cand["frame"] = frame
            cand["actual_seconds"] = probe_duration(file_path)
            saved.append(cand)

        if not saved:
            report.append({"position": position, "visual": visual, "picked": None,
                           "reason": "download failed"})
            continue

        with_frames = [c for c in saved if c["frame"]]
        verdicts: dict[int, dict] = {}
        if with_frames and sentence:
            try:
                verdicts = judge(
                    [(f"{visual} (scene: {sentence})", c["frame"]) for c in with_frames]
                )
            except Exception as exc:
                print(f"[clipvet] {position}. verdict failed ({str(exc)[:120]}); keeping first")

        # rank: a matching frame first, then by how confident the model was
        for offset, cand in enumerate(with_frames, start=1):
            cand["verdict"] = verdicts.get(offset, {"match": None, "confidence": 0, "reason": ""})
        for cand in saved:
            verdict = cand.get("verdict") or {"match": None, "confidence": 0, "reason": ""}
            cand["score"] = (100 if verdict["match"] else 0) + verdict["confidence"]
        saved.sort(key=lambda c: c["score"], reverse=True)

        best = saved[0]
        verdict = best.get("verdict") or {}
        picked.append(best)
        report.append({
            "position": position,
            "visual": visual,
            "sentence": sentence,
            "picked": best["asset_id"],
            "file": str(best["path"]),
            "seconds": round(best.get("actual_seconds", 0.0), 2),
            "match": verdict.get("match"),
            "confidence": verdict.get("confidence"),
            "reason": verdict.get("reason"),
            "candidates": len(saved),
        })
        mark = "OK " if verdict.get("match") else ("~  " if verdict.get("match") is None else "X  ")
        print(f"[clipvet] {position}. {mark}{visual!r} -> {verdict.get('reason') or 'kept'}")

    total = sum(c.get("actual_seconds", 0.0) for c in picked)
    # A short timeline leaves black frames at the end, so top up with the next
    # best unused candidate if the picked clips do not cover the narration.
    if needed_seconds and total < needed_seconds:
        used = {str(c["path"]) for c in picked}
        extras = sorted(
            (c for c in report_sources(work) if str(c["path"]) not in used),
            key=lambda c: c.get("score", 0), reverse=True,
        )
        for extra in extras:
            if total >= needed_seconds:
                break
            picked.append(extra)
            total += extra.get("actual_seconds", 0.0)
            print(f"[clipvet] topped up with {Path(extra['path']).name}")

    return {
        "clips": [Path(c["path"]) for c in picked],
        "report": report,
        "total_seconds": round(total, 2),
        "work_dir": str(work),
    }


def report_sources(work: Path) -> list[dict]:
    """Every downloaded clip in the work dir, as fallback filler material."""
    out: list[dict] = []
    for path in sorted(work.glob("shot*_c*.mp4")):
        out.append({
            "path": path,
            "actual_seconds": probe_duration(path),
            "asset_id": path.stem,
            "score": 0,
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Vet Pixabay clips against narration (NVIDIA VL)")
    parser.add_argument("--brief", required=True, help="brief.json with shots")
    parser.add_argument("--out", default=None, help="output dir (default beside the brief)")
    parser.add_argument("--aspect", default="9:16", choices=["9:16", "16:9", "1:1"])
    parser.add_argument("--clip-duration", type=int, default=5)
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--needed-seconds", type=float, default=0.0)
    args = parser.parse_args()

    brief = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    shots = brief.get("shots") or []
    if not shots:
        print("[clipvet] brief has no shots")
        return 2

    out_dir = Path(args.out) if args.out else Path(args.brief).resolve().parent
    result = vet_shots(
        shots, out_dir, aspect=args.aspect, clip_duration=args.clip_duration,
        candidates_per_shot=args.candidates, needed_seconds=args.needed_seconds,
    )
    (out_dir / "clipvet.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"[clipvet] {len(result['clips'])} clips / {result['total_seconds']}s -> {out_dir / 'clipvet.json'}")
    print()
    print(",".join(str(p) for p in result["clips"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
