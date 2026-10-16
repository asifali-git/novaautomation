"""Execute an edit plan (from analyze.py) with ffmpeg.

Pipeline per plan:
  1. Render each segment (trim + speed + zoom + sharpness + color), keeping audio in sync (atempo).
  2. Concatenate segments into one timeline.
  3. Burn captions (drawtext) at each segment's output offset.
  4. Mix Kokoro narration wavs at their offsets (duck original audio if planned).
  5. Apply intro/ending effects.

Low-RAM: medium, 2 threads, bilinear scale — same levers as edit.py.
"""
import os
import subprocess
import json
import shlex
import analyze

FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
THREADS = os.getenv("FFMPEG_THREADS", "2")
FTHREADS = os.getenv("FFMPEG_FILTER_THREADS", "2")
CRF = os.getenv("FFMPEG_CRF", "18")
CRF_HIGH = os.getenv("FFMPEG_CRF_HIGH", "14")  # intermediate segments: near-lossless
W, H = 1080, 1920


def _audio_duration(path):
    """Probe audio duration in seconds via ffprobe."""
    try:
        cmd = [
            FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if proc.returncode == 0 and proc.stdout.strip():
            return float(proc.stdout.strip())
    except Exception:
        pass
    return 0.0

COLOR_PRESETS = {
    "warm": "colorbalance=rs=0.10:bs=-0.10,eq=saturation=1.10:contrast=1.05",
    "cool": "colorbalance=rs=-0.10:bs=0.15,eq=saturation=1.05",
    "vivid": "eq=saturation=1.18:contrast=1.06",
    "noir": "hue=s=0,eq=contrast=1.20:brightness=-0.02",
    "vintage": "colorbalance=rs=0.15:gs=0.05:bs=-0.10,eq=saturation=0.80:contrast=1.05,vignette=PI/4",
    "neon": "eq=saturation=1.22:contrast=1.08,colorbalance=bs=0.08:rs=0.03",
    "moody": "eq=brightness=-0.05:contrast=1.12:saturation=0.90,vignette=PI/5",
    "none": "",
}

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _font():
    env = os.getenv("CAPTION_FONT")
    if env and os.path.exists(env):
        return env
    for f in FONTS:
        if os.path.exists(f):
            return f
    return None


def _font_esc():
    """Font path escaped for a filtergraph option (escape ':' and "'")."""
    f = _font()
    if not f:
        return None
    return f.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + proc.stderr[-700:])
    return proc


def _thread_flags():
    return ["-threads", THREADS, "-filter_threads", FTHREADS, "-filter_complex_threads", FTHREADS]


def _speed_of(seg):
    try:
        s = float(seg.get("speed", 1.0))
    except Exception:
        s = 1.0
    return min(2.0, max(0.5, s))


def _seg_duration(seg):
    return max(0.05, (seg["end"] - seg["start"]) / _speed_of(seg))


def _delogo_chain(regions, sw=None, sh=None):
    """Build delogo filter chain for mid-band caption regions (action='delogo').
    regions: list of dicts with top_frac, bottom_frac (fractions of source H).
    sw/sh: ACTUAL source dimensions (delogo needs absolute pixels on the
    source frame — W/H constants are wrong for non-1080x1920 sources).
    Returns filter string like 'delogo=x=0:y=200:w=1080:h=150,delogo=...' or ''.
    """
    if not regions:
        return ""
    w = int(sw) if sw else W
    hh_src = int(sh) if sh else H
    chains = []
    for r in regions:
        top = float(r.get("top_frac", 0.0) or 0.0)
        bot = float(r.get("bottom_frac", 0.0) or 0.0)
        if bot <= top:
            continue
        x = 0
        y = int(round(top * hh_src))
        ww = w
        h = int(round((bot - top) * hh_src))
        if h > 0:
            chains.append(f"delogo=x={x}:y={y}:w={ww}:h={h}:show=0")
    return ",".join(chains)


def _video_chain(seg, caption_crop_top=0.0, caption_crop_bottom=0.0, delogo_filter="", mirror=False, gcolor=None):
    """Build the video filter chain (no audio) for one segment.
    caption_crop_top/bottom are fractions of source height to crop first (burned-in captions).
    delogo_filter: pre-built delogo filter chain string for mid-band captions.
    mirror: hflip (OFF — natural orientation requested).
    gcolor: one shared color grade for the whole video (overrides per-seg color).
    """
    parts = []
    # Mirror first (on source frame) — strongest cheap anti-fingerprint transform.
    if mirror:
        parts.append("hflip")
    # Delogo for mid-band captions FIRST (on source frame).
    if delogo_filter:
        parts.append(delogo_filter)
    # Crop burned-in caption bands (top/bottom) AFTER delogo, before scale/zoom.
    top = max(0.0, float(caption_crop_top or 0.0))
    bot = max(0.0, float(caption_crop_bottom or 0.0))
    # Also apply segment's own crop (reframe for transformation).
    seg_crop = seg.get("crop") or {}
    top += max(0.0, float(seg_crop.get("top", 0) or 0.0))
    bot += max(0.0, float(seg_crop.get("bottom", 0) or 0.0))
    left = max(0.0, float(seg_crop.get("left", 0) or 0.0))
    right = max(0.0, float(seg_crop.get("right", 0) or 0.0))
    if top > 0.001 or bot > 0.001 or left > 0.001 or right > 0.001:
        # crop=w:h:x:y — keep center after removing bands.
        cw = f"iw*(1-{left:.4f}-{right:.4f})"
        ch = f"ih*(1-{top:.4f}-{bot:.4f})"
        cx = f"iw*{left:.4f}"
        cy = f"ih*{top:.4f}"
        parts.append(f"crop={cw}:{ch}:{cx}:{cy}")

    speed = _speed_of(seg)
    if abs(speed - 1.0) > 0.01:
        parts.append(f"setpts=PTS/{speed:.4f}")

    zoom = seg.get("zoom") or {}
    ztype = zoom.get("type", "none")
    zamt = float(zoom.get("amount", 1.0))
    if ztype in ("in", "out") and zamt > 1.01:
        fps = 30
        # Faster zoom rate (0.005/frame) so short segments still show clear zoom.
        rate = 0.005
        zexpr = (
            f"min(zoom+{rate},{zamt:.3f})" if ztype == "in"
            else f"if(eq(on,1),{zamt:.3f},max(zoom-{rate},1.0))"
        )
        parts.append(
            f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
            f"crop={W*2}:{H*2},"
            f"zoompan=z='{zexpr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={fps}"
        )
    else:
        # No zoom: scale cropped video to FILL frame (no black bars).
        # lanczos = best quality downscale/upscale (vs bilinear).
        parts.append(f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}")

    sharp = float(seg.get("sharpness", 0) or 0)
    if sharp > 0.01:
        # Subtle (user: "sharpness thoda kam"): 0.10 -> ~0.16, 0.40 -> ~0.26.
        luma = round(0.12 + sharp * 0.35, 2)
        parts.append(f"unsharp=5:5:{luma}:5:5:0")

    color = gcolor if gcolor is not None else (seg.get("color") or {})
    preset = COLOR_PRESETS.get(color.get("preset", "none"), "")
    if preset:
        parts.append(preset)
    b = float(color.get("brightness", 0) or 0)
    c = float(color.get("contrast", 1.0) or 1.0)
    s = float(color.get("saturation", 1.0) or 1.0)
    if abs(b) > 0.001 or abs(c - 1) > 0.001 or abs(s - 1) > 0.001:
        parts.append(f"eq=brightness={b:.3f}:contrast={c:.3f}:saturation={s:.3f}")

    # NO noise filter — was degrading quality significantly.
    # Anti-fingerprint is already handled by: mirror + crop + speed + color + sharpness.

    parts.append("format=yuv420p")
    return ",".join(parts)


def render_segment(src, seg, out, has_audio=True, caption_crop_top=0.0, caption_crop_bottom=0.0, delogo_filter="", mirror=False, gcolor=None):
    speed = _speed_of(seg)
    dur = seg["end"] - seg["start"]
    vf = _video_chain(seg, caption_crop_top, caption_crop_bottom, delogo_filter, mirror=mirror, gcolor=gcolor)

    cmd = [FFMPEG, "-y", "-ss", f"{seg['start']:.3f}", "-t", f"{dur:.3f}", "-i", src]
    cmd += _thread_flags()

    if has_audio:
        # Audio anti-fingerprint: pitch-up 2.5% (asetrate trick, duration
        # restored via atempo) — breaks audio Content-ID style fingerprints.
        # Chain order: normalize rate -> relabel (pitch up, shorter) -> real
        # resample -> tempo back to original duration -> pad -> speed atempo.
        # apad BEFORE final atempo: source audio may end before video (last seg).
        # Video output = dur / speed (setpts), so pad_dur MUST equal dur —
        # otherwise audio is 'speed'x longer than video, leaving gaps in the
        # video track after concat => frozen frames while audio keeps playing.
        pad_dur = round(dur, 3)
        atempo = ("aresample=44100,asetrate=45203,aresample=44100,atempo=0.9756,"
                  f"apad=whole_dur={pad_dur},atempo={speed:.4f}")
        cmd += [
            "-filter_complex", f"[0:v]{vf}[v];[0:a]{atempo}[a]",
            "-map", "[v]", "-map", "[a]",
        ]
    else:
        cmd += ["-vf", vf, "-map", "0:v:0"]

    # Intermediate segments: near-lossless (CRF 14) so final re-encode after
    # concat+captions still looks great. Final output re-encodes at CRF 18.
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", CRF_HIGH]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-map_metadata", "-1", "-avoid_negative_ts", "make_zero", out]
    _run(cmd)
    return out


def concat_segments(seg_paths, out):
    listfile = out + ".txt"
    out_dir = os.path.dirname(os.path.abspath(out))
    with open(listfile, "w", encoding="utf-8") as f:
        for p in seg_paths:
            # ffmpeg concat resolves relative paths against the listfile's dir,
            # so use absolute paths to avoid manual_7/manual_7/seg_00.mp4.
            abs_p = os.path.abspath(p)
            f.write("file '" + abs_p.replace("'", "'\\''") + "'\n")
    # Stream copy first (fast). Then strip metadata via a second pass if needed.
    cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy",
           "-map_metadata", "-1", out]
    try:
        _run(cmd)
    except RuntimeError:
        # Re-encode fallback if stream copy fails on mismatched params.
        cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
               "-c:v", "libx264", "-preset", "medium", "-crf", CRF,
               "-c:a", "aac", "-b:a", "128k", "-map_metadata", "-1", out]
        _run(cmd)
    return out


def output_offsets(plan):
    """Compute each segment's start offset on the final timeline."""
    offsets = []
    t = 0.0
    for seg in plan["segments"]:
        offsets.append(t)
        t += _seg_duration(seg)
    return offsets, t


def apply_captions(src, plan, offsets, out):
    """Burn captions via drawtext using textfile= (avoids all escaping issues)."""
    font = _font_esc()
    if not font:
        print("[captions] NO FONT FOUND — skipping caption burn!", file=__import__('sys').stderr)
        # Try to find any TTF font as last resort
        import glob
        for pattern in ["/usr/share/fonts/**/*.ttf", "/usr/share/fonts/**/*.otf"]:
            found = glob.glob(pattern, recursive=True)
            if found:
                font = found[0].replace("\\", "/").replace(":", "\\:")
                print(f"[captions] fallback font: {found[0]}", file=__import__('sys').stderr)
                break
        if not font:
            return src
    else:
        print(f"[captions] using font: {font}", file=__import__('sys').stderr)
    work = os.path.dirname(out) or "."
    capdir = os.path.join(work, "captions")
    os.makedirs(capdir, exist_ok=True)

    chains = []
    segs = plan["segments"]
    cap_count = 0
    for i, (seg, off) in enumerate(zip(segs, offsets)):
        cap = seg.get("caption") or {}
        text = (cap.get("text") or "").strip()
        if not text:
            continue
        cap_count += 1
        pos = cap.get("position", "bottom")
        style = cap.get("style", "outline")
        start = off
        dur = _seg_duration(seg)
        # Clamp end strictly before next segment's start to avoid 1-frame overlap.
        if i + 1 < len(segs):
            next_off = offsets[i + 1]
            end = min(off + dur, next_off - 0.02)
        else:
            end = off + dur
        if end <= start + 0.01:
            continue

        # Auto-shrink font to fit width (max 90% of frame width).
        # DejaVu Bold: avg char width ≈ 0.55 * fontsize
        text_len = len(text)
        max_width_frac = 0.90
        # Estimate: fontsize such that text_len * 0.55 * fontsize <= W * max_width_frac
        fit_size = int((W * max_width_frac) / (text_len * 0.55)) if text_len > 0 else 60
        fontsize = max(24, min(64, fit_size))

        tf_real = os.path.join(capdir, f"cap_{i}.txt")
        tf = tf_real.replace("\\", "/").replace(":", "\\:")
        with open(tf_real, "w", encoding="utf-8") as f:
            f.write(text)

        y = {"top": "h*0.10", "center": "(h-text_h)/2", "bottom": "h*0.82"}[pos]
        # Use text_w so x is always centered and never overflows
        base = (
            f"drawtext=fontfile='{font}':textfile='{tf}':fontsize={fontsize}:"
            f"fontcolor=white:borderw=4:bordercolor=black@0.9:"
            f"x='min(max((w-text_w)/2,10),w-text_w-10)':y={y}:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
        if style == "box":
            base += ":box=1:boxcolor=black@0.55:boxborderw=14"
        chains.append(base)
        print(f"[captions] seg {i}: '{text[:35]}' size={fontsize} at {start:.1f}-{end:.1f}s")

    print(f"[captions] total caption segments: {cap_count}")
    if not chains:
        print("[captions] no caption text in plan; skipping burn")
        return src

    cmd = [FFMPEG, "-y", "-i", src]
    cmd += _thread_flags()
    cmd += ["-vf", ",".join(chains), "-c:v", "libx264", "-preset", "medium", "-crf", CRF,
            "-c:a", "copy", "-map_metadata", "-1", out]
    _run(cmd)
    print(f"[captions] burned {cap_count} captions -> {out}")
    return out


def mix_narration(src, plan, offsets, voice_wavs, out):
    """voice_wavs: dict {segment_index: wav_path}. Mix each at its offset.
    Segments are pre-extended by main.fit_segments_to_voices so narration plays
    at natural speed. Last segment narration gets full wav duration + fade so
    it never cuts mid-word. Output duration = video duration (duration=first)."""
    usable = {i: p for i, p in (voice_wavs or {}).items() if p and os.path.exists(p)}
    if not usable:
        print("[voice-mix] no usable voice wavs; skipping narration mix", file=__import__('sys').stderr)
        return src

    g = plan.get("global") or {}
    vgain = float(g.get("voice_gain", 1.0))
    # Boost voiceover to be clearly audible over original audio.
    vgain = max(vgain, 1.8)
    # Original audio NEVER muted: force at least 40% (legacy plans may carry 0.0).
    orig_vol = float(plan.get("audio_volume", 0.40) or 0.0)
    if orig_vol <= 0.01:
        orig_vol = 0.40

    print(f"[voice-mix] mixing {len(usable)} tracks, voice_gain={vgain:.2f}, orig_vol={orig_vol:.2f}")

    cmd = [FFMPEG, "-y", "-i", src]
    wavs = []
    for i, path in sorted(usable.items()):
        cmd += ["-i", path]
        wavs.append((i, path))

    last_idx = max(usable.keys())
    fc = []
    fc.append(f"[0:a]volume={orig_vol:.3f}[a0]")
    mix_inputs = ["[a0]"]
    for n, (i, wav_path) in enumerate(wavs, start=1):
        delay_ms = int(offsets[i] * 1000)
        target = _seg_duration(plan["segments"][i])
        wav_dur = _audio_duration(wav_path)
        tempo = 1.0
        if wav_dur > 0.01 and target > 0.01:
            if wav_dur > target * 1.15:
                tempo = min(1.15, wav_dur / target)
        # Last segment: allow full wav duration so narration never cuts mid-word.
        # Non-last: cap at target to avoid overlap with next segment.
        if i == last_idx and wav_dur > target:
            target_audio = wav_dur + 0.05
        else:
            target_audio = target
        fade_start = max(0.0, target_audio - 0.15)
        fc.append(
            f"[{n}:a]atempo={tempo:.4f},apad,atrim=0:{target_audio:.3f},"
            f"afade=t=out:st={fade_start:.3f}:d=0.15,"
            f"volume={vgain:.2f},adelay={delay_ms}|{delay_ms}[a{n}]"
        )
        mix_inputs.append(f"[a{n}]")
        print(f"[voice-mix] seg {i}: wav={wav_dur:.2f}s target={target:.2f}s delay={delay_ms}ms")

    fc.append("".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0:normalize=0[aout]")

    # Get video duration to cap audio exactly.
    vid_dur = analyze.probe_duration(src) or 0
    if vid_dur <= 0:
        vid_dur = _audio_duration(src)
    # Trim BOTH video and audio to exact source duration in filter graph.
    fc.append(f"[aout]atrim=0:{vid_dur:.3f},asetpts=PTS-STARTPTS[aout2]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[aout2]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-map_metadata", "-1",
            "-shortest", out]
    _run(cmd)
    print(f"[voice-mix] narration mixed -> {out}")
    return out


def apply_effects(src, plan, out):
    g = plan.get("global") or {}
    intro = g.get("intro_effect", "none")
    ending = g.get("ending_effect", "none")
    if intro == "none" and ending == "none":
        return src

    dur = None
    try:
        import analyze
        dur = analyze.probe_duration(src)
    except Exception:
        pass

    filters = []
    # NO intro fade — makes start look dark/blurry. Keep only short ending fade.
    if ending == "fade" and dur:
        filters.append(f"fade=t=out:st={max(0.0, dur-0.3):.3f}:d=0.3")
    if not filters:
        print("[effects] no effects to apply (intro fade removed for quality)")
        return src

    print(f"[effects] applying: {filters}")
    cmd = [FFMPEG, "-y", "-i", src]
    cmd += _thread_flags()
    cmd += ["-vf", ",".join(filters), "-c:v", "libx264", "-preset", "medium",
            "-crf", CRF, "-c:a", "copy", "-map_metadata", "-1", out]
    _run(cmd)
    return out


def apply_plan(src, plan, workdir="work", voice_wavs=None, out_name="final_edit.mp4"):
    """Full apply. Returns path to the finished video."""
    os.makedirs(workdir, exist_ok=True)
    # Drop segments marked for removal, then reindex 0..N-1 and remap voice wavs
    # (voice_wavs is keyed by the ORIGINAL segment index).
    kept = [s for s in plan["segments"] if s.get("action") != "remove"]
    if not kept:
        raise RuntimeError("plan has no keepable segments")
    remap = {}
    for new_i, seg in enumerate(kept):
        remap[seg.get("index", new_i)] = new_i
        seg["index"] = new_i
    plan = dict(plan)
    plan["segments"] = kept
    new_wavs = {}
    for old_i, path in (voice_wavs or {}).items():
        if old_i in remap:
            new_wavs[remap[old_i]] = path
    voice_wavs = new_wavs
    segs = plan["segments"]

    # Compute caption crop fractions. Uses UNION (max) across regions of the
    # same band so multiple text lines don't sum into over-cropping.
    #   top region    [t, b] -> crop top down to b        (crop_top = b)
    #   bottom region [t, b] -> crop bottom from t        (crop_bottom = 1 - t)
    caption_crop_top = 0.0
    caption_crop_bottom = 0.0
    delogo_regions = []
    for r in (plan.get("caption_regions") or []):
        action = r.get("action", "none")
        top = float(r.get("top_frac", 0.0) or 0.0)
        bot = float(r.get("bottom_frac", 0.0) or 0.0)
        if bot <= top:
            continue
        if action == "crop":
            if r.get("band") == "top":
                caption_crop_top = max(caption_crop_top, bot)
            elif r.get("band") == "bottom":
                caption_crop_bottom = max(caption_crop_bottom, 1.0 - top)
        elif action == "delogo":
            delogo_regions.append({"top_frac": top, "bottom_frac": bot})
    # Safety: never crop away more than 30% of a side.
    caption_crop_top = min(0.30, max(0.0, caption_crop_top))
    caption_crop_bottom = min(0.30, max(0.0, caption_crop_bottom))
    if caption_crop_top > 0.001 or caption_crop_bottom > 0.001:
        print(f"[captions] crop top={caption_crop_top:.3f} bottom={caption_crop_bottom:.3f}")

    # Actual source dimensions (delogo + aspect math need real pixels).
    src_w, src_h = W, H
    try:
        proc = subprocess.run(
            [FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error",
             "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", src], capture_output=True, text=True)
        parts_d = (proc.stdout or "").strip().split(",")
        if len(parts_d) >= 2 and parts_d[0].isdigit() and parts_d[1].isdigit():
            src_w, src_h = int(parts_d[0]), int(parts_d[1])
    except Exception:
        pass
    delogo_filter = _delogo_chain(delogo_regions, src_w, src_h)

    # Anti-reuse mirror OFF — natural orientation requested (no hflip).
    mirror = False

    # ONE gentle color grade for the WHOLE video — per-segment grades made
    # colors jump at every cut and looked bad (user complaint).
    gcolor = {"preset": "none", "brightness": 0.0, "contrast": 1.05, "saturation": 1.10}

    # Detect audio presence once.
    has_audio = True
    try:
        proc = subprocess.run(
            [FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error",
             "-select_streams", "a", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", src], capture_output=True, text=True)
        has_audio = "audio" in (proc.stdout or "")
    except Exception:
        has_audio = True

    # 1. Render segments.
    seg_paths = []
    for i, seg in enumerate(segs):
        p = os.path.join(workdir, f"seg_{i:02d}.mp4")
        render_segment(src, seg, p, has_audio=has_audio,
                       caption_crop_top=caption_crop_top,
                       caption_crop_bottom=caption_crop_bottom,
                       delogo_filter=delogo_filter,
                       mirror=mirror,
                       gcolor=gcolor)
        seg_paths.append(p)

    # 2. Concat.
    concat = os.path.join(workdir, "concat.mp4")
    concat_segments(seg_paths, concat)

    offsets, _ = output_offsets(plan)

    # 3. Captions.
    captioned = os.path.join(workdir, "captioned.mp4")
    step3 = apply_captions(concat, plan, offsets, captioned)

    # 4. Narration mix.
    narrated = os.path.join(workdir, "narrated.mp4")
    step4 = mix_narration(step3, plan, offsets, voice_wavs, narrated)

    # 5. Intro/ending effects. apply_effects may return its input unchanged when
    #    there is nothing to do, so ensure `final` actually exists on disk.
    final = os.path.join(workdir, out_name)
    result = apply_effects(step4, plan, final)
    if result != final and os.path.exists(result):
        import shutil
        shutil.copyfile(result, final)

    return final


if __name__ == "__main__":
    # Self-test on a synthetic plan.
    import sys
    print("apply.py loaded; use via main.py")
