# NOVA Studio — Project State & Handoff

> User ke sath language: **Roman Urdu**, concise. Technical terms English me.
> Ye file poora project state rakhti hai — naya session pehle ISKO parhe, phir kaam kare.

## Project Goal
Fully automated AI video pipeline: trend research → video production → repurpose → publish → comments AI → analytics feedback loop. Cloned open-source engines ko NOVA ke existing system ke sath wire karna hai.

## Environment
- Windows, PowerShell 5.1 (chaining: `cmd1; if ($?) { cmd2 }` — `&&` NAHI)
- Project: `D:\Nova Automation` (NOVA: worker/, uploader-fb/, uploader-multi/, kokoro/, pipeline/, site/, plans/, fastpoll.py, ytapi_server.py, render.yaml)
- Cloned repos: `D:\repos\`
- GitHub API token: `D:\Nova Automation\github api.txt` (raw → trim → `Authorization: token <g>`)

## Repo Inventory — `D:\repos\` (17 repos)

| Repo | Role |
|---|---|
| **MoneyPrinterTurbo** | Stage 2 PRIMARY engine — GH Actions native. Headless: `cli.py` + FastAPI `app/controllers/v1`. `video_source = "pexels" \| "pixabay" \| "coverr" \| "wavespeed"`. AI text-to-video: `app/services/muapi.py` (MuAPI submit/poll), `volcengine_seedance.py`. Free Edge TTS, subtitles, BGM. `upload_post.py` = TikTok/IG/YT Shorts cross-post (upload-post.com). 60s video ≈ 5–15 min runner. |
| short-video-maker (gyoridavid) | Stage 2 backup shorts engine (REST/MCP, Node, docker) |
| AI-Youtube-Shorts-Generator (Anil-matcha) | Stage 2 alternate (source→shorts, CI Python) |
| huobao-drama | Stage 2 alternate — 3–5 min dramas (agents: script_rewriter → storyboard_breaker → prompt_generator). VPS/docker. |
| Agent-Reach | Stage 1 trend signals — Python CLI (`agent_reach/cli.py`), 13+ platforms, backends/channels/runtime |
| social-media-research-skills | Stage 1 — 13 skill md: trend-discovery, outlier-post-finder, comment-mining, content-repurposing, audience-research, etc. |
| last30days-skill | Stage 1 grounded research brief (agent-mode.md, competitors.md) |
| youtube-skills (ZeroPointRepo) | Stage 1 — transcripts/search for agents |
| **youtube-agent-skill** (Jakeschincariol) | Strategy/packaging — 11 skills: `yt-audit, yt-chapters, yt-comment, yt-edit, yt-package, yt-plan, yt-retention, yt-script, yt-seo, yt-shorts, yt-viral`. Markdown prompts (code nahi). |
| autoclip | Stage 4 — long → ranked shorts (`backend/pipeline` headless). NOTE: is repo ke apne AGENTS.md quality gates hain (`scripts/quality_gate.py`) — code change pe follow karo. |
| VideoLingo | Stage 4 — Netflix-level subtitles + multi-language dubbing |
| whisperX | Stage 3 — word-level alignment → karaoke captions base |
| opensource-clipping | Stage 3 — face-tracking 9:16 crop + B-roll + kinetic subs |
| Real-ESRGAN | Stage 3 upscale (model weights runtime pe download hote hain) |
| Practical-RIFE | Stage 3 fps interpolation (weights runtime download) |
| RobustVideoMatting | Stage 3 background replace (weights runtime download) |
| postiz-app | Stage 5 — scheduler (reference ya direct use), 37.7k★ |

## Final Pipeline Flow
```
NOVA task → Stage 1 trend (Agent-Reach + skills)
  → Stage 2 production (MoneyPrinterTurbo on GH Actions; dramas = huobao-drama VPS)
  → Stage 3 enhance (whisperX karaoke, face-crop, upscale)
  → Stage 4 repurpose (autoclip: long → ranked shorts)
  → Stage 5 publish (NOVA worker: YouTube OAuth + FB/IG/TikTok cookies)
  → Stage 6 comments AI (yt-comment rules)
  → Stage 7 analytics feedback → agla brief (loop)
```

## Wiring Contracts (5 JSON)
1. `task.json` — wizard → scheduler (filters, accounts, format vertical/horizontal, duration 15s–5min, schedule)
2. `topic_brief.json` — Stage 1 → Stage 2 (angles, hooks, references)
3. `final.mp4 + meta.json` — Stage 2 → NOVA upload
4. `shorts_queue.json` — Stage 4 → scheduler (virality-ranked)
5. `analytics.json` — Stage 7 → Stage 1 (loop)

## Decisions (locked)
- **OpenMontage DELETED** (user decision) — do NOT re-clone. Reason: GH Actions pe agent host nahi chal sakta. Production engine = MoneyPrinterTurbo.
- GH Actions runner limits: 4-core/16GB, no GPU — AI-gen sirf cloud APIs se (muapi/wavespeed/Seedance).
- **Stack decision (Oct 2026, user-locked):**
  - **Video gen (AI-gen continuity engine) = Gemini Omni Flash** (`gemini-omni-1.1-flash`, paid tier only, ~$0.10/sec 720p). Continuity features: `IMAGE_REF_n` subject reference (same character/clothes), `FIRST_FRAME`/`LAST_FRAME` control, `task:"extend"` = 3–10s seamless continuation, multi-turn (`previous_interaction_id`) pe model-generated video extend = NO 10s cap + dialogue continue, stateful editing, 9:16 + 4K + native audio. Limits: English best (Urdu untested), uploaded-extend ≤10s (model-gen multi-turn pe cap nahi), audio-ref upload nahi, SynthID watermark. 60s = 6×10s extend chain + ek hi kokoro voiceover + ffmpeg xfade grade ≈ $6/video.
  - **Model routing (user-locked 27 Oct 2026):**
    - **Text (script/title/desc/terms/comments)**: **Gemini first → OpenRouter fallback → NVIDIA NIM last**. Implemented in `pipeline/brains.py` (`complete`, `complete_json`, `documentary_brief`), keys via `pipeline/apikeys.py` hub/env then local `*api key.txt`.
    - **NVIDIA = sirf 2 kaam**: (1) **text-to-video** (Cosmos3-Nano, free, `nova_nvidia.text_to_video`), (2) **video analysis → JSON** (`nova_nvidia.analyze_video` / `analyze_frames`, Nemotron 3.5 Super VL). Baaki NVIDIA text sirf aakhri fallback.
    - Gemini verified working: `gemini-3-flash-preview`, `gemini-2.5-flash`; `gemini-omni-1.1-flash` key pe visible hai (paid, abhi use nahi).
    - OpenRouter key abhi missing (`openrouter api key.txt` + `OPENROUTER_API_KEYS`) — chain khud skip kar deti hai.
  - **Documentary = free stock path** — `pipeline/docgen.py` → Gemini brief (script + terms + title/desc/tags) → MoneyPrinterTurbo `--video-script/--video-terms` → **Pixabay** clips (Pexels user-ne pause kiya) + Edge TTS English voice + subtitles → `pipeline/docs/<slug>/final-1.mp4` + `brief.json` + `meta.json`. Zero cost, continuity issue nahi (B-roll style). Kokoro single-60s-VO path baad me (`--custom-audio-file`).
  - Local GPU: Intel HD 3000 (no CUDA) — self-host video gen kabhi NAHI; sirf cloud APIs.
  - Keys already present: `nvidia api key.txt`, `gemini api key.txt`, `google api.txt`.
  - Gemini paid test/authorization: **abhi nahi** — pehle NVIDIA models + documentary path ready karo (user instruction).

## Next Steps (order se karo, har bade step pe user ko update do)
1. **Documentary path verify** — `pipeline/docgen.py` se English-voice full run (Gemini script → Pixabay → Edge TTS → subtitles → 9:16 final), output `pipeline/docs/<slug>/final-1.mp4`.
2. **NVIDIA Cosmos T2V unlock** — build.nvidia.com par cosmos3-nano page pe "Get API" (wahi key) → phir `nova_nvidia.text_to_video` live test (720p, 9:16, ~8s, free). Jab tak 404 aata hai tab tak B-roll sirf Pixabay se.
3. **youtube-agent-skill prompts mine** — `D:\repos\youtube-agent-skill\skills\` ke 11 skills parho; reusable prompts/patterns nikalo; `pipeline/brains.py` ke brief/comment prompts me merge karo. Mapping: `yt-script`+`yt-package` → brief (scored hook + title/thumbnail pair); `yt-comment` → NOVA AI reply; `yt-retention`+`yt-viral` → analytics review.
4. **Studio wizard + scheduler** — task.json schema, cron math, job queue.
5. **Pehla end-to-end run** — MoneyPrinterTurbo GH Actions: 1 topic → 1 video → 1 upload.
6. autoclip shorts_queue wiring, comments AI, analytics loop.
7. **Kokoro single-voiceover path** — `--custom-audio-file` + faster-whisper subtitles (deps installed) taake ek hi 60s VO rahe, clip-wise TTS nahi.

## NOVA modules (naye, is session me)
- `nova_nvidia.py` — NVIDIA ka **sirf 2 kaam**: `text_to_video()` (Cosmos3-Nano, free) aur `analyze_video()/analyze_frames()` (Nemotron VL → JSON). Key env-first, phir `nvidia api key.txt`.
- `pipeline/brains.py` — text chain Gemini → OpenRouter → NVIDIA; `documentary_brief()` script + terms + title/desc/tags JSON.
- `pipeline/docgen.py` — documentary runner (Gemini brief → MPT CLI → Pixabay + Edge TTS → `pipeline/docs/<slug>/`).
- Existing social download/edit/upload modules (`pipeline/download.py`, `edit.py`, `smart.py`, `upload_social.py`, `metadata.py`) **jaisa hai waisa rakha gaya** — chheda nahi.


## Rules
- Paid API calls (muapi/wavespeed/Seedance/Kling/Veo), publishing, releases — **sirf user ke explicit authorization** ke sath.
- Secrets (`*.txt` key files, firebase_service_account.json, cookies) kabhi commit/upload na karo.
- Code likhne se pehle existing repo conventions parho; tests/verify jab possible ho.
