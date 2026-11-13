# Python Media Ingestion, Processing & Multi-Channel Dispatch Pipeline

A Python-based media ingestion and processing pipeline built to run unattended across two macOS nodes: a local control machine (Apple Silicon) and a headless/GUI worker node (Intel `x86_64`). The system handles source ingestion, perceptual deduplication, ASR transcription, TTS audio ducking, FFmpeg filtergraph compositing, pre-dispatch policy validation, and scheduled browser automation across 10 channels.

---

## System Architecture

The pipeline separates **CPU/IO-bound media preparation** from **stateful browser sessions** so long FFmpeg jobs never block scheduled dispatch windows.

```text
[Source Feeds / Catalogs]
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ 1. Ingestion & Deduplication (crawler.py, downloader.py)      │
│    • yt-dlp / curl_cffi extraction                            │
│    • Visual pHash/vHash & audio fingerprint checks            │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ 2. Media Processing & Compositing                             │
│    • Short-form: faster-whisper ASR -> translation ->         │
│      edge-tts voiceover -> FFmpeg audio ducking & ASS burn-in │
│    • Long-form splitters (bongcuc_splitter, gausohai_splitter)│
│      Time-sliced chunking, 1080x1920 vertical reframing,      │
│      time-of-day background compositing (-preset superfast)   │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ 3. Pre-Dispatch Validation Gate                               │
│    • stream1_antidetect_skill.py (codec, sample rate, xattr)  │
│    • community_guidelines_guard.py (episode blocklist,        │
│      regex text/hashtag sanitization)                         │
└───────────────────────────────────────────────────────────────┘
        │
        ▼  output/<channel>/ready/*.mp4
┌───────────────────────────────────────────────────────────────┐
│ 4. Scheduler & Browser Worker (auto_poster.py + 5 Mixins)     │
│    • Acquires fcntl.flock (.tiktok_browser_session.lock)      │
│    • Launches isolated Chrome profile + Quartz CGEvent input  │
│    • Runs pre-post studio check -> publishes or quarantines   │
│    • Commits JSON telemetry & screenshots back to Git         │
└───────────────────────────────────────────────────────────────┘
```

### Key Engineering Decisions

- **Control Plane vs. Worker Plane over Git (`agy_intel_bridge.py`)**:
  Configuration changes (`schedule_config.json`) and code updates are pushed from the Apple Silicon machine. The Intel worker polls the repository, compares file modification timestamps (`_get_codebase_mtime_snapshot`), and reloads modified Python modules in-place via `importlib.reload` without killing active daemon processes.
- **Lock Separation (FFmpeg Outside Browser Mutex)**:
  Media downloading and FFmpeg encoding for a 5-minute segment take 3–6 minutes on `x86_64`. `_get_next_ready_video_for_channel()` runs before acquiring `.tiktok_browser_session.lock` so the Chrome lock is only held during the actual UI upload transaction (~60–90 seconds).
- **Pre-Indexed Catalog Durations (`data/*_full_catalog.json`)**:
  To avoid sequential `yt-dlp --dump-json` network calls when building interleaved queues across 500+ long-form episodes, durations are pre-populated in JSON catalogs and cached locally.
- **Mixin Decomposition for Live Hot-Reloading**:
  `TikTokAutoPoster` is split across 6 modules (`auto_poster.py`, `poster_browser.py`, `poster_studio_ui.py`, `poster_cleanup.py`, `poster_media_prep.py`, `poster_stream2_warmup.py`). Reloading `auto_poster` cascades `importlib.reload` across all 5 mixins and patches live daemon instances in memory.

---

## Pipeline Stages in Detail

### Stage 1: Ingestion & Perceptual Deduplication
- **Multi-Source Fetching (`downloader.py`, `crawler.py`)**: Pulls raw media and metadata via `yt-dlp` and `curl_cffi`, filtering by region (CN/VN), duration bounds, and play/like counts.
- **Duplicate Rejection**: Computes visual frame hashes (`pHash`/`vHash`) and audio fingerprints against `story_audio_history.json` and `uploaded_videos_registry.json` before queueing any download.

### Stage 2: Audio/Video Transformation
- **ASR & Localization (`transcriber.py`, `translator.py`, `tts_engine.py`, `composer.py`)**:
  - Runs local speech-to-text with `faster-whisper` (CTranslate2).
  - Translates foreign transcripts into Vietnamese scripts and generates speech via `edge-tts`.
  - Runs an FFmpeg filtergraph that ducks original source audio to `12%` (`0.12`) under the synthesized voiceover, blurs hardcoded source captions, and renders `ASS` subtitles.
- **Time-Sliced Episode Splitters (`bongcuc_splitter.py`, `gausohai_splitter.py`)**:
  - Slices 30–60 minute source videos into ~280s–300s parts (`1.42x` tempo adjustment), crops source watermarks, and composites vertical `1080x1920` layouts with downscaled boxblur backgrounds (`240x426`, `boxblur=12:2`) to keep CPU encoding times low on Intel hardware.
  - Builds interleaved queues so consecutive parts of the same episode are never dispatched back-to-back.

### Stage 3: Pre-Dispatch Validation & Sanitization
- **Technical Inspection (`stream1_antidetect_skill.py`)**: Verifies `H.264 yuv420p` video streams, `44.1kHz` AAC audio, strips macOS `com.apple.macl` extended attributes, and writes container metadata tags.
- **Policy & Metadata Guard (`community_guidelines_guard.py`)**: Filters blocked episode IDs, strips restricted terms from overlay titles and captions, and switches hashtag sets based on dispatch hour (daytime `05:00–17:59` vs. nighttime `18:00–04:59`).

### Stage 4: Scheduled Dispatch & Telemetry
- **Browser Automation (`poster_browser.py`, `poster_studio_ui.py`, `macos_desktop_controller.py`)**:
  - Runs persistent Chrome profiles (`browser_profiles/<channel>/`) with `Function.prototype.toString` guards and native macOS Quartz `CGEvent` mouse/keyboard events.
  - Waits for TikTok Studio's pre-post content check (`Kiểm tra nội dung nhanh`) before clicking post; if a warning is detected, the job aborts and moves the file to `quarantine_cg_violation/`.
- **Process Watchdog & Storage Management (`master_switch_controller.py`)**:
  - Monitors Stream 1 (`--stream-upload`) and Stream 2 (`--stream-filter24h`) PIDs and restarts dead workers automatically.
  - Prunes `workspace_temp/` files older than 2 hours and retains only the top 8 posted `.mp4` files per channel.

---

## Repository Layout

```text
tool_scan_video_tiktok/
├── main.py                                  # CLI entry point & daemon loop
├── harness.py                               # Pre-push verification suite & switch CLI
├── schedule_config.json                     # Dispatch schedules, runtime switches & triggers
├── HANDOFF.md                               # Architecture state, module map & task checklist
├── AGENT.md                                 # Regression log & invariants ([BUG-001]..[BUG-025])
├── tiktok_pipeline/
│   ├── auto_poster.py                       # Core scheduler, hot-reload hook & telemetry push
│   ├── poster_browser.py                    # Chrome context launcher, flock mutex & session sync
│   ├── poster_studio_ui.py                  # Upload form DOM interaction & pre-post check gate
│   ├── poster_cleanup.py                    # Content list deduplication & flagged post removal
│   ├── poster_media_prep.py                 # Ready-queue selector & pre-dispatch verification
│   ├── poster_stream2_warmup.py             # Stream 2 interaction loop & FYP browsing
│   ├── bongcuc_splitter.py                  # Long-form audio story splitter & FFmpeg compositor
│   ├── gausohai_splitter.py                 # Animated episode splitter & watermark cropper
│   ├── stream1_antidetect_skill.py          # 6-layer stream/metadata inspector
│   ├── community_guidelines_guard.py        # Episode blocklist & text/hashtag sanitizer
│   ├── master_switch_controller.py          # Runtime switch reader & disk garbage collector
│   ├── agy_intel_bridge.py                  # Git polling bridge & worker daemon supervisor
│   ├── crawler.py                           # Source discovery, region filter & hash deduplication
│   ├── downloader.py                        # yt-dlp / curl_cffi media fetcher
│   ├── transcriber.py                       # faster-whisper ASR wrapper
│   ├── translator.py                        # Script translation & hashtag rotation
│   ├── tts_engine.py                        # edge-tts synthesis wrapper
│   ├── composer.py                          # FFmpeg audio ducking & subtitle burn-in
│   ├── growth_24h_filter.py                 # 6-step interaction workflow for Stream 2
│   └── macos_desktop_controller.py          # macOS Quartz CoreGraphics input controller
├── data/
│   ├── bongcuc_full_catalog.json            # Indexed episode metadata & durations (147 items)
│   └── gausohai_full_catalog.json           # Indexed episode metadata (353 items)
├── output/<channel>/{ready,posted}/         # Per-channel staging and archive directories
├── remote_telemetry/                        # Worker status JSON & step screenshots
├── run_24_7.sh                              # Caffeinate wrapper for 24/7 execution
└── setup_mac_intel.sh                       # Dependency installer for Intel macOS (x86_64)
```

---

## Tech Stack

| Layer | Libraries / Tools |
| :--- | :--- |
| **Runtime & Concurrency** | Python 3.9+, `fcntl.flock` file locks, `subprocess`, Bash |
| **Media Processing** | FFmpeg (`static-ffmpeg`), OpenCV (`opencv-python`), Pillow, NumPy |
| **Speech & Audio** | `faster-whisper` (CTranslate2), `edge-tts` |
| **Ingestion & HTTP** | `yt-dlp`, `curl_cffi` |
| **Browser & OS Input** | Playwright (Chrome), macOS Quartz `CoreGraphics` (`ctypes`), AppleScript |

---

## Setup & CLI Usage

### 1. Environment Setup
```bash
git clone https://github.com/VoHoang07/tool_scan_video_tiktok.git
cd tool_scan_video_tiktok

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```
*(For Intel macOS `x86_64` worker setup, run `./setup_mac_intel.sh`).*

### 2. Pre-Push Verification (`harness.py`)
`harness.py` runs 4 checks in ~1 second: bytecode compilation across all `.py` files, `schedule_config.json` schema validation, `TikTokAutoPoster` MRO/method signature parity (59 methods across 6 Mixins), and catalog integrity.
```bash
./venv/bin/python harness.py --verify
```

### 3. Runtime Switches & Telemetry Inspection
```bash
# Print current switch states and latest worker telemetry
./venv/bin/python harness.py --status

# Toggle runtime switches in schedule_config.json and push to origin/main
./venv/bin/python harness.py --switch 2 on --push
./venv/bin/python harness.py --switch 3 off --push
```

### 4. Running the Daemons
```bash
# Authenticate a Chrome profile once (persisted in browser_profiles/<channel>/)
./venv/bin/python main.py --login-channel audio_dem_khuyen

# Start Stream 1 (Ingestion, FFmpeg preparation & scheduled dispatch)
./venv/bin/python main.py --stream-upload

# Start Stream 2 (Scheduled interaction & profile warmup)
./venv/bin/python main.py --stream-filter24h

# Trigger an immediate single-channel dispatch
./venv/bin/python main.py --post-now audio_dem_khuyen
```
