"""NOVA documentary generator — free stock path.

Pipeline inside (all free):
  script      pipeline/brains.py  -> Gemini, fallback OpenRouter, fallback NVIDIA
  clips       Pixabay (free stock B-roll, so no character continuity needed)
  voiceover   Edge TTS (free, language-matched narrator)
  subtitles   Edge timing (free)

Languages: en (English), hi (Hindi/Devanagari), hinglish (Roman-script Hinglish).
Hindi subtitles need a Devanagari font - pass --font-name or let it auto-pick.

NVIDIA is *not* used here; it stays reserved for text-to-video (Cosmos) and
video analysis (Nemotron VL -> JSON).

Usage:
  python pipeline/docgen.py --topic "the history of coffee"
  python pipeline/docgen.py --topic "..." --language hi --aspect 9:16
  python pipeline/docgen.py --topic "..." --language hinglish --paragraphs 2
  python pipeline/docgen.py --brief pipeline/docs/<slug>/brief.json   # reuse a script
  python pipeline/docgen.py --topic "..." --stop-at materials        # build only

Output:
  pipeline/docs/<slug>/brief.json + meta.json + final-1.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import brains  # noqa: E402

# Windows consoles default to cp1252 -> Devanagari output would crash prints.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MPT_DIR = Path(os.getenv("MPT_DIR", r"D:\repos\MoneyPrinterTurbo"))
MPT_CLI = MPT_DIR / "cli.py"
MPT_STORAGE = MPT_DIR / "storage" / "tasks"
OUT_ROOT = HERE / "docs"

# voice + subtitle font per language. MPT reads fonts from resource/fonts.
LANGUAGE_SETUP = {
    "en": {"voice": "en-US-AndrewNeural-Male", "mpt_lang": "en", "font": ""},
    "hi": {"voice": "hi-IN-MadhurNeural-Male", "mpt_lang": "hi", "font": "NotoSansDevanagari-Bold.ttf"},
    "hinglish": {"voice": "en-IN-PrabhatNeural-Male", "mpt_lang": "en", "font": "BeVietnamPro-Bold.ttf"},
}

DEFAULT_VOICE = LANGUAGE_SETUP["en"]["voice"]  # calm documentary narration


def slugify(text: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit] or "doc"


def load_brief(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("script"):
        raise SystemExit(f"[docgen] brief has no script: {path}")
    return data


def make_brief(topic: str, paragraphs: int, language: str, angle: str) -> dict:
    print(f"[docgen] script brain chain: {brains.providers() or 'none'}")
    brief = brains.documentary_brief(topic, paragraphs=paragraphs, language=language, angle=angle)
    print(f"[docgen] script via {brief['provider']} / {brief['model']} ({brief['words']} words)")
    for attempt in brief.get("attempts", []):
        state = "ok" if attempt["ok"] else "fail"
        print(f"  - {attempt['provider']}/{attempt['model']}: {state} {attempt['error'][:120]}")
    if not brief["script"]:
        raise SystemExit("[docgen] every provider failed to produce a script")
    return brief


def build_command(args: argparse.Namespace, brief: dict) -> list[str]:
    terms = ",".join(brief.get("search_terms") or [])
    setup = LANGUAGE_SETUP[args.language]
    cmd = [
        sys.executable,
        str(MPT_CLI),
        "--video-subject", brief.get("title") or args.topic,
        "--video-script", brief["script"],
        "--video-language", setup["mpt_lang"],
        "--video-source", args.source,
        "--video-aspect", args.aspect,
        "--voice-name", args.voice,
        "--voice-rate", str(args.voice_rate),
        "--video-count", "1",
        "--video-concat-mode", "sequential",
        "--video-transition-mode", "fade-in",
        "--video-clip-duration", str(args.clip_duration),
        "--subtitle-position", args.subtitle_position,
        "--subtitle-display-mode", "sentence",
        "--n-threads", str(args.threads),
    ]
    if terms:
        cmd += ["--video-terms", terms]
    if args.font_name:
        cmd += ["--font-name", args.font_name]
    cmd.append("--subtitle-enabled" if args.subtitle else "--no-subtitle-enabled")
    if args.stop_at:
        cmd += ["--stop-at", args.stop_at]
    return cmd


def run(cmd: list[str], log_path: Path) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=str(MPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)
            log.write(line)
            log.flush()
            sys.stdout.write(line)
        proc.wait()
    return proc.returncode, "".join(chunks)


def parse_task_id(output: str) -> str | None:
    match = re.search(r'"task_id"\s*:\s*"([0-9a-f-]{36})"', output)
    if match:
        return match.group(1)
    matches = re.findall(r"task_id=([0-9a-f-]{36})", output)
    return matches[-1] if matches else None


def collect_outputs(task_id: str, out_dir: Path) -> dict:
    task_dir = MPT_STORAGE / task_id
    info: dict = {"task_id": task_id, "task_dir": str(task_dir), "files": []}
    if not task_dir.exists():
        return info
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("final-1.mp4", "combined-1.mp4", "audio.mp3", "subtitle.srt", "script.json"):
        src = task_dir / name
        if src.exists():
            dst = out_dir / name
            shutil.copy2(src, dst)
            info["files"].append(name)
            if name == "final-1.mp4":
                info["final"] = str(dst)
                info["size_mb"] = round(src.stat().st_size / (1024 * 1024), 2)
    return info


def run_qc(video: Path, out_dir: Path) -> None:
    """NVIDIA Nemotron VL analysis of the finished video -> qc.json (never fatal)."""
    try:
        from nova_nvidia import analyze_video
    except Exception as exc:  # module moved/missing
        print(f"[docgen] qc skipped ({exc})")
        return
    try:
        result = analyze_video(
            video,
            question=(
                "This is a finished vertical documentary short. Judge technical quality and packaging: "
                "sharpness, watermark or burned-in text problems, framing, whether the clips match the topic."
            ),
            frames=8,
        )
    except Exception as exc:
        print(f"[docgen] qc failed ({str(exc)[:160]})")
        return
    (out_dir / "qc.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    quality = result.get("quality") or {}
    print(f"[docgen] qc: score={quality.get('score')} issues={quality.get('issues')} -> {out_dir / 'qc.json'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="NOVA documentary generator (Gemini script + free stock)")
    parser.add_argument("--topic", default=None, help="documentary subject")
    parser.add_argument("--brief", default=None, help="reuse an existing brief.json")
    parser.add_argument("--angle", default="", help="extra angle for the script brain")
    parser.add_argument("--language", default="en", choices=sorted(LANGUAGE_SETUP),
                        help="en = English, hi = Hindi (Devanagari), hinglish = Roman Hinglish")
    parser.add_argument("--paragraphs", type=int, default=1, help="script paragraphs (~130 words each)")
    parser.add_argument("--aspect", default="9:16", choices=["9:16", "16:9", "1:1"])
    parser.add_argument("--source", default="pixabay", choices=["pixabay", "coverr", "pexels"])
    parser.add_argument("--voice", default=None, help="Edge TTS voice (default depends on --language)")
    parser.add_argument("--voice-rate", type=float, default=1.0)
    parser.add_argument("--font-name", default=None,
                        help="subtitle font inside MPT resource/fonts (auto per language)")
    parser.add_argument("--subtitle-position", default="bottom",
                        choices=["top", "center", "bottom", "two_thirds_bottom", "custom"])
    parser.add_argument("--clip-duration", type=int, default=5, help="seconds per stock clip (integer)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--no-subtitle", dest="subtitle", action="store_false")
    parser.add_argument("--stop-at", default=None, choices=["script", "terms", "audio", "subtitle", "materials", "video"])
    parser.add_argument("--out", default=None, help="output folder (default pipeline/docs/<slug>)")
    parser.add_argument("--qc", action="store_true", help="run NVIDIA VL analysis on the final video -> qc.json")
    args = parser.parse_args()

    setup = LANGUAGE_SETUP[args.language]
    args.voice = args.voice or setup["voice"]
    args.font_name = args.font_name if args.font_name is not None else setup["font"]

    if not args.topic and not args.brief:
        parser.error("--topic or --brief is required")
    if not MPT_CLI.exists():
        print(f"[docgen] MoneyPrinterTurbo not found: {MPT_CLI}", file=sys.stderr)
        return 2

    if args.brief:
        brief = load_brief(Path(args.brief))
        slug = slugify(brief.get("topic") or args.topic or "doc")
    else:
        brief = make_brief(args.topic, args.paragraphs, args.language, args.angle)
        slug = slugify(f"{args.topic} {args.language}")

    out_dir = Path(args.out) if args.out else OUT_ROOT / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "brief.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")

    cmd = build_command(args, brief)
    log_path = out_dir / "run.log"
    print("[docgen] " + " ".join(cmd[1:5]) + " ...")
    started = time.time()
    code, output = run(cmd, log_path)
    elapsed = round(time.time() - started, 1)

    task_id = parse_task_id(output)
    meta: dict = {
        "topic": brief.get("topic"),
        "slug": slug,
        "title": brief.get("title"),
        "hook": brief.get("hook"),
        "description": brief.get("description"),
        "tags": brief.get("tags"),
        "script_provider": brief.get("provider"),
        "script_model": brief.get("model"),
        "language": args.language,
        "language_label": brief.get("language"),
        "aspect": args.aspect,
        "source": args.source,
        "voice": args.voice,
        "font_name": args.font_name,
        "stop_at": args.stop_at,
        "exit_code": code,
        "elapsed_sec": elapsed,
        "engine": "MoneyPrinterTurbo + Gemini script + Edge TTS voice + Pixabay clips",
        "log": str(log_path),
    }
    if task_id:
        meta.update(collect_outputs(task_id, out_dir))
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[docgen] exit={code} in {elapsed}s")
    if meta.get("final"):
        print(f"[docgen] final video: {meta['final']} ({meta.get('size_mb')} MB)")
        if args.qc:
            run_qc(Path(meta["final"]), out_dir)
    else:
        print(f"[docgen] no final video (stop_at={args.stop_at}); see {log_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
