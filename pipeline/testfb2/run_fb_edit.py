"""Test: backend download.py se Facebook video download + full AI pipeline."""
import os, sys, time, json, shutil

PIPE = r"C:\Users\asu\Documents\nova automation\pipeline"
sys.path.insert(0, PIPE)
os.chdir(PIPE)

# yt-dlp needs ffmpeg in PATH for merging video+audio streams.
FFBIN = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin"
os.environ["PATH"] = FFBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["FFMPEG_BIN"] = os.path.join(FFBIN, "ffmpeg.exe")
os.environ["FFPROBE_BIN"] = os.path.join(FFBIN, "ffprobe.exe")
os.environ["KOKORO_DIR"] = r"C:\Users\asu\Documents\nova automation\kokoro"
with open(r"C:\Users\asu\Documents\nova automation\nvidia api key.txt") as f:
    os.environ["NVIDIA_API_KEY"] = f.read().strip()

import download as dl
import analyze as an
import smart, voice, apply as ap
import main as mn

URL = "https://www.facebook.com/share/r/1BeDGdwQnr/"
WORK = os.path.join(PIPE, "testfb2")
os.makedirs(WORK, exist_ok=True)

t0 = time.time()

# [1] Backend download
existing = os.path.join(WORK, "src.mp4")
if os.path.exists(existing) and os.path.getsize(existing) > 100000:
    src = existing
    title = "Facebook video"
    dur = an.probe_duration(src) or 0
    print(f"[1] Download SKIPPED (already have src.mp4: {os.path.getsize(src)//1024}KB, {dur:.1f}s)")
else:
    print(f"[1] Download via backend yt-dlp: {URL}")
    src, info = dl.download(URL, out_dir=WORK, max_secs=None)
    title = info.get("title", "Facebook video")
    dur = an.probe_duration(src) or 0
    print(f"    -> {os.path.basename(src)}, {os.path.getsize(src)//1024}KB, {dur:.1f}s")
    print(f"    title: {title.encode('ascii', 'replace').decode('ascii')[:80]}")

# [2] Nemotron analyze (full AI path)
print("[2] Nemotron analyze...")
plan = an.analyze_video(src, title_hint=title)
segs = plan.get("segments", [])
print(f"    {len(segs)} segs, style={plan.get('style')}, audio={plan.get('audio_type')}, "
      f"mode={plan.get('audio_mode')}, voice_gain={plan.get('global',{}).get('voice_gain')}")
print(f"    video_summary: {plan.get('video_summary','')[:80].encode('ascii','replace').decode('ascii')}")

# [3] Voiceover (AI path only)
print("[3] Kokoro voiceover...")
t1 = time.time()
wavs = mn.make_voices(plan, WORK, None, used_ai=True)
print(f"    {len(wavs)} wavs, {time.time()-t1:.1f}s")

# [3b] Fit segments to voices
if wavs:
    mn.fit_segments_to_voices(plan, wavs, src_duration=dur)
    total = sum(s["end"]-s["start"] for s in plan["segments"])
    print(f"    timeline: {total:.2f}s (source {dur:.2f}s)")

# [3c] Detect burned-in captions/watermarks for precise crop
plan["caption_regions"] = smart.detect_caption_regions(src)
print(f"[3c] caption_regions: {len(plan['caption_regions'])}")
for r in plan["caption_regions"]:
    print(f"     {r['band']}: {r['top_frac']:.3f}-{r['bottom_frac']:.3f} "
          f"action={r['action']} conf={r['confidence']}")

# [4] Apply edits
print("[4] ffmpeg apply...")
t1 = time.time()
out = ap.apply_plan(src, plan, workdir=WORK, voice_wavs=wavs, out_name="nova_fb_edit.mp4")
print(f"    -> {out}, {os.path.getsize(out)//1024}KB, {time.time()-t1:.1f}s")

# [5] Duration check
final_dur = an.probe_duration(out) or 0
print(f"[5] final duration: {final_dur:.2f}s (source {dur:.2f}s)")
print(f"[TOTAL] {time.time()-t0:.1f}s")

# Copy to Desktop with UNIQUE name (timestamp + style) so runs never overwrite.
desktop = r"C:\Users\asu\Desktop"
stamp = time.strftime("%H%M%S")
style = str(plan.get("style", "edit")).replace(" ", "")[:12]
out_name2 = f"nova_fb_{style}_{stamp}.mp4"
shutil.copy2(out, os.path.join(desktop, out_name2))
print(f"Copied to {desktop}\\{out_name2}")

# Print plan summary
print("\n--- PLAN ---")
for s in segs:
    narr = (s.get('narration') or '')[:40].encode('ascii','replace').decode('ascii')
    c = s.get('caption') or ''
    if isinstance(c, dict):
        c = c.get('text', '')
    cap = str(c)[:25].encode('ascii', 'replace').decode('ascii')
    print(f"  seg{s.get('index')}: {s['start']:.1f}-{s['end']:.1f} "
          f"spd={s.get('speed')} color={s.get('color',{}).get('preset')} "
          f"narr='{narr}' cap='{cap}'")
