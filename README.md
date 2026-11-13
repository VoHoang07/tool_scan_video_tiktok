# TikTok 10-Channel Video Pipeline & Scheduler

Automated Python pipeline for sourcing, splitting, localizing, rendering, and publishing short-form vertical videos across **10 TikTok channels**. Designed to run in a two-machine setup: an Apple Silicon Mac for configuration/control and an Intel Mac (`x86_64`) worker running 24/7 browser and FFmpeg jobs.

---

## Core Modules & Workflow

### 1. Video Sourcing & Multi-Part Splitting
- **Short-Form Discovery (`crawler.py`, `downloader.py`)**:
  - Downloads source videos from Douyin, TikTok, and YouTube using `yt-dlp` and `curl_cffi`.
  - Filters candidates by region (CN/VN), duration, and engagement thresholds (`MIN_PLAY_COUNT`, `MIN_DIGG_COUNT`).
- **Long-Form Episode Splitters (`bongcuc_splitter.py`, `gausohai_splitter.py`)**:
  - **`audio_dem_khuyen`**: Splits full-length audio stories from `data/bongcuc_full_catalog.json` into ~4m40s vertical parts (`1080x1920`), pairs the narration with time-of-day background footage (daytime cafe/dessert clips vs. nighttime 1080p Korean mukbang clips), and schedules parts in interleaved order.
  - **`video_AI_hay`**: Splits animated episodes from `data/gausohai_full_catalog.json` into sequential parts, trims source intros/outros, crops corner watermarks, and schedules a 4:1 continuation-to-new-episode wave.
- **Localization & Audio Mixing (`transcriber.py`, `translator.py`, `tts_engine.py`, `composer.py`)**:
  - Transcribes foreign audio locally with `faster-whisper` (CTranslate2), translates scripts into Vietnamese, synthesizes voiceovers with `edge-tts`, ducks original audio to `12%` in `FFmpeg`, and burns in styled `ASS`/`SRT` subtitles.

### 2. Pre-Upload Verification & Content Filtering
- **6-Layer Technical Check (`stream1_antidetect_skill.py`)**:
  - Checks output resolution/codec (`1080x1920`, `H.264 yuv420p`), conditions audio sample rates (`44.1kHz`), strips macOS extended attributes (`com.apple.macl`), writes device metadata tags, and prevents consecutive uploads from the same episode.
- **Community Guidelines Filter (`community_guidelines_guard.py`)**:
  - Blocks sensitive episode IDs before rendering, removes restricted terms from on-screen titles, captions, and hashtags, and switches `audio_dem_khuyen` hashtags between daytime (`05:00–17:59`) and nighttime (`18:00–04:59`) sets.
  - Reads TikTok Studio's pre-post check (`Kiểm tra nội dung nhanh`) before clicking `Đăng`; if a content warning appears, the upload is aborted and the file is moved to `quarantine_cg_violation/`.

### 3. Modular Browser Publisher & Scheduler (`auto_poster.py` + 5 Mixins)
- **`auto_poster.py`**: Main `TikTokAutoPoster` class, 10-channel schedule loop, in-place module hot-reloading (`importlib.reload`), process watchdog, and Git telemetry push.
- **`poster_browser.py`**: Persistent Chrome profile launcher (`browser_profiles/<channel>/`), `fcntl.flock` session locking (`.tiktok_browser_session.lock`), per-channel hardware fingerprint config, and encrypted session sync.
- **`poster_studio_ui.py`**: TikTok Studio DOM actions (file input, caption/hashtag editor, privacy dropdown, copyright/content check switches, and post button click).
- **`poster_cleanup.py`**: Scans `tiktokstudio/content` to remove duplicate or flagged posts and switch private posts to public.
- **`poster_media_prep.py`**: Prepares `output/<channel>/ready/*.mp4` outside the browser lock so FFmpeg rendering does not block other channels due for upload.
- **`poster_stream2_warmup.py`**: Runs Stream 2 (`growth_24h_filter.py`) interaction rounds and FYP browsing sessions.

### 4. System Switches (`master_switch_controller.py` & `schedule_config.json`)
Four switches in `schedule_config.json` control runtime behavior on the remote worker:
- **`master_switch`**: Global on/off switch for the worker.
- **`switch_1_factory`**: Background crawling and `ready/` queue preparation (up to 5 videos per niche).
- **`switch_2_upload`**: Scheduled TikTok Studio uploads across the 10 channels.
- **`switch_3_interact`**: 24-hour filter interaction rounds and FYP browsing.
- **`switch_4_clear`**: Retains the top 8 posted videos per channel and deletes older `.mp4` files and temporary workspace files older than 2 hours.

---

## Channel Schedule (`schedule_config.json`)

| # | Channel Folder | Niche / Content Type | Morning | Afternoon | Night Batch (From 23:00) |
| :-: | :--- | :--- | :-: | :-: | :-: |
| 1 | `audio_dem_khuyen` | Audio Stories & Family Drama | `08:48` | `13:25` | **`23:00`** (First) |
| 2 | `video_AI_hay` | Animated Stories & AI Shorts | `07:25` | `12:05` | `23:06` |
| 3 | `meo_vat_gia_dinh` | Home Tips & Life Hacks | `07:10` | `11:35` | `23:12` |
| 4 | `suc_khoe_duong_sinh` | Wellness & Daily Health | `06:50` | `11:55` | `23:18` |
| 5 | `an_vat_moi_ngay` | Street Food & Cooking | `07:38` | `12:22` | `23:24` |
| 6 | `baby_AI` | Baby & Family Videos | `08:05` | `12:42` | `23:30` |
| 7 | `nuoi_con_eq` | Parenting & Kids EQ | `08:15` | `12:52` | `23:36` |
| 8 | `phu_nu_khi_chat` | Women's Lifestyle & Mindset | `08:28` | `13:05` | `23:42` |
| 9 | `phu_kien_tien_ich` | Gadgets & Everyday Tools | `09:05` | `13:45` | `23:48` |
| 10 | `tu_chan_gioi` | 3D Cultivation & Fantasy | `09:18` | `14:10` | `23:54` |

---

## Directory Structure

```text
tool_scan_video_tiktok/
├── main.py                                  # CLI entry point & stream runner
├── harness.py                               # Pre-push verification suite & 1-step switch CLI
├── schedule_config.json                     # 10-channel post times, switches & remote triggers
├── HANDOFF.md                               # Architecture notes, module map & active tasks
├── AGENT.md                                 # Bug history & regression invariants ([BUG-001]..[BUG-025])
├── tiktok_pipeline/
│   ├── auto_poster.py                       # TikTokAutoPoster core scheduler & telemetry sync
│   ├── poster_browser.py                    # Chrome stealth context, locks & session sync
│   ├── poster_studio_ui.py                  # TikTok Studio upload form & pre-check DOM controls
│   ├── poster_cleanup.py                    # Content page deduplication & flagged post removal
│   ├── poster_media_prep.py                 # Ready-queue selector & pre-upload verification
│   ├── poster_stream2_warmup.py             # Stream 2 24h filter & FYP browsing routines
│   ├── bongcuc_splitter.py                  # Multi-part splitter & background compositor for audio_dem_khuyen
│   ├── gausohai_splitter.py                 # Multi-part splitter & watermark cropper for video_AI_hay
│   ├── stream1_antidetect_skill.py          # 6-layer video/audio/metadata verification
│   ├── community_guidelines_guard.py        # Episode blocklist & text/hashtag sanitizer
│   ├── master_switch_controller.py          # 4-switch state manager & disk cleanup
│   ├── agy_intel_bridge.py                  # Git sync & daemon bridge for Intel Mac worker
│   ├── crawler.py                           # Multi-platform video crawler & deduplication
│   ├── downloader.py                        # yt-dlp / curl_cffi media downloader
│   ├── transcriber.py                       # faster-whisper speech-to-text
│   ├── translator.py                        # Script translation & hashtag rotation
│   ├── tts_engine.py                        # edge-tts voice synthesis
│   ├── composer.py                          # FFmpeg audio mixing & subtitle rendering
│   ├── growth_24h_filter.py                 # 6-step interaction workflow for Stream 2
│   └── macos_desktop_controller.py          # macOS CoreGraphics hardware mouse/keyboard input
├── data/
│   ├── bongcuc_full_catalog.json            # Pre-indexed catalog (147 episodes with durations)
│   └── gausohai_full_catalog.json           # Pre-indexed catalog (353 episodes)
├── output/<channel>/{ready,posted}/         # Per-channel video queues
├── remote_telemetry/                        # Screenshots & intel_status_report.json from worker
├── run_24_7.sh                              # 24/7 daemon launcher with caffeinate
└── setup_mac_intel.sh                       # Environment setup script for Intel Mac (x86_64)
```

---

## Setup & Usage

### 1. Install Dependencies
```bash
git clone https://github.com/VoHoang07/tool_scan_video_tiktok.git
cd tool_scan_video_tiktok

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```
*(On an Intel Mac `x86_64`, run `./setup_mac_intel.sh`).*

### 2. Verify Code & Config (`harness.py`)
Run the 4-layer check (syntax, `schedule_config.json`, 59 `TikTokAutoPoster` methods, and catalog rules) before pushing changes:
```bash
./venv/bin/python harness.py --verify
```

### 3. Toggle Switches & Check Worker Status
```bash
# View current switch states and latest Intel worker telemetry
./venv/bin/python harness.py --status

# Toggle switches in schedule_config.json and push to origin/main in one step
./venv/bin/python harness.py --switch 2 on --push
./venv/bin/python harness.py --switch 3 off --push
```

### 4. Log In & Run Streams
```bash
# Log in to a channel profile once (saved under browser_profiles/<channel>/)
./venv/bin/python main.py --login-channel audio_dem_khuyen

# Run Stream 1 (Scheduled uploads across 10 channels)
./venv/bin/python main.py --stream-upload

# Run Stream 2 (24h interaction filter & FYP browsing)
./venv/bin/python main.py --stream-filter24h

# Publish 1 video immediately for a specific channel
./venv/bin/python main.py --post-now audio_dem_khuyen
```
