"""Quick test: fallback with fixed crop/speed/zoom (no moody/vintage)."""
import os, sys, time, subprocess, shutil

os.environ["FFMPEG_BIN"] = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"
os.environ["FFPROBE_BIN"] = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe"

sys.path.insert(0, r"C:\Users\asu\Documents\nova automation\pipeline")

import smart, apply as ap

CLIP = r"C:\Users\asu\Desktop\Ai Cartoon Video. ai cartoon video. PapadJalebi vala(720P_HD).mp4"
WORK = r"C:\Users\asu\Documents\nova automation\pipeline\testfix"
os.makedirs(WORK, exist_ok=True)

t0 = time.time()
plan = smart.analyze_video(CLIP)
plan["caption_regions"] = []
print(f"[plan] {len(plan['segments'])} segs, audio_mode={plan['audio_mode']}, vol={plan['audio_volume']}")
for s in plan["segments"]:
    print(f"  seg{s['index']}: spd={s['speed']} zoom={s['zoom']['type']}{s['zoom']['amount']} "
          f"crop_t={s['crop']['top']} crop_b={s['crop']['bottom']} color={s['color']['preset']}")

t1 = time.time()
out = ap.apply_plan(CLIP, plan, workdir=WORK, voice_wavs=None, out_name="nova_fb3.mp4")
print(f"[apply] {time.time()-t1:.1f}s -> {out} ({os.path.getsize(out)//1024} KB)")

# Verify output duration + audio
ffprobe = os.environ["FFPROBE_BIN"]
r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", out], capture_output=True, text=True)
print(f"[duration] {r.stdout.strip()}s")

r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a",
                    "-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1", out],
                   capture_output=True, text=True)
print(f"[audio] {r.stdout.strip()}")

dest = r"C:\Users\asu\Desktop\nova_fb3.mp4"
shutil.copyfile(out, dest)
print(f"Copied to {dest}")
print(f"[TOTAL] {time.time()-t0:.1f}s")
