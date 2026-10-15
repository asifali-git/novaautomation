"""Quick fallback test: visual-only, audio preserved (not muted)."""
import os, sys, time

os.environ["FFMPEG_BIN"] = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"
os.environ["FFPROBE_BIN"] = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe"

sys.path.insert(0, r"C:\Users\asu\Documents\nova automation\pipeline")

import smart, apply as ap

CLIP = r"C:\Users\asu\Desktop\Ai Cartoon Video. ai cartoon video. PapadJalebi vala(720P_HD).mp4"
WORK = r"C:\Users\asu\Documents\nova automation\pipeline\testfix"
os.makedirs(WORK, exist_ok=True)

t0 = time.time()
plan = smart.analyze_video(CLIP, title_hint="test")
print(f"[plan] {time.time()-t0:.1f}s | {len(plan['segments'])} segs")
print(f"  audio_mode={plan['audio_mode']}, audio_volume={plan['audio_volume']}")
for s in plan["segments"][:3]:
    print(f"  seg{s['index']}: spd={s['speed']} zoom={s['zoom']['type']}{s['zoom']['amount']} "
          f"color={s['color']['preset']} narr='{s['narration']}' cap='{s['caption']['text']}'")

# No captions detection for pure fallback test
plan["caption_regions"] = []

# Apply (no voice_wavs -> mix_narration skipped, audio preserved)
t1 = time.time()
out = ap.apply_plan(CLIP, plan, workdir=WORK, voice_wavs=None, out_name="nova_fb2.mp4")
print(f"[apply] {time.time()-t1:.1f}s -> {out} ({os.path.getsize(out)//1024} KB)")

# Verify audio exists and volume
import subprocess
ffprobe = os.environ["FFPROBE_BIN"]
r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a",
                    "-show_entries", "stream=codec_name,volume",
                    "-of", "default=noprint_wrappers=1", out],
                   capture_output=True, text=True)
print(f"[audio] {r.stdout.strip()}")

import shutil
dest = r"C:\Users\asu\Desktop\nova_fb2.mp4"
shutil.copyfile(out, dest)
print(f"Copied to {dest}")
print(f"[TOTAL] {time.time()-t0:.1f}s")
