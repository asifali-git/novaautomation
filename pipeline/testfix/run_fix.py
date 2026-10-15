"""Quick test: cartoon video with fit_segments + natural voice (no atempo crush)."""
import os, sys, time, json, subprocess

os.environ["FFMPEG_BIN"] = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"
os.environ["FFPROBE_BIN"] = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe"
os.environ["KOKORO_DIR"] = r"C:\Users\asu\Documents\nova automation\kokoro"

sys.path.insert(0, r"C:\Users\asu\Documents\nova automation\pipeline")

import smart, voice, apply as ap
import main as mn

CLIP = r"C:\Users\asu\Desktop\Ai Cartoon Video. ai cartoon video. PapadJalebi vala(720P_HD).mp4"
WORK = r"C:\Users\asu\Documents\nova automation\pipeline\testfix"
os.makedirs(WORK, exist_ok=True)

t0 = time.time()

# 1. Smart fallback plan
plan = smart.analyze_video(CLIP, title_hint="Funny cartoon jalebi shop")
print(f"[plan] {time.time()-t0:.1f}s | {len(plan['segments'])} segs, "
      f"audio={plan['audio_type']}, mode={plan['audio_mode']}")

for s in plan["segments"]:
    print(f"  seg{s['index']}: {s['start']:.2f}-{s['end']:.2f} "
          f"spd={s['speed']} zoom={s['zoom']['type']}{s['zoom']['amount']} "
          f"color={s['color']['preset']} narr='{s['narration'][:45]}' "
          f"cap='{s['caption']['text']}'")

# 2. Caption regions
try:
    plan["caption_regions"] = smart.detect_caption_regions(CLIP)
except Exception:
    plan["caption_regions"] = []
print(f"[captions] {len(plan['caption_regions'])} regions: {plan['caption_regions']}")

# 3. Voice
wavs = {}
for seg in plan["segments"]:
    text = (seg.get("narration") or "").strip()
    if not text:
        continue
    out = os.path.join(WORK, f"narr_{seg['index']:02d}.wav")
    speed = float((seg.get("voice_style") or {}).get("speed", 1.0))
    voice.synthesize(text, out_path=out, speed=speed)
    wavs[seg["index"]] = out
    print(f"[voice] seg{seg['index']} spd={speed}")

# 4. Fit segments to voices (NEW)
t1 = time.time()
mn.fit_segments_to_voices(plan, wavs)
print(f"[fit] {time.time()-t1:.1f}s done")
for s in plan["segments"]:
    print(f"  seg{s['index']}: {s['start']:.2f}-{s['end']:.2f} dur={s['end']-s['start']:.2f}s")

# 5. Apply
t2 = time.time()
out = ap.apply_plan(CLIP, plan, workdir=WORK, voice_wavs=wavs, out_name="nova_fix.mp4")
print(f"[apply] {time.time()-t2:.1f}s -> {out} ({os.path.getsize(out)//1024} KB)")

# 6. Check durations
for i, seg in enumerate(plan["segments"]):
    wav = wavs.get(i)
    if wav:
        r = subprocess.run([os.environ["FFPROBE_BIN"], "-v", "error",
                            "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", wav],
                           capture_output=True, text=True)
        wav_dur = float(r.stdout.strip())
        out_dur = seg["end"] - seg["start"]
        tempo_needed = wav_dur / out_dur if out_dur > 0 else 999
        print(f"  seg{i}: wav={wav_dur:.2f}s out={out_dur:.2f}s tempo_needed={tempo_needed:.2f} "
              f"{'OK' if tempo_needed <= 1.15 else 'STILL TOO LONG'}")

total = time.time() - t0
print(f"\n[TOTAL] {total:.1f}s | output: {out}")
# Copy to Desktop
import shutil
dest = r"C:\Users\asu\Desktop\nova_fix.mp4"
shutil.copyfile(out, dest)
print(f"Copied to {dest}")
