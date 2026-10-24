# 🎬 TikTok & Douyin Multi-Channel Automated Pipeline

An end-to-end automated system for downloading, processing, localizing, and publishing short-form videos from TikTok and Douyin across **4 Specialized Channels**:

- **Direct & Smart Extraction:** Automatically fetches all videos from your seed video list (`seed_video_urls`), full creator profiles (`seed_channels`), and related videos sharing the same niche, hashtags, or original audio.
- **Language-Aware Processing:**
  - **Existing Vietnamese Videos (`vi`):** Retains 100% of the original watermark-free video, original audio, and original caption/title (no script rewriting, no voiceover dubbing).
  - **Chinese / Foreign Language Videos (`zh`, etc.):** Translates dialogue directly into Vietnamese, generates AI voiceover via `edge-tts`, ducks original audio down to **12% (`0.12`)** to preserve background music and sound effects (SFX), blurs hardcoded foreign subtitles, and burns in styled Vietnamese subtitles.

---

## 1. Video Management Directory Structure (`Ready` vs. `Posted`)

```text
output/
├── video_AI_hay/
│   ├── ready/                 # READY-TO-POST videos for Channel 1 (AI Animation / Pets / AI Short Films)
│   └── posted/                # POSTED videos for Channel 1
├── baby_AI/
│   ├── ready/                 # READY-TO-POST videos for Channel 2 (Lifestyle / Family Comedy)
│   └── posted/                # POSTED videos for Channel 2
├── audio_dem_khuyen/
│   ├── ready/                 # READY-TO-POST videos for Channel 3 (Romance Audio Stories & Short Dramas)
│   └── posted/                # POSTED videos for Channel 3
├── tu_chan_gioi/
│   ├── ready/                 # READY-TO-POST videos for Channel 4 (AI Cultivation / Xianxia / Fantasy)
│   └── posted/                # POSTED videos for Channel 4
└── danh_sach_video.csv        # Centralized tracking sheet (channel, filename, source_url, caption, hashtags, status, posted_at)
```

---

## 2. Managing & Auto-Syncing `READY` and `POSTED` States

The system supports **4 automated synchronization methods** across all 4 channels (`video_AI_hay`, `baby_AI`, `audio_dem_khuyen`, `tu_chan_gioi`):

1. **Auto-Detection via macOS Browser File Upload Dialog:**
   When you select a video inside `output/<channel_name>/ready/` from your browser's file upload dialog (Chrome, Safari, Brave, or Arc) to upload to TikTok, the background watcher (`--watch-sync`, `--status`, or `--continuous`) automatically detects the upload event, moves the video to `output/<channel_name>/posted/`, and updates `status=POSTED` in `output/danh_sach_video.csv`.

2. **Manual Marking via File Path, Filename, or Video ID:**
   ```bash
   ./venv/bin/python main.py --mark-posted "/Users/vohoang/Documents/tool_scan_video_tiktok/output/audio_dem_khuyen/ready/20261022_232340_tiktok_story_7682055125342424341.mp4"

   # Or simply pass the video ID:
   ./venv/bin/python main.py --mark-posted 7682055125342424341
   ```

3. **Two-Way Sync via `output/danh_sach_video.csv` or Finder Drag-and-Drop:**
   - **CSV to Finder:** Change the `status` column to `POSTED` (or `x`, `1`, `done`) in the CSV file $\rightarrow$ the system automatically moves the corresponding `.mp4` file from `ready/` to `posted/`.
   - **Finder to CSV:** Drag and drop any `.mp4` file from `ready/` into `posted/` in macOS Finder $\rightarrow$ the system automatically updates its CSV status to `POSTED`.

4. **Inventory Check & Background Sync Commands:**
   ```bash
   # Sync immediately and display the 4-channel inventory summary table
   ./venv/bin/python main.py --status

   # Run a background watcher that auto-moves videos to posted/ right after you select them for TikTok upload
   ./venv/bin/python main.py --watch-sync
   ```

---

## 3. Crawling Commands by Channel or Direct URL

```bash
# Crawl by channel (automatically pulls from seed URLs -> full seed channels -> related videos)
./venv/bin/python main.py --channel 1 --limit 3
./venv/bin/python main.py --channel 2 --limit 3
./venv/bin/python main.py --channel 3 --limit 5
./venv/bin/python main.py --channel 4 --limit 3

# Feed a single TikTok/Douyin video URL or a full creator profile URL into a specific channel
./venv/bin/python main.py --url "[https://www.tiktok.com/@tieutin2000/video/7682055125342424341](https://www.tiktok.com/@tieutin2000/video/7682055125342424341)" --channel 3
./venv/bin/python main.py --url "[https://www.tiktok.com/@tieutin2000](https://www.tiktok.com/@tieutin2000)" --channel 3 --limit 5
```

---

## 4. 24/7 Unattended Mode on Intel / Apple Silicon MacBooks (Auto-Crawl & Scheduled Auto-Post)

### Step 1: Setup on an Intel MacBook (`x86_64`)
Copy the entire `tool_scan_video_tiktok` directory to your Intel MacBook, open Terminal in the project folder, and run:

```bash
chmod +x setup_mac_intel.sh run_24_7.sh
./setup_mac_intel.sh
```
*(This script automatically rebuilds a native Intel `x86_64` virtual environment and installs `ffmpeg`, `faster-whisper`, and `playwright`).*

### Step 2: Log In to Your 4 TikTok Accounts (One-Time Setup)
Each command below launches an isolated Chrome instance for the corresponding channel. Simply scan the QR code with your phone to log into TikTok and close the browser window (the session is saved persistently in `browser_profiles/<channel_name>/`):

```bash
./venv/bin/python main.py --login-channel video_AI_hay
./venv/bin/python main.py --login-channel baby_AI
./venv/bin/python main.py --login-channel audio_dem_khuyen
./venv/bin/python main.py --login-channel tu_chan_gioi
```

### Step 3: Configure Daily Auto-Post Schedules
Set your daily posting times using `--set-schedule` (the system automatically staggers each channel by 10 minutes so your Intel Mac never has to open 4 browser instances simultaneously), or edit `schedule_config.json` directly:

```bash
# Example: Schedule 3 posting slots per day at 08:00 AM, 12:30 PM, and 08:00 PM
./venv/bin/python main.py --set-schedule "08:00,12:30,20:00"

# Or test immediate auto-posting for 1 ready video on a specific channel:
./venv/bin/python main.py --post-now video_AI_hay
```

### Step 4: Launch 24/7 Autonomous Mode (`./run_24_7.sh`)

```bash
./run_24_7.sh
```

- **Anti-Sleep Protection:** Built-in `caffeinate -dimsu` integration prevents the Intel MacBook from sleeping even when the display turns off.
- **Continuous Queue Replenishment:** Automatically rotates through all 4 channels to crawl, process, and maintain a buffer of **5 ready videos per channel** in `ready/`.
- **Hands-Free Scheduled Publishing:** At each scheduled time in `schedule_config.json`, the system launches the channel's dedicated browser profile, grabs the next video from `ready/` along with its caption and hashtags from `output/danh_sach_video.csv`, publishes it to TikTok, and automatically moves the file to `posted/`.
