import sys, os
os.environ['FFMPEG_BIN'] = r'C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe'
os.environ['FFPROBE_BIN'] = r'C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe'
sys.path.insert(0, r'C:\Users\asu\Documents\nova automation\pipeline')
import smart

for p in [r'C:\Users\asu\Documents\nova automation\pipeline\testfb2\src.mp4',
          r'C:\Users\asu\Desktop\nex_output.mp4']:
    print('===', os.path.basename(p))
    caps = smart.detect_caption_regions(p)
    print('regions:', len(caps))
    for c in caps:
        print(f"  {c['band']}: {c['top_frac']:.3f}-{c['bottom_frac']:.3f} "
              f"action={c['action']} conf={c['confidence']}")
        if c['action'] == 'crop':
            if c['band'] == 'bottom':
                print(f"    -> crop_bottom = {1 - c['top_frac']:.3f}")
            if c['band'] == 'top':
                print(f"    -> crop_top = {c['bottom_frac']:.3f}")
