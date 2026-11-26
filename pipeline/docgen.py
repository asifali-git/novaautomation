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

try:
    import imageio_ffmpeg
except Exception:  # ffmpeg comes from PATH then
    imageio_ffmpeg = None

DEVANAGARI_FONT_URL = (
    "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/"
    "NotoSansDevanagari/NotoSansDevanagari-Bold.ttf"
)

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


def build_command(args: argparse.Namespace, brief: dict, custom_audio: Path | None = None,
                  local_materials: list[Path] | None = None) -> list[str]:
    setup = LANGUAGE_SETUP[args.language]
    if local_materials:
        # clipvet already verified and cropped these, so MPT only concatenates.
        source = "local"
        materials = ",".join(str(p) for p in local_materials)
    else:
        source = args.source
        materials = ""
    cmd = [
        sys.executable,
        str(MPT_CLI),
        "--video-subject", brief.get("title") or args.topic,
        "--video-script", brief["script"],
        "--video-language", setup["mpt_lang"],
        "--video-source", source,
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
    if materials:
        cmd += ["--video-materials", materials]
    else:
        terms = ",".join(brains.shot_terms(brief))
        if terms:
            cmd += ["--video-terms", terms]
        if args.match_script_order:
            # Round-robin the candidates per term so an early term cannot eat the
            # whole timeline; this is what keeps clips near their sentence.
            cmd.append("--match-materials-to-script")
    if args.font_name:
        cmd += ["--font-name", args.font_name]
    if custom_audio:
        # One narration track for the whole video; subtitles come from our own SRT.
        cmd += ["--custom-audio-file", str(custom_audio), "--no-subtitle-enabled"]
    else:
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


def ffmpeg_exe() -> str:
    if imageio_ffmpeg is not None:
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    found = shutil.which("ffmpeg")
    if not found:
        raise SystemExit("[docgen] ffmpeg not available (pip install imageio-ffmpeg)")
    return found


def ensure_font(work_dir: Path, language: str) -> Path | None:
    """Put a subtitle font next to the video; Hindi needs a Devanagari face."""
    work_dir.mkdir(parents=True, exist_ok=True)
    font_dir = work_dir / "fonts"
    if language != "hi":
        return None
    font_dir.mkdir(parents=True, exist_ok=True)
    target = font_dir / "NotoSansDevanagari-Bold.ttf"
    if target.exists() and target.stat().st_size > 50000:
        return target
    try:
        import urllib.request

        urllib.request.urlretrieve(DEVANAGARI_FONT_URL, target)
        return target
    except Exception as exc:  # subtitle rendering still works, glyphs may differ
        print(f"[docgen] Devanagari font download failed ({exc}); using system fonts")
        return None


def burn_subtitles(video: Path, srt: Path, out: Path, font_dir: Path | None) -> Path:
    """Second pass with libass so subtitle shaping (Hindi) is correct."""
    work = video.parent
    srt_local = work / srt.name
    if srt.resolve() != srt_local.resolve():
        shutil.copy2(srt, srt_local)
    vf = f"subtitles={srt_local.name}"
    if font_dir is not None and font_dir.exists():
        vf += f":fontsdir={font_dir.name}"
    cmd = [
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", video.name,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy",
        out.name,
    ]
    print(f"[docgen] burning subtitles -> {out.name}")
    subprocess.run(cmd, cwd=str(work), check=True, timeout=3600)
    return out


def vet_clips(brief: dict, out_dir: Path, args: argparse.Namespace,
              narration_seconds: float = 0.0) -> tuple[list[Path] | None, dict]:
    """Option B: download candidates, judge each frame with NVIDIA VL, keep matches.

    A clip that the vision model rejects is exactly the mismatch QC catches too
    late ("strawberry drink under a line about coffee"). Doing it here means the
    wrong clip never reaches the timeline. Returns (paths, report).
    """
    shots = brief.get("shots") or []
    if not shots:
        return None, {"reason": "brief has no shots"}
    try:
        from pipeline import clipvet
    except Exception as exc:  # module missing -> plain stock path still works
        print(f"[docgen] clipvet unavailable ({str(exc)[:120]}); using stock terms")
        return None, {"reason": f"clipvet import failed: {exc}"}

    # The timeline has to cover the narration, so ask for slightly more than
    # measured; a slow TTS then cannot leave black frames at the end.
    needed = float(narration_seconds) if narration_seconds else 0.0
    try:
        result = clipvet.vet_shots(
            shots,
            out_dir,
            aspect=args.aspect,
            clip_duration=args.clip_duration,
            candidates_per_shot=args.candidates,
            needed_seconds=needed,
        )
    except Exception as exc:
        print(f"[docgen] clipvet failed ({str(exc)[:160]}); using stock terms")
        return None, {"reason": f"clipvet failed: {exc}"}

    (out_dir / "clipvet.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    clips = [Path(p) for p in result.get("clips") or [] if Path(p).exists()]
    kept = sum(1 for r in result.get("report", []) if r.get("match"))
    judged = sum(1 for r in result.get("report", []) if r.get("match") is not None)
    print(f"[docgen] vet: {kept}/{judged} judged clips matched, {result.get('total_seconds')}s total")
    return (clips or None), result


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
    parser.add_argument("--vet", action="store_true",
                        help="download + NVIDIA-VL-verify every clip before rendering (Option B)")
    parser.add_argument("--no-vet", dest="vet", action="store_false")
    parser.set_defaults(vet=True)
    parser.add_argument("--match-script-order", action="store_true",
                        help="keep MPT materials in script order (Option A, on when not vetting)")
    parser.add_argument("--candidates", type=int, default=3,
                        help="clipvet candidates to judge per shot")
    parser.add_argument("--voice", default=None, help="Edge TTS voice (default depends on --language)")
    parser.add_argument("--voice-engine", default="kokoro", choices=["kokoro", "edge"],
                        help="kokoro = local free TTS (one track, works on GitHub runners); edge = Edge TTS")
    parser.add_argument("--female", action="store_true", help="female narrator voice")
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

    custom_audio: Path | None = None
    srt_file: Path | None = None
    voice_info: dict = {}
    if args.voice_engine == "kokoro" and args.stop_at != "script":
        from pipeline import kokoro_voice

        print(f"[docgen] narration: kokoro ({args.language}, voice={args.voice})")
        voice_info = kokoro_voice.synthesize(
            brief["script"],
            out_dir / "voice.wav",
            language=args.language,
            female=args.female,
            srt_path=out_dir / "subtitles.srt",
        )
        custom_audio = Path(voice_info["wav"])
        srt_file = Path(voice_info["srt"])
        print(
            f"[docgen] narration ready: {voice_info['seconds']}s / {voice_info['sentences']} sentences "
            f"/ voice {voice_info['voice']} ({voice_info['elapsed_sec']}s)"
        )

    local_materials: list[Path] | None = None
    vet_info: dict = {}
    if args.vet and args.stop_at not in {"script", "terms", "audio"}:
        local_materials, vet_info = vet_clips(
            brief, out_dir, args, narration_seconds=float(voice_info.get("seconds") or 0.0)
        )
        if local_materials:
            print(f"[docgen] {len(local_materials)} clips verified -> local source")
        else:
            print("[docgen] vet found nothing usable, falling back to Pixabay search terms")

    cmd = build_command(args, brief, custom_audio=custom_audio, local_materials=local_materials)
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
        "voice": voice_info.get("voice") or args.voice,
        "voice_engine": args.voice_engine,
        "narration_seconds": voice_info.get("seconds"),
        "subtitle_file": str(srt_file) if srt_file else None,
        "font_name": args.font_name,
        "stop_at": args.stop_at,
        "exit_code": code,
        "elapsed_sec": elapsed,
        "engine": (
            "MoneyPrinterTurbo + Gemini script + kokoro narration + Pixabay clips"
            if args.voice_engine == "kokoro"
            else "MoneyPrinterTurbo + Gemini script + Edge TTS voice + Pixabay clips"
        ),
        "log": str(log_path),
    }
    if task_id:
        meta.update(collect_outputs(task_id, out_dir))

    final = meta.get("final")
    if final and srt_file and srt_file.exists() and args.subtitle:
        raw = Path(final)
        plain = raw.with_name("video-raw.mp4")
        try:
            font_dir = ensure_font(out_dir, args.language)
            shutil.move(str(raw), str(plain))
            burn_subtitles(plain, srt_file, raw, font_dir)
            meta["final"] = str(raw)
            meta["raw_video"] = str(plain)
            meta["size_mb"] = round(raw.stat().st_size / (1024 * 1024), 2)
            meta["subtitle_font_dir"] = str(font_dir) if font_dir else None
        except Exception as exc:  # keep the un-burned video
            print(f"[docgen] subtitle burn failed ({str(exc)[:200]}); keeping plain video")
            meta["final"] = str(plain) if plain.exists() else str(raw)

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
