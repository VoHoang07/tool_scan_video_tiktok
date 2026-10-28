# TikTok & Douyin Multi-Channel Video Localization Pipeline

An automated Python CLI pipeline for discovering, downloading, transcribing, translating, AI-dubbing, subtitle-rendering, and scheduling short-form videos from **Douyin** and **TikTok** into Vietnamese across **4 specialized channels**.

---

## Key Features

- **Multi-Source Discovery & 24h Growth Filtering (`crawler.py`, `growth_24h_filter.py`, `downloader.py`)**
  - Extracts watermark-free videos from seed URLs, full creator profiles, and niche hashtag/audio feeds using `yt-dlp` and `curl_cffi`.
  - Tracks engagement velocity over 24-hour windows (`growth_24h_history.json`) to prioritize high-performing candidate videos.
- **Language-Aware Localization Pipeline (`transcriber.py`, `translator.py`, `tts_engine.py`, `composer.py`)**
  - **Speech-to-Text:** Runs local on-device transcription with timestamps using `faster-whisper`.
  - **Context-Aware Translation:** Translates Chinese/foreign transcripts into natural Vietnamese storytelling scripts.
  - **Neural Voiceover & Audio Ducking:** Synthesizes Vietnamese speech via `edge-tts` and mixes audio in `FFmpeg`, ducking original tracks to `12%` (`0.12`) to retain background music and sound effects.
  - **Hardcoded Subtitle Blur & Styled Burn-in:** Blurs original foreign burned-in captions and renders styled Vietnamese subtitles (`ASS`/`SRT`).
- **4-Channel Queue & Two-Way Inventory Sync (`main.py`, `output/danh_sach_video.csv`)**
  - Manages `ready/` and `posted/` queues across 4 channels (`video_AI_hay`, `baby_AI`, `audio_dem_khuyen`, `tu_chan_gioi`).
  - Two-way synchronization between `output/danh_sach_video.csv`, macOS Finder drag-and-drop, and browser file-upload dialogs.
- **24/7 Autonomous Daemon & Browser Automation (`auto_poster.py`, `run_24_7.sh`)**
  - Uses isolated persistent Playwright Chromium profiles (`browser_profiles/`) to publish videos on staggered daily schedules (`schedule_config.json`).
  - Includes cross-machine synchronization (`sync_to_intel.sh`, `sync_git_push.sh`) and remote visual telemetry (`remote_telemetry/`).

---

## Architecture & Tech Stack

```text
tool_scan_video_tiktok/
├── main.py                             # CLI entry point & daemon orchestrator
├── tiktok_pipeline/
│   ├── crawler.py                      # Seed URL, creator profile, and related video discovery
│   ├── growth_24h_filter.py            # 24-hour engagement velocity evaluator
│   ├── downloader.py                   # Watermark-free video & metadata extractor
│   ├── transcriber.py                  # faster-whisper ASR speech-to-text engine
│   ├── translator.py                   # Script translation & Vietnamese adaptation
│   ├── tts_engine.py                   # Microsoft Edge neural TTS synthesis
│   ├── composer.py                     # FFmpeg audio ducking, subtitle blur & burn-in
│   ├── auto_poster.py                  # Playwright browser automation for scheduled posting
│   └── macos_desktop_controller.py     # Native macOS UI & upload watcher integration
├── schedule_config.json                # Multi-channel daily publishing schedule
├── run_24_7.sh                         # Caffeinated 24/7 autonomous daemon runner
├── setup_mac_intel.sh                  # Automated environment bootstrapper
└── requirements.txt                    # Python dependencies
```

| Component | Technologies |
|---|---|
| **Core Language** | Python 3.10+, Bash |
| **Video & Audio Processing** | FFmpeg (`static-ffmpeg`), Pillow |
| **Speech Recognition (ASR)** | `faster-whisper` (CTranslate2) |
| **Text-to-Speech (TTS)** | `edge-tts` |
| **Scraping & Browser Automation** | `yt-dlp`, `playwright`, `curl_cffi`, `requests` |

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
*(On an Intel MacBook, run `./setup_mac_intel.sh` for one-step environment setup).*

### 2. Configure Environment
Copy `.env.example` to `.env` and configure your translation API keys and channel parameters:
```bash
cp .env.example .env
```

### 3. CLI Usage Examples

**Crawl and localize videos by channel or direct URL:**
```bash
# Process up to 3 videos for Channel 1
./venv/bin/python main.py --channel 1 --limit 3

# Localize a specific TikTok/Douyin video URL into Channel 3
./venv/bin/python main.py --url "https://www.tiktok.com/@creator/video/123456789" --channel 3
```

**Check inventory status & sync `ready/` vs `posted/` queues:**
```bash
# Display 4-channel inventory table and reconcile CSV/Finder states
./venv/bin/python main.py --status

# Watch macOS browser upload dialogs and auto-move uploaded videos to posted/
./venv/bin/python main.py --watch-sync
```

**Configure schedule & launch 24/7 autonomous mode:**
```bash
# One-time QR login for each channel's isolated browser profile
./venv/bin/python main.py --login-channel video_AI_hay

# Configure daily posting slots (staggered automatically across channels)
./venv/bin/python main.py --set-schedule "08:00,12:30,20:00"

# Start 24/7 continuous crawling and scheduled publishing daemon
./run_24_7.sh
```
