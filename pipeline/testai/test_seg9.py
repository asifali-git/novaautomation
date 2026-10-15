import subprocess
FF = r'C:\Users\asu\AppData\Local\Temp\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe'
src = r'C:\Users\asu\Desktop\nex_output.mp4'
vf = 'scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=30,unsharp=5:5:2.55:5:5:0,eq=saturation=1.18:contrast=1.06,vflip,hflip,format=yuv420p'
# Use apad=whole_dur to pad only to needed length
cmd = [FF, '-y', '-ss', '29.350', '-t', '1.860', '-i', src,
       '-filter_complex', f'[0:v]{vf}[v];[0:a]aresample=44100,apad=whole_dur=2.1,atempo=1.1000[a]',
       '-map', '[v]', '-map', '[a]',
       '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '21',
       '-c:a', 'aac', '-b:a', '128k', '-map_metadata', '-1', '-avoid_negative_ts', 'make_zero',
       r'C:\Users\asu\Documents\nova automation\pipeline\testai\seg9_test.mp4']
r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
print('returncode:', r.returncode)
if r.returncode != 0:
    for line in r.stderr.split('\n'):
        if 'error' in line.lower() or 'Invalid' in line:
            print(line)
else:
    print('SUCCESS')
