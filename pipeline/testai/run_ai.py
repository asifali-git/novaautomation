"""AI (Nemotron) path test: analyze -> voiceover -> fit -> apply. Cartoon video."""
import os, sys, time, subprocess, shutil

os.environ["FFMPEG_BIN"] = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"
os.environ["FFPROBE_BIN"] = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe"
os.environ["KOKORO_DIR"] = r"C:\Users\asu\Documents\nova automation\kokoro"
with open(r"C:\Users\asu\Documents\nova automation\nvidia api key.txt") as f:
    os.environ["NVIDIA_API_KEY"] = f.read().strip()

sys.path.insert(0, r"C:\Users\asu\Documents\nova automation\pipeline")

import analyze as an
import smart, voice, apply as ap
import main as mn

CLIP = r"C:\Users\asu\Desktop\Ai Cartoon Video. ai cartoon video. PapadJalebi vala(720P_HD).mp4"
WORK = r"C:\Users\asu\Documents\nova automation\pipeline\testai"
os.makedirs(WORK, exist_ok=True)

t0 = time.time()

# 1. Nemotron analyze
print("[1] Nemotron analyze...")
plan = an.analyze_video(CLIP, title_hint="Funny cartoon jalebi shop")
print(f"[plan] {time.time()-t0:.1f}s | {len(plan['segments'])} segs, "
      f"style={plan.get('style')}, audio={plan.get('audio_type')}, mode={plan.get('audio_mode')}")

for s in plan["segments"]:
    c = s.get("caption") or {}
    print(f"  seg{s['index']}: {s['start']:.2f}-{s['end']:.2f} spd={s['speed']} "
          f"zoom={s['zoom']['type']}{s['zoom']['amount']} color={s['color']['preset']} "
          f"narr='{(s.get('narration') or '')[:40]}' cap='{c.get('text','')}'")

# 2. Caption regions
try:
    plan["caption_regions"] = smart.detect_caption_regions(CLIP)
except Exception:
    plan["caption_regions"] = []
print(f"[captions] {len(plan['caption_regions'])} regions")

# 3. Voiceover
t1 = time.time()
wavs = {}
for seg in plan["segments"]:
    text = (seg.get("narration") or "").strip()
    if not text:
        continue
    out = os.path.join(WORK, f"narr_{seg['index']:02d}.wav")
    speed = float((seg.get("voice_style") or {}).get("speed", 1.0))
    voice.synthesize(text, out_path=out, speed=speed)
    wavs[seg["index"]] = out
    print(f"[voice] seg{seg['index']} spd={speed}: {text[:50]}")
print(f"[voice] {time.time()-t1:.1f}s, {len(wavs)} wavs")

# 4. Fit segments to voices
if wavs:
    mn.fit_segments_to_voices(plan, wavs)
    print("[fit] done")
    for s in plan["segments"]:
        print(f"  seg{s['index']}: {s['start']:.2f}-{s['end']:.2f} dur={s['end']-s['start']:.2f}s")

# 5. Apply
t2 = time.time()
out = ap.apply_plan(CLIP, plan, workdir=WORK, voice_wavs=wavs, out_name="nova_ai.mp4")
print(f"[apply] {time.time()-t2:.1f}s -> {out} ({os.path.getsize(out)//1024} KB)")

# 6. Verify
ffprobe = os.environ["FFPROBE_BIN"]
r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", out], capture_output=True, text=True)
print(f"[duration] {r.stdout.strip()}s")

r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a",
                    "-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1", out],
                   capture_output=True, text=True)
print(f"[audio] {r.stdout.strip()}")

dest = r"C:\Users\asu\Desktop\nova_ai.mp4"
shutil.copyfile(out, dest)
print(f"\nCopied to {dest}")
print(f"[TOTAL] {time.time()-t0:.1f}s")
