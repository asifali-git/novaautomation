import os, sys, time
PIPE = r"C:\Users\asu\Documents\nova automation\pipeline"
sys.path.insert(0, PIPE)
os.chdir(PIPE)
FFBIN = r"C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin"
os.environ["FFMPEG_BIN"] = os.path.join(FFBIN, "ffmpeg.exe")
os.environ["FFPROBE_BIN"] = os.path.join(FFBIN, "ffprobe.exe")

import apply as ap

src = os.path.join(PIPE, "testfb2", "src.mp4")
seg = {"index": 0, "start": 2.0, "end": 5.0, "speed": 1.10,
       "crop": {"top": 0.08, "bottom": 0.06, "left": 0.04, "right": 0.04},
       "zoom": {"type": "none", "amount": 1.0},
       "sharpness": 0.18,
       "color": {"preset": "cool", "brightness": 0.0, "contrast": 1.06, "saturation": 1.12}}
out = os.path.join(PIPE, "testfb2", "t_mir.mp4")
t0 = time.time()
ap.render_segment(src, seg, out, has_audio=True,
                  caption_crop_bottom=0.139, mirror=True)
print(f"rendered in {time.time()-t0:.1f}s")

# measure stream durations
import subprocess
for stream in ("v", "a"):
    r = subprocess.run([os.environ["FFPROBE_BIN"], "-v", "error",
                        "-select_streams", stream,
                        "-show_entries", "stream=duration",
                        "-of", "csv=p=0", out], capture_output=True, text=True)
    print(f"{stream} duration: {r.stdout.strip()}")
r = subprocess.run([os.environ["FFPROBE_BIN"], "-v", "error",
                    "-show_entries", "stream=width,height",
                    "-select_streams", "v", "-of", "csv=p=0", out],
                   capture_output=True, text=True)
print(f"dimensions: {r.stdout.strip()}")
