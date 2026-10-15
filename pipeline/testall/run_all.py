import os, sys, time, shutil, subprocess
PIPE = r"C:\Users\asu\Documents\nova automation\pipeline"
sys.path.insert(0, PIPE)
os.environ.setdefault("FFMPEG_BIN", "ffmpeg")
os.environ.setdefault("FFPROBE_BIN", "ffprobe")
import analyze, smart, apply, voice

DESK = r"C:\Users\asu\Desktop"
WORK = os.path.join(PIPE, "testall")
os.makedirs(WORK, exist_ok=True)

VIDEOS = {
    "cartoon": os.path.join(DESK, "Ai Cartoon Video. ai cartoon video. PapadJalebi vala(720P_HD).mp4"),
    "nex": os.path.join(DESK, "nex_output.mp4"),
}

def make_clip(src, name, secs=15):
    clip = os.path.join(WORK, f"{name}.mp4")
    if not os.path.exists(clip):
        subprocess.run(["ffmpeg","-y","-i",src,"-t",str(secs),
                        "-vf","scale=720:-2","-c:v","libx264","-preset","ultrafast","-crf","26",
                        "-c:a","aac","-ac","1","-ar","16000",clip], capture_output=True)
    return clip

def run_pipeline(clip, plan, tag, work):
    wavs = {}
    for seg in plan["segments"]:
        text = (seg.get("narration") or "").strip()
        if not text: continue
        out = os.path.join(work, f"narr_{seg['index']:02d}.wav")
        voice.synthesize(text, out_path=out, speed=seg.get("voice_style",{}).get("speed",1.0))
        wavs[seg["index"]] = out
    final = apply.apply_plan(clip, plan, workdir=work, voice_wavs=wavs, out_name=f"{tag}.mp4")
    dest = os.path.join(DESK, f"nova_{tag}.mp4")
    shutil.copy(final, dest)
    dur = smart.probe_duration(final)
    print(f"[DONE] {tag}: {dur:.1f}s {os.path.getsize(dest)//1024}KB -> {dest}")
    return dest

# ---- CARTOON ----
print("\n===== CARTOON =====")
clip = make_clip(VIDEOS["cartoon"], "cartoon")

# Fallback
print("\n--- cartoon FALLBACK ---")
w = os.path.join(WORK, "cartoon_fb"); os.makedirs(w, exist_ok=True)
plan = smart.analyze_video(clip, title_hint="Funny cartoon jalebi shop")
try: plan["caption_regions"] = smart.detect_caption_regions(clip)
except: plan["caption_regions"] = []
print(f"  plan: {len(plan['segments'])} segs audio={plan['audio_type']} mode={plan['audio_mode']}")
for s in plan["segments"][:3]:
    print(f"    narr='{s['narration'][:50]}'")
run_pipeline(clip, plan, "cartoon_fallback", w)

# Nemotron
print("\n--- cartoon NEMOTRON ---")
w = os.path.join(WORK, "cartoon_nm"); os.makedirs(w, exist_ok=True)
try:
    plan = analyze.analyze_video(clip, title_hint="Funny cartoon jalebi shop")
    try: plan["caption_regions"] = smart.detect_caption_regions(clip)
    except: plan["caption_regions"] = []
    print(f"  plan: {len(plan['segments'])} segs audio={plan['audio_type']} mode={plan['audio_mode']}")
    for s in plan["segments"][:3]:
        print(f"    narr='{s['narration'][:50]}'")
    run_pipeline(clip, plan, "cartoon_nemotron", w)
except Exception as e:
    print(f"  NEMOTRON FAILED: {e}")

# ---- NEX_OUTPUT ----
print("\n===== NEX_OUTPUT =====")
clip2 = make_clip(VIDEOS["nex"], "nex")

# Fallback
print("\n--- nex FALLBACK ---")
w = os.path.join(WORK, "nex_fb"); os.makedirs(w, exist_ok=True)
plan = smart.analyze_video(clip2, title_hint="nex output video")
try: plan["caption_regions"] = smart.detect_caption_regions(clip2)
except: plan["caption_regions"] = []
print(f"  plan: {len(plan['segments'])} segs audio={plan['audio_type']} mode={plan['audio_mode']}")
for s in plan["segments"][:3]:
    print(f"    narr='{s['narration'][:50]}'")
run_pipeline(clip2, plan, "nex_fallback", w)

# Nemotron
print("\n--- nex NEMOTRON ---")
w = os.path.join(WORK, "nex_nm"); os.makedirs(w, exist_ok=True)
try:
    plan = analyze.analyze_video(clip2, title_hint="nex output video")
    try: plan["caption_regions"] = smart.detect_caption_regions(clip2)
    except: plan["caption_regions"] = []
    print(f"  plan: {len(plan['segments'])} segs audio={plan['audio_type']} mode={plan['audio_mode']}")
    for s in plan["segments"][:3]:
        print(f"    narr='{s['narration'][:50]}'")
    run_pipeline(clip2, plan, "nex_nemotron", w)
except Exception as e:
    print(f"  NEMOTRON FAILED: {e}")

print("\n===== ALL DONE =====")
