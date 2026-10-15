"""Test: fallback with metadata strip + verify no metadata in output."""
import os, sys, time, subprocess, shutil

os.environ["FFMPEG_BIN"] = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"
os.environ["FFPROBE_BIN"] = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe"

sys.path.insert(0, r"C:\Users\asu\Documents\nova automation\pipeline")

import smart, apply as ap

CLIP = r"C:\Users\asu\Desktop\Ai Cartoon Video. ai cartoon video. PapadJalebi vala(720P_HD).mp4"
WORK = r"C:\Users\asu\Documents\nova automation\pipeline\testfix"
os.makedirs(WORK, exist_ok=True)

# Check source metadata first
ffprobe = os.environ["FFPROBE_BIN"]
print("[SOURCE metadata]")
r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                    "-show_format", "-show_streams", CLIP], capture_output=True, text=True)
import json
meta = json.loads(r.stdout)
print(f"  tags: {meta.get('format', {}).get('tags', {})}")
for s in meta.get('streams', []):
    if s.get('tags'):
        print(f"  stream {s.get('index')}: {s['tags']}")

t0 = time.time()
plan = smart.analyze_video(CLIP)
plan["caption_regions"] = []
print(f"\n[plan] {len(plan['segments'])} segs")

out = ap.apply_plan(CLIP, plan, workdir=WORK, voice_wavs=None, out_name="nova_fb4.mp4")
print(f"[apply] {time.time()-t0:.1f}s -> {out} ({os.path.getsize(out)//1024} KB)")

# Check OUTPUT metadata (should be empty/clean)
print("\n[OUTPUT metadata]")
r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                    "-show_format", "-show_streams", out], capture_output=True, text=True)
meta = json.loads(r.stdout)
fmt_tags = meta.get('format', {}).get('tags', {})
print(f"  format tags: {fmt_tags if fmt_tags else '(EMPTY - CLEAN)'}")
for s in meta.get('streams', []):
    tags = s.get('tags', {})
    # encoder/handler_name are ffmpeg defaults, but source-specific tags should be gone
    print(f"  stream {s.get('index')} tags: {tags if tags else '(none)'}")

dest = r"C:\Users\asu\Desktop\nova_fb4.mp4"
shutil.copyfile(out, dest)
print(f"\nCopied to {dest}")
