"""AI (Nemotron) test on coconut video with fit cap fix."""
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

CLIP = r"C:\Users\asu\Desktop\nex_output.mp4"
WORK = r"C:\Users\asu\Documents\nova automation\pipeline\testai"
os.makedirs(WORK, exist_ok=True)

t0 = time.time()

# Probe source duration
src_dur = an.probe_duration(CLIP)
print(f"[source] {CLIP.split(chr(92))[-1]} duration={src_dur:.2f}s")

# 1. Nemotron analyze
print("\n[1] Nemotron analyze...")
plan = an.analyze_video(CLIP, title_hint="Coconut opening satisfying")
print(f"[plan] {time.time()-t0:.1f}s | {len(plan['segments'])} segs, "
      f"style={plan.get('style')}, audio={plan.get('audio_type')}, mode={plan.get('audio_mode')}")

for s in plan["segments"]:
    c = s.get("caption") or {}
    print(f"  seg{s['index']}: {s['start']:.2f}-{s['end']:.2f} spd={s['speed']} "
          f"zoom={s['zoom']['type']}{s['zoom']['amount']} color={s['color']['preset']} "
          f"narr='{(s.get('narration') or '')[:45]}' cap='{c.get('text','')}'")

# 2. Caption regions
try:
    plan["caption_regions"] = smart.detect_caption_regions(CLIP)
except Exception:
    plan["caption_regions"] = []
print(f"\n[captions] {len(plan['caption_regions'])} regions")

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
    print(f"[voice] seg{seg['index']}: {text[:50]}")
print(f"[voice] {time.time()-t1:.1f}s, {len(wavs)} wavs")

# 4. Fit segments (CAPPED at source duration)
if wavs:
    mn.fit_segments_to_voices(plan, wavs, src_duration=src_dur)
    print(f"\n[fit] done (capped at {src_dur:.2f}s)")
    total = 0
    for s in plan["segments"]:
        d = s["end"] - s["start"]
        total += d
        print(f"  seg{s['index']}: {s['start']:.2f}-{s['end']:.2f} dur={d:.2f}s")
    print(f"  total timeline: {total:.2f}s (source={src_dur:.2f}s)")

# 5. Apply
t2 = time.time()
out = ap.apply_plan(CLIP, plan, workdir=WORK, voice_wavs=wavs, out_name="nova_ai_coco.mp4")
print(f"\n[apply] {time.time()-t2:.1f}s -> {out} ({os.path.getsize(out)//1024} KB)")

# 6. Verify
ffprobe = os.environ["FFPROBE_BIN"]
r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", out], capture_output=True, text=True)
print(f"[duration] {r.stdout.strip()}s (source={src_dur:.2f}s)")

r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a",
                    "-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1", out],
                   capture_output=True, text=True)
print(f"[audio] {r.stdout.strip()}")

dest = r"C:\Users\asu\Desktop\nova_ai_coco.mp4"
shutil.copyfile(out, dest)
print(f"\nCopied to {dest}")
print(f"[TOTAL] {time.time()-t0:.1f}s")
