import os, sys, time, shutil, subprocess
PIPE = r"C:\Users\asu\Documents\nova automation\pipeline"
sys.path.insert(0, PIPE)
os.environ.setdefault("FFMPEG_BIN", "ffmpeg")
os.environ.setdefault("FFPROBE_BIN", "ffprobe")
import smart, apply, voice

WORK = r"C:\Users\asu\Documents\nova automation\pipeline\testfb"
os.makedirs(WORK, exist_ok=True)
CLIP = os.path.join(WORK, "cartoon15.mp4")
SRC = r"C:\Users\asu\Desktop\nova test\Ai Cartoon Video. ai cartoon video. PapadJalebi vala(720P_HD).mp4"
if not os.path.exists(CLIP):
    subprocess.run(["ffmpeg","-y","-i",SRC,"-t","15","-c:v","libx264","-preset","ultrafast","-crf","26","-c:a","aac",CLIP],capture_output=True)

# 1. Caption detection
t0 = time.time()
caps = smart.detect_caption_regions(CLIP)
print(f"[captions] {time.time()-t0:.1f}s -> {caps}")

# 2. Fallback plan
t1 = time.time()
plan = smart.analyze_video(CLIP, title_hint="Funny cartoon jalebi")
print(f"[plan] {time.time()-t1:.1f}s segs={len(plan['segments'])} audio={plan['audio_type']} mode={plan['audio_mode']}")
for s in plan["segments"][:4]:
    print(f"  seg{s['index']} spd={s['speed']} zoom={s['zoom']['type']}{s['zoom']['amount']} color={s['color']['preset']} narr='{s['narration'][:40]}'")
plan["caption_regions"] = caps

# 3. Kokoro voices
t2 = time.time(); wavs = {}
for seg in plan["segments"]:
    text = (seg.get("narration") or "").strip()
    if not text: continue
    out = os.path.join(WORK, f"narr_{seg['index']:02d}.wav")
    voice.synthesize(text, out_path=out, speed=seg.get("voice_style",{}).get("speed",1.0))
    wavs[seg["index"]] = out
print(f"[voices] {time.time()-t2:.1f}s count={len(wavs)}")

# 4. Apply
t3 = time.time()
final = apply.apply_plan(CLIP, plan, workdir=WORK, voice_wavs=wavs, out_name="final.mp4")
sz = os.path.getsize(final)
print(f"[apply] {time.time()-t3:.1f}s out={sz}B")

# 5. Verify
dur = smart.probe_duration(final)
print(f"[verify] duration={dur:.2f}s size={sz//1024}KB")
dest = os.path.join(r"C:\Users\asu\Desktop", "nova_fallback.mp4")
shutil.copy(final, dest)
print("SAVED_TO", dest)
