# TikTok & Douyin Multi-Channel Video Localization & 24/7 Growth Pipeline

An end-to-end automated Python pipeline for discovering, downloading, transcribing, translating, AI-dubbing, subtitle-rendering, scheduled publishing, and organic 24-hour community growth across **4 specialized TikTok channels**.

---

## Key Features

- **Strict Region & High-Engagement Discovery (`crawler.py`, `downloader.py`)**
  - Extracts watermark-free videos from **China (Douyin)** and **Vietnam (TikTok)** seed URLs, creator profiles, and niche hashtag feeds using `yt-dlp` and `curl_cffi`.
  - Enforces strict region filtering (rejects non-CN/VN scripts and Latin-only foreign content), engagement velocity thresholds (`MIN_PLAY_COUNT`, `MIN_DIGG_COUNT`), and dedicated topic gates per channel (e.g., strict baby/toddler AI validation for `baby_AI`).
- **Language-Aware Localization Pipeline (`transcriber.py`, `translator.py`, `tts_engine.py`, `composer.py`)**
  - **Vietnamese Source Videos (`vi`):** Retains 100% of the original watermark-free video, audio, and caption without artificial re-dubbing.
  - **Chinese / Foreign Source Videos (`zh`):**
    - **Speech-to-Text:** Local timestamped transcription via `faster-whisper` (CTranslate2).
    - **Context-Aware Translation:** Adapts Chinese transcripts into natural Vietnamese storytelling scripts.
    - **Neural Voiceover & Audio Ducking:** Synthesizes Vietnamese speech via `edge-tts` and mixes audio in `FFmpeg`, ducking original tracks to `12%` (`0.12`) to preserve background music and sound effects (SFX).
    - **Subtitle Blur & Styled Burn-in:** Blurs hardcoded foreign captions and burns in styled Vietnamese subtitles (`ASS`/`SRT`).
- **Dual-Stream 24/7 Autonomous Operation (`auto_poster.py`, `growth_24h_filter.py`, `run_24_7.sh`)**
  - **Stream 1 — Continuous Replenishment & Scheduled Upload (`--stream-upload`):**
    - Maintains a 5-video buffer in `output/<channel>/ready/` across all 4 channels (`video_AI_hay`, `baby_AI`, `audio_dem_khuyen`, `tu_chan_gioi`).
    - Publishes on staggered daily schedules (`schedule_config.json`) with a 180-minute anti-spam cooldown guard and automatic `Who can watch this video -> Everyone (Public)` enforcement.
  - **Stream 2 — 24h Filter Organic Growth Engine (`--stream-filter24h`):**
    - Runs 7 daily sessions × 10 videos per channel targeting newly created channels (`#tuongtaccheo`, `#moixaykenh`, `#followcheo`, `#xaykenhtiktok`) with global cross-channel deduplication.
    - Executes a 6-step human workflow per video: **(1)** Watch full video (`15s–25s`) → **(2)** Like (`❤️`, verified past TikTok's `3.0s` rollback timer) → **(3)** Copy Share Link (`🔗`) → **(4)** Add to Favorites (`🔖`) → **(5)** Type a Vietnamese comment with human keystroke cadence (`💬`) → **(6)** Transition via in-page SPA click to the creator's profile (`/@creator`) and click the primary `Follow` button (`[data-e2e="follow-button"]`).
- **Anti-Detect Stealth & Native macOS Hardware Input (`macos_desktop_controller.py`)**
  - Launches isolated persistent Google Chrome profiles (`browser_profiles/`) with `--disable-blink-features=AutomationControlled`, `no_viewport=True`, and `WeakMap`-backed `Function.prototype.toString` guards against `webmssdk.js` CDP `Runtime.enable` traps.
  - Dispatches OS-level mouse trajectories and clicks via macOS CoreGraphics (`CGEventCreateMouseEvent`) and solves TikTok's 3D rotation puzzle captcha automatically using OpenCV polar-coordinate template matching.
- **Cross-Machine Git Sync, Remote Telemetry & Auto-Revive Watchdog**
  - Synchronizes code, hot-reloads live daemon modules (`importlib.reload`), and processes remote triggers (`remote_trigger_post_now`, `remote_trigger_filter_24h`) between Apple Silicon and Intel (`x86_64`) Macs via GitHub.
  - Pushes live screenshots and JSON execution logs to `remote_telemetry/intel_status_report.json`.
  - Built-in `caffeinate -dimsu` sleep prevention and Cross-Stream Watchdog (Stream 2 automatically revives Stream 1 if interrupted).

---

## Architecture & Directory Structure

```text
tool_scan_video_tiktok/
├── main.py                             # CLI entry point & dual-stream daemon orchestrator
├── tiktok_pipeline/
│   ├── config.py                       # Channel profiles, niche keywords, and quality thresholds
│   ├── crawler.py                      # CN/VN region filter, seed/profile discovery & queue manager
│   ├── growth_24h_filter.py            # 6-step 24h community growth engine & SPA profile verifier
│   ├── downloader.py                   # Watermark-free video & metadata extractor (yt-dlp / curl_cffi)
│   ├── transcriber.py                  # faster-whisper ASR speech-to-text engine
│   ├── translator.py                   # Script translation & Vietnamese adaptation
│   ├── tts_engine.py                   # Microsoft Edge neural TTS synthesis
│   ├── composer.py                     # FFmpeg audio ducking, subtitle blur & burn-in
│   ├── auto_poster.py                  # Playwright stealth publisher, scheduler & telemetry sync
│   └── macos_desktop_controller.py     # macOS CoreGraphics CGEvent hardware mouse/keyboard controller
├── output/
│   ├── video_AI_hay/{ready,posted}/    # Channel 1: AI Animation / Pets / Sci-Fi Short Films
│   ├── baby_AI/{ready,posted}/         # Channel 2: Baby & Toddler AI / Family Comedy
│   ├── audio_dem_khuyen/{ready,posted}/# Channel 3: Late-Night Audio Stories & Short Dramas
│   ├── tu_chan_gioi/{ready,posted}/    # Channel 4: AI Cultivation / Xianxia / Fantasy
│   └── danh_sach_video.csv             # Master inventory & caption/hashtag tracking sheet
├── remote_telemetry/                   # Live screenshots & intel_status_report.json from remote worker
├── schedule_config.json                # Staggered upload schedules, 24h filter slots & remote triggers
├── growth_24h_history.json             # Cross-channel 24h interaction & follow-back ledger
├── run_24_7.sh                         # Caffeinated 24/7 autonomous daemon runner
├── setup_mac_intel.sh                  # One-step Intel Mac (x86_64) environment bootstrapper
└── requirements.txt                    # Python dependencies
```

| Component | Technologies |
|---|---|
| **Core Language** | Python 3.9+, Bash, AppleScript |
| **Video, Audio & Vision** | FFmpeg (`static-ffmpeg`), OpenCV (`opencv-python`), NumPy, Pillow |
| **Speech Recognition (ASR)** | `faster-whisper` (CTranslate2) |
| **Text-to-Speech (TTS)** | `edge-tts` |
| **Browser & OS Automation** | `playwright` (Google Chrome), macOS Quartz `CoreGraphics` (`ctypes`), `curl_cffi`, `yt-dlp` |

---

## Getting Started

### 1. Installation
```bash
git clone https://github.com/VoHoang07/tool_scan_video_tiktok.git
cd tool_scan_video_tiktok

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```
*(On an Intel MacBook `x86_64`, run `./setup_mac_intel.sh` for automated environment setup).*

### 2. One-Time Channel Login (QR Code)
Log into each of the 4 isolated Chrome profiles once; sessions persist automatically in `browser_profiles/<channel>/`:
```bash
./venv/bin/python main.py --login-channel video_AI_hay
./venv/bin/python main.py --login-channel baby_AI
./venv/bin/python main.py --login-channel audio_dem_khuyen
./venv/bin/python main.py --login-channel tu_chan_gioi
```

### 3. Running the 24/7 Dual-Stream Pipeline

**Terminal Window 1 — Stream 1 (Auto-Crawl, Queue Replenishment & Scheduled Upload):**
```bash
./venv/bin/python main.py --stream-upload
```

**Terminal Window 2 — Stream 2 (24h Filter Organic Growth Engine — 7x/Day per Channel):**
```bash
./venv/bin/python main.py --stream-filter24h
```
*(Both streams coordinate browser and OS mouse access via an inter-process `fcntl.flock` lock and automatically pull live updates from `origin/main`).*

### 4. Additional CLI Commands

```bash
# Crawl and localize up to 3 videos for a specific channel (1..4)
./venv/bin/python main.py --channel 2 --limit 3

# Localize a specific TikTok/Douyin video or creator profile URL into a channel
./venv/bin/python main.py --url "https://www.tiktok.com/@creator/video/123456789" --channel 3

# Check 4-channel inventory status (ready/ vs posted/) and reconcile CSV
./venv/bin/python main.py --status

# Publish 1 ready video immediately for a specific channel
./venv/bin/python main.py --post-now baby_AI

# Run 1 immediate round of the 24h Filter Growth Engine for a channel
./venv/bin/python main.py --run-filter-24h baby_AI --filter-count 5
```
