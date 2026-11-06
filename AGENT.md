# 🧬 AGENT.md — Sổ Tay Tự Tiến Hóa & Miễn Dịch Lỗi (Self-Evolving Bug Memory)

> **MỤC ĐÍCH TỐI THƯỢNG**: File này là bộ nhớ tiến hóa vĩnh viễn của hệ thống AI Agent (trên cả **Mac Silicon `arm64`** và **Mac Intel `x86_64`**).
> - **Trước khi sửa code hoặc viết tính năng mới**: Agent BẮT BUỘC đọc file `AGENT.md` này để kiểm tra các **Luật Miễn Dịch (Immunity Rules)**, tuyệt đối không tái phạm các lỗi cũ.
> - **Ngay sau khi phát hiện và xử lý xong bất kỳ bug nào**: Agent (hoặc cơ chế Tự Tiến Hóa `_auto_self_evolve_on_log_errors` trong `tiktok_pipeline/agy_intel_bridge.py`) BẮT BUỘC ghi nối tiếp (append) bug đó vào mục **Danh Sách Lỗi Đã Tiến Hóa** bên dưới kèm **Luật Miễn Dịch Vĩnh Viễn**.

---

## 🛡️ 1. CÁC NGUYÊN TẮC BẤT BIẾN (CORE IMMUNITY INVARIANTS)

1. **Không bao giờ gọi I/O mạng không có Timeout**:
   - Mọi lệnh gọi WebSocket / HTTP / Subprocess (`edge-tts`, `yt-dlp`, `ffmpeg`, `playwright`, `urllib`, `requests`) bắt buộc phải có `timeout` hoặc `asyncio.wait_for(..., timeout=...)`.
2. **Không bao giờ khởi chạy tiến trình ngầm mà để hở `stdin`**:
   - Mọi lệnh `subprocess.Popen` hoặc `nohup ... &` chạy ngầm phải luôn có `</dev/null` và `stdin=subprocess.DEVNULL` để tránh lỗi `OSError: [Errno 9] Bad file descriptor` trong `init_sys_streams` của Python.
3. **Không bao giờ mở trình duyệt Chrome mà không qua khóa liên tiến trình (`Browser Process Lock`)**:
   - Cả Dòng 1 (`--stream-upload`) và Dòng 2 (`--stream-filter24h`) phải khóa `workspace_temp/.tiktok_browser.lock` và dọn sạch `SingletonLock` mồ côi trước khi mở `launch_persistent_context`.
4. **Không bao giờ để tiến trình treo im lặng (Silent Hang)**:
   - Cầu nối `tiktok_pipeline/agy_intel_bridge.py` giám sát thời gian cập nhật (`mtime`) của `logs/daemon_24_7.log` và `logs/stream2_filter24h.log`. Nếu đứng im quá ngưỡng cho phép (20 phút), Watchdog tự động diệt tiến trình bị đơ và khởi động lại.
5. **Không bao giờ cắt mất người hoặc hạ độ phân giải nền kênh `audio_dem_khuyen`**:
   - Luôn tải nguồn `1920x1080` Full HD bằng `.bin/yt-dlp_macos` và dựng khung bằng `scale=w=1056:h=720:force_original_aspect_ratio=decrease:flags=lanczos` (giữ nguyên toàn bộ khung hình và người trong video, `-crf 19`).
6. **Đồng bộ Git 2 chiều chống xung đột tuyệt đối**:
   - Luôn dùng `git pull --rebase --autostash origin main` trước khi `git push origin main`.

---

## 📚 2. DANH SÁCH LỖI ĐÃ TIẾN HÓA (EVOLVED BUG LEDGER)

### [BUG-001] `OSError: [Errno 9] Bad file descriptor` khi khởi chạy Daemon ngầm
- **Thời gian**: 2026-11-04
- **Triệu chứng**: Tiến trình Dòng 1 (`--stream-upload`) hoặc Dòng 2 (`--stream-filter24h`) vừa được gọi dậy qua `subprocess.Popen` / `nohup` thì chết ngay lập tức với `Fatal Python error: init_sys_streams: <stdin> is a directory, cannot continue / OSError: [Errno 9] Bad file descriptor`.
- **Nguyên nhân gốc rễ**: Khi tiến trình cha chạy dưới dạng daemon không gắn với TTY, descriptor `0` (`stdin`) bị đóng hoặc không hợp lệ; tiến trình con Python kế thừa descriptor lỗi này khi khởi tạo `sys.stdin`.
- **Cách khắc phục & File đã sửa**:
  - Thêm `</dev/null` vào chuỗi lệnh shell và `stdin=subprocess.DEVNULL` trong [`tiktok_pipeline/agy_intel_bridge.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/agy_intel_bridge.py) và [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py).
- **Luật Miễn Dịch Vĩnh Viễn**: Mọi lệnh spawn tiến trình ngầm trong toàn bộ dự án bắt buộc phải có `stdin=subprocess.DEVNULL` và `</dev/null`.

### [BUG-002] Treo cứng Dòng 1 (`--stream-upload`) vô thời hạn ở bước `edge-tts`
- **Thời gian**: 2026-11-05
- **Triệu chứng**: Tiến trình `main.py --stream-upload` vẫn hiển thị `stream1_running: true` trong `ps aux`, nhưng log `logs/daemon_24_7.log` đứng im suốt nhiều giờ sau dòng `TTS #09`, không chuyển sang bước tiếp theo và không đăng bài.
- **Nguyên nhân gốc rễ**: Hàm `await communicate.save(str(output_path))` của thư viện `edge-tts` mở kết nối WebSocket tới máy chủ Microsoft nhưng không có `timeout` mặc định; khi mạng chập chờn làm WebSocket treo lơ lửng (half-open connection), coroutine chờ vĩnh viễn.
- **Cách khắc phục & File đã sửa**:
  - Bọc `await asyncio.wait_for(communicate.save(str(output_path)), timeout=25.0)` trong [`tiktok_pipeline/tts_engine.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/tts_engine.py).
  - Thêm **Stale-Log Watchdog (1200s = 20 phút)** trong `_ensure_both_streams_alive()` của [`tiktok_pipeline/agy_intel_bridge.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/agy_intel_bridge.py) để tự động `pkill -9` và bật lại Dòng 1 nếu log đứng im quá 20 phút.
- **Luật Miễn Dịch Vĩnh Viễn**: Không bao giờ gọi `await` tới dịch vụ mạng bên ngoài mà thiếu `asyncio.wait_for(..., timeout=...)`; luôn duy trì Stale-Log Watchdog cho cả 2 dòng hoạt động.

### [BUG-003] Video nền kênh `audio_dem_khuyen` mờ (360p) và bị cắt mất người
- **Thời gian**: 2026-11-04
- **Triệu chứng**: Video xuất ra cho kênh `audio_dem_khuyen` có nền phía sau bị rỗ/mờ và khung hình bị zoom cắt sát làm mất hình người.
- **Nguyên nhân gốc rễ**:
  1. Bản `yt-dlp` cũ trong `venv` bị YouTube giới hạn xuống format `18` (`640x360`).
  2. Bộ lọc FFmpeg cũ dùng `crop=w='min(iw,ih*9/16)':h=ih` cắt mất hơn 68% chiều ngang của video ngang 16:9.
  3. Nguồn video ASMR cũ quay cận cảnh cằm/miệng nên không thấy toàn thân/nửa thân người.
- **Cách khắc phục & File đã sửa**:
  - Ưu tiên dùng `.bin/yt-dlp_macos` (hỗ trợ `visionos`/`ios` client tải Full HD `1920x1080` format `137/299`) và danh sách nguồn Mukbang có hình người rõ nét (`Eat with Boki`, `SULGI`, `GONGSAM TABLE`) trong [`tiktok_pipeline/bongcuc_splitter.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/bongcuc_splitter.py).
  - Chuyển sang layout `bongcuc_v4` giữ nguyên khung hình gốc bằng `scale=w=1056:h=720:force_original_aspect_ratio=decrease:flags=lanczos` và `-crf 19`.
- **Luật Miễn Dịch Vĩnh Viễn**: Tuyệt đối không dùng center-crop `ih*9/16` lên nguồn video ngang của `audio_dem_khuyen`; luôn kiểm tra chiều cao video tải về `>= 720p` trước khi đưa vào `bongcuc_bg_pool`.

### [BUG-004] Kẹt khóa `SingletonLock` của Chrome làm Playwright không mở được kênh
- **Thời gian**: 2026-10-31
- **Triệu chứng**: Khi mở trình duyệt cho kênh TikTok, Playwright báo lỗi `Profile is already in use by another Google Chrome process` hoặc treo đến hết `timeout` dù phiên trước đã kết thúc.
- **Nguyên nhân gốc rễ**: Khi Chrome for Testing bị đóng cưỡng bức bởi Watchdog hoặc crash, các file khóa `SingletonLock`, `SingletonCookie`, `SingletonSocket` trong `browser_profiles/<channel>/` không được xóa.
- **Cách khắc phục & File đã sửa**:
  - Tự động kiểm tra và dọn sạch `SingletonLock` / `SingletonCookie` / `SingletonSocket` trước và sau mỗi phiên trong `_cleanup_stale_chrome_profile_locks` của [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py).
- **Luật Miễn Dịch Vĩnh Viễn**: Luôn gọi `_cleanup_stale_chrome_profile_locks(user_data_dir)` trong khối `try...finally` của mọi hàm mở trình duyệt.

### [BUG-005] Chạy trùng nhiều tiến trình Dòng 1 hoặc Dòng 2 gây tranh chấp tài nguyên
- **Thời gian**: 2026-11-01
- **Triệu chứng**: Trên máy Mac Intel xuất hiện 2–3 tiến trình `main.py --stream-upload` hoặc `--stream-filter24h` chạy song song, ghi đè log và tranh giành trình duyệt.
- **Nguyên nhân gốc rễ**: Lệnh khởi động thủ công chồng lên lệnh tự phục hồi của cầu nối mà không kiểm tra/dọn tiến trình trùng trước đó.
- **Cách khắc phục & File đã sửa**:
  - Bổ sung `_deduplicate_stream_processes()` trong [`tiktok_pipeline/agy_intel_bridge.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/agy_intel_bridge.py) chạy mỗi 20 giây để giữ lại đúng 1 PID mới nhất cho mỗi dòng và tắt toàn bộ bản trùng lặp.
- **Luật Miễn Dịch Vĩnh Viễn**: Trước mọi thao tác kiểm tra sức khỏe hoặc khởi động lại stream trên máy Intel, bắt buộc chạy `_deduplicate_stream_processes()` trước tiên.

### [BUG-006] Lồng tiếng Edge-TTS đọc quá chậm (như slow-motion `-25%..-42%` và `atempo 0.55x`)
- **Thời gian**: 2026-11-05 18:05:00
- **Triệu chứng**: Các video lồng tiếng Việt có tốc độ giọng đọc lê thê, thiếu tự nhiên (`rate=-25%..-42%`, `atempo < 1.0`).
- **Nguyên nhân gốc rễ**:
  1. `CHANNEL_VOICE_PROFILES` trong [`tiktok_pipeline/composer.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/composer.py) để `base_rate: "+0%"` và hàm `_synthesize_all_async` trong [`tiktok_pipeline/tts_engine.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/tts_engine.py) gán cứng `rate_str = "+0%"` ở bước 1.
  2. Khi câu dịch tiếng Việt ngắn hơn khoảng thời gian segment của Whisper (`ratio < 0.90`), `tts_engine.py` ép giảm tốc độ phát âm xuống `-10% .. -42%` và dùng `atempo` kéo giãn xuống `0.55x` để lấp đầy toàn bộ khoảng lặng của câu gốc.
- **Cách khắc phục & File đã sửa**:
  - Nâng `base_rate` mặc định của toàn bộ kênh trong [`tiktok_pipeline/composer.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/composer.py) lên `+15%..+18%` và khóa sàn `self.base_pct = max(15, raw_pct)` trong [`tiktok_pipeline/tts_engine.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/tts_engine.py).
  - Xóa bỏ hoàn toàn nhánh làm chậm `if ratio < 0.90:` và khóa cứng `residual_factor = max(1.0, min(1.60, needed_factor))` (cùng `clamped = max(1.0, ...)` trong `_finalize_segment_audio`).
- **Luật Miễn Dịch Vĩnh Viễn**: Tuyệt đối không bao giờ truyền `rate < +15%` (hoặc số âm `-XX%`) vào `edge-tts` và không bao giờ dùng `atempo < 1.0`. Nếu câu thoại ngắn hơn khung thời gian gốc, giữ nguyên tốc độ chuẩn và để khoảng nghỉ tự nhiên trước câu kế tiếp.

### [BUG-007] Đăng trùng video trên một số kênh (`an_vat_moi_ngay`, `suc_khoe_duong_sinh`, `phu_nu_khi_chat`, `audio_dem_khuyen`)
- **Thời gian**: 2026-11-05 18:05:00
- **Triệu chứng**: Cùng một video được cào và đăng lại lần 2 lên kênh TikTok sau vài giờ hoặc ngày hôm sau.
- **Nguyên nhân gốc rễ**:
  1. Hàm `_save_and_push_telemetry` trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py) chạy `git checkout -f -B main origin/main` mỗi 3 phút mà không sao lưu/hợp nhất `story_audio_history.json` và không push `story_audio_history.json` lên GitHub, làm mất các `video_id` vừa cào trên máy Intel.
  2. Biểu thức chính quy `r"(?:tiktok|douyin)_story_(\d+)"` ở 6 vị trí trong `auto_poster.py`, `crawler.py`, `main.py` bỏ sót tiền tố `youtube_shorts_story_([A-Za-z0-9_-]+)` khiến ID của YouTube Shorts trả về rỗng (`""`) khi kiểm tra chống trùng.
  3. Cả Dòng 1 và Dòng 2 cùng gọi `check_and_run_stream1_upload_schedule()` khi có `remote_trigger_post_now`.
- **Cách khắc phục & File đã sửa**:
  - Đồng nhất regex đa nền tảng `r"(?:tiktok|douyin|youtube_shorts|facebook_reels|instagram_reels|bilibili|xiaohongshu)_story_([A-Za-z0-9_-]+)"` tại mọi điểm trích xuất ID trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py), [`tiktok_pipeline/crawler.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/crawler.py), và [`tiktok_pipeline/main.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/main.py).
  - Sao lưu, hợp nhất (union-merge) và `git add story_audio_history.json` ngay trong `_save_and_push_telemetry()` và `_auto_git_pull_if_available()`, đồng thời nạp toàn bộ 131 ID lịch sử vào [`story_audio_history.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/story_audio_history.json).
  - Thêm hàm `_deduplicate_existing_posts_in_content_page()` trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py) để tự động phát hiện và xóa ngay các bài đăng bị trùng trên bảng `tiktokstudio/content`.
- **Luật Miễn Dịch Vĩnh Viễn**: Mọi lệnh `git checkout -f` trên máy Intel bắt buộc phải union-merge và push `story_audio_history.json`; mọi regex trích xuất `story_id` bắt buộc hỗ trợ đầy đủ cả 7 nền tảng (`[A-Za-z0-9_-]+`).

### [BUG-008] Kênh `audio_dem_khuyen` lỡ lịch đăng buổi tối do click trượt nút `Đăng` + `auto_sync_posted_videos` xóa nhầm file đang upload + khóa cứng khung giờ
- **Thời gian**: 2026-11-06 01:05:00
- **Triệu chứng**: Phiên đăng `audio_dem_khuyen` lúc `18:56` tải video `20261105_185144_tiktok_story_990004501_bongcuc_v4_s45_p1_bg_mukbang.mp4` đạt 100% nhưng nút đỏ `Đăng` không ăn click (`post_confirmed = False`), sau đó báo `FileNotFoundError` ở `mark_video_posted` và bỏ qua luôn khung giờ `21:15`.
- **Nguyên nhân gốc rễ**:
  1. `upload_one_channel_video` trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py) chỉ gọi `_os_click_viewport` (tọa độ màn hình OS) mà không gọi kèm `page.mouse.click` + DOM `b.click()`, nên khi cửa sổ Chrome trên Mac Intel bị lệch/mất focus thì nút `Đăng` không ăn click.
  2. Dù `post_confirmed == False`, code vẫn gọi `_record_uploaded_video_in_registry` và giữ lại timestamp đặt chỗ trong `posted_schedule_history.json`, làm `_was_channel_uploaded_recently(min_interval_minutes=180.0)` tưởng kênh vừa đăng lúc `18:57` nên chặn luôn khung `21:15`.
  3. `auto_sync_posted_videos(min_age_seconds=35.0)` trong [`tiktok_pipeline/crawler.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/crawler.py) thấy cờ `com.apple.macl` sau 35 giây liền di chuyển file đang upload sang `posted/` rồi gọi `run_switch_4_clear()` xóa mất file mới vì điểm viral mặc định thấp hơn 8 video seed cũ (`5_000_000`).
  4. Thời gian render on-demand (4–5 phút) cộng dồn vào `.tiktok_browser_session.lock` làm Watchdog tưởng Chrome treo.
- **Cách khắc phục & File đã sửa**:
  - Click nút `Đăng` (`post_video_button`) đa tầng (`_os_click_viewport` + `page.mouse.click` + DOM `b.click()` + `locator.click(force=True)`), và nếu `post_confirmed == False` khi vẫn ở trang upload thì raise `RuntimeError` trước khi ghi vào `uploaded_videos_registry.json`, đồng thời khôi phục `slot_key` / `last_upload_ts` trong `except` của [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py).
  - Tăng `min_age_seconds` của `auto_sync_posted_videos` lên `900.0` giây, không di chuyển file khi `.tiktok_browser_session.lock` đang bận, xử lý êm `mark_video_posted` trong [`tiktok_pipeline/crawler.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/crawler.py), và ưu tiên giữ video mới/<1h hoặc `bongcuc_v4_` (`score >= 6_000_000`) trong [`tiktok_pipeline/master_switch_controller.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/master_switch_controller.py).
  - Bổ sung `_refresh_browser_process_lock()` sau khi render và nâng ngưỡng Watchdog lên `720s` trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py) & [`tiktok_pipeline/agy_intel_bridge.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/agy_intel_bridge.py).
- **Luật Miễn Dịch Vĩnh Viễn**: Tuyệt đối không ghi nhận `uploaded_videos_registry.json` hoặc giữ `slot_key` khi `post_confirmed == False`; không bao giờ chạy `auto_sync_posted_videos` trên file mới dưới 15 phút hoặc khi trình duyệt đang giữ lock; luôn click nút `Đăng` bằng cả OS click lẫn DOM `.click()`.

### [BUG-009] Đăng trùng video & trùng lặp Caption mẫu trên nhiều kênh do thứ tự ghi `danh_sach_video.csv`, thiếu catalog 148 tập Bông Cúc, Watchdog ngắt giữa chừng và tọa độ cứng nút `...`
- **Thời gian**: 2026-11-06 13:23:00
- **Triệu chứng**:
  1. Một số bài đăng trên `phu_nu_khi_chat` (`00:28`, `00:18`), `an_vat_moi_ngay` (`02:19`, `02:11`), và `suc_khoe_duong_sinh` (`02:52`) vẫn còn bản trùng lặp trên trang `tiktokstudio/content` chưa được xóa.
  2. Các bài đăng mới trên `audio_dem_khuyen`, `suc_khoe_duong_sinh`, `an_vat_moi_ngay`, `phu_nu_khi_chat` bị lặp lại cùng một câu caption mẫu chung chung thay vì tiêu đề thực tế của từng video.
- **Nguyên nhân gốc rễ**:
  1. `append_to_master_csv()` trong [`tiktok_pipeline/crawler.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/crawler.py) gọi `self._upgrade_master_csv_schema()` **trước khi** ghi dòng caption thực tế vào `danh_sach_video.csv`. Khi file `.mp4` vừa xuất ra `ready/`, `_upgrade_master_csv_schema()` quét thấy file chưa có trong CSV nên tự chèn 1 dòng caption fallback chung chung lên trước, rồi `append_to_master_csv()` mới chèn dòng caption thật ở bên dưới. Sau đó `_get_next_ready_video_for_channel()` trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py) đọc CSV từ trên xuống và `break` ngay ở dòng đầu tiên (dòng fallback chung chung).
  2. `recover_contextual_caption_and_hashtags()` chỉ tìm trong `BONGCUC_EPISODES_CATALOG` (17 tập) thay vì `splitter.get_full_catalog()` (148 tập) và dùng `video_id[-4:].isdigit()` (luôn bằng `0` với ID chữ-số của YouTube Shorts).
  3. Khi kho `ready/` trống phải cào/render on-demand mất 10–12 phút trong lúc đang giữ `.tiktok_browser_session.lock`, `agy_intel_bridge.py` (`lock_age > 720`) tự động kill Chrome đúng lúc vừa bấm nút `Đăng`, làm `upload_one_channel_video` văng lỗi trước khi gọi `mark_video_posted` và `check_and_run_stream1_upload_schedule` xóa `slot_key` rồi đăng lại chính video đó lần 2.
  4. `_deduplicate_existing_posts_in_content_page()` gán cứng `r.left >= 1050` cho nút `...` (trên màn hình `1024px` của `suc_khoe_duong_sinh` nút `...` nằm ở `r.left ~ 940..1020` nên không tìm thấy nút xóa) và không lọc bỏ chuỗi ngày tháng (`"6 tháng 11, 9:30 SA"`).
- **Cách khắc phục & File đã sửa**:
  - Sửa `append_to_master_csv()` và `_upgrade_master_csv_schema()` trong [`tiktok_pipeline/crawler.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/crawler.py): ghi/cập nhật dòng caption thực tế trước khi nâng cấp schema, khử trùng lặp dòng theo `basename` (ưu tiên giữ caption thực tế thay vì fallback), tra cứu đủ 148 tập qua `splitter.get_full_catalog()`, đọc `pipeline_summary.json` / `segments_translated.json` trong `workspace_temp/`, và dùng băm `md5` thay cho `video_id[-4:].isdigit()`.
  - Sửa `_deduplicate_existing_posts_in_content_page()`, `delete_channel_posts_by_keywords()`, `_get_next_ready_video_for_channel()`, và `upload_one_channel_video()` trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py): định vị `dur`, `caption`, `views`, `moreX/moreY` tương đối theo vị trí nút Quyền riêng tư (`pb.left`, `pb.right`), lọc chuỗi ngày tháng, tự động ưu tiên xóa bản mang caption fallback chung chung và giữ bản có caption gốc nhiều view, đồng thời ghi nhận ngay video đã đăng nếu trình duyệt đóng sau khi `post_btn_clicked = True`.
  - Tăng ngưỡng `auto_heal_stuck_browser_lock` lên `1500s` và không bao giờ kill lock khi `ffmpeg` hoặc `yt-dlp` đang chạy trong [`tiktok_pipeline/agy_intel_bridge.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/agy_intel_bridge.py).
- **Luật Miễn Dịch Vĩnh Viễn**: Tuyệt đối không bao giờ gọi `_upgrade_master_csv_schema()` trước khi ghi dòng metadata thực tế vào `danh_sach_video.csv`; mọi truy vấn bảng `tiktokstudio/content` bắt buộc định vị tương đối theo cột Quyền riêng tư (`pb.left`, `pb.right`) thay vì gán cứng tọa độ ngang `px`; một khi đã click nút đỏ `Đăng` (`post_btn_clicked = True`) mà trình duyệt bị đóng thì bắt buộc đánh dấu video đã đăng để không bao giờ đăng lặp lần 2.

### [BUG-010] Bộ lọc 24h bị TikTok rollback lượt Follow (`0 Đang follow` trên điện thoại) do `time.sleep` chặn event loop Playwright kích hoạt `btn.click()` (`isTrusted: false`) & lỗi double-scroll / pause trên FYP
- **Thời gian**: 2026-11-06 14:55:00
- **Triệu chứng**:
  1. Dòng 2 (`filter24h`) báo `persisted: True` trong log nhưng khi mở điện thoại tìm các kênh thì vẫn thấy `0 Đang follow`, và ngay ở Video #2 trong cùng phiên thì TikTok trả về `"is_digg": 1` rồi khóa kênh vào `channel_cooldown_until` 4 giờ.
  2. Khi lướt `FYP` (`warmup_fyp`), hàm `_focus_and_play_tiktok_video` click thẳng vào giữa video đang phát làm video bị tạm dừng (pause), `_scroll_to_next_fyp_video` gọi cả `mouse.wheel` lẫn `ArrowDown` làm nhảy cóc video, không bỏ qua video Quảng cáo (`Được tài trợ`) dài hàng giờ, và chụp ảnh telemetry ngay sau khi vừa cuộn sang video mới ở giây `00:01`.
- **Nguyên nhân gốc rễ**:
  1. Trong `_verify_and_follow_on_creator_profile()` của [`tiktok_pipeline/growth_24h_filter.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/growth_24h_filter.py), sau khi bấm chuột phần cứng `self._os_click_viewport(page, bx, by)` (`isTrusted: true`), code gọi `time.sleep(2.2)` (chặn đứng luồng Python, không bơm greenlet event loop của Playwright nên callback `_on_response` chưa nhận được phản hồi `/commit/follow/user/`). Vì vậy điều kiện `len(trace.get("follow_responses")) <= before_f_cnt` luôn đúng 100%, khiến code gọi tiếp `btn.click()` trong `page.evaluate` (`isTrusted: false`, `screenX=0, clientX=0`) đúng 2 giây sau cú click thật (và `a.click()` ở dòng 1593 khi tab `Bình luận` chưa mở). Hệ thống chống bot `webmssdk.js` / `Shark` của TikTok phát hiện chuỗi click tổng hợp `isTrusted: false` liền thu hồi (rollback) ngầm lượt Follow trên máy chủ và đánh cờ `"is_digg": 1` ở video kế tiếp.
  2. `run_session_on_page()` mở thẳng URL video mục tiêu từ `about:blank` mà chưa làm ấm phiên `webmssdk.js` / `msToken` trên trang FYP, và khi gặp `is_digg: 1` (chỉ giới hạn Like) lại ngắt luôn phiên và bỏ qua `_capture_own_profile_stats()`.
  3. Trên FYP trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py), click vào tâm video `(vid_x, vid_y)` khi video đang tự chạy gây pause video; gọi đồng thời `page.mouse.wheel` + `ArrowDown` cách nhau 0.25s gây cuộn kép.
- **Cách khắc phục & File đã sửa**:
  - Xóa bỏ hoàn toàn mọi lệnh `.click()` tổng hợp JS (`a.click()`, `btn.click()`) và thay toàn bộ `time.sleep(...)` sau thao tác tương tác bằng `page.wait_for_timeout(...)` trong [`tiktok_pipeline/growth_24h_filter.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/growth_24h_filter.py); luôn mở tab `Bình luận` sau Bước 1, làm ấm phiên trên `https://www.tiktok.com/foryou?lang=vi-VN` đầu mỗi phiên Bộ lọc 24h, thực hiện đủ 6 bước trên video mục tiêu, nán lại cuộn trang Hồ sơ chủ kênh `3.6–5.2s` sau khi bấm Follow phần cứng 1 lần duy nhất, tách riêng cờ giới hạn Like (`_session_digg_rate_limited`) với cờ giới hạn Follow (`_follow_circuit_breaker_tripped`), và luôn chụp số liệu `domFollowing` thực tế ở cuối phiên.
  - Sửa `_focus_and_play_tiktok_video()`, `_scroll_to_next_fyp_video()`, và `warmup_browse_channel()` trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py): click vào vùng nền trung tính `(295, 420)` để đóng thanh địa chỉ mà không pause video, chuyển video bằng `ArrowDown` đơn lẻ (chỉ dùng `mouse.wheel` khi video chưa đổi), tự động lướt qua nhanh video Quảng cáo (`Được tài trợ`), kết hợp thả tim & bấm Follow phần cứng tự nhiên ngay trên FYP, và chụp ảnh telemetry trước khi cuộn sang video mới.
- **Luật Miễn Dịch Vĩnh Viễn**: TUYỆT ĐỐI CẤM dùng `.click()` tổng hợp trong `page.evaluate` (`isTrusted: false`) trên bất kỳ nút hay thẻ `<a>` nào của TikTok Web (`Follow`, `Like`, `Bookmark`, `Comment`, `Profile Link`); luôn dùng `page.wait_for_timeout(...)` thay cho `time.sleep(...)` khi chờ phản hồi mạng hoặc cập nhật DOM trên Playwright; không bao giờ click vào tâm video đang phát trừ khi `v.paused === true`.

### [BUG-011] Kênh `baby_AI` lọt video lạc đề (người lớn/iPhone Live Photo/phim Shorts) & Dòng 1 (`stream1_upload`) chết ngầm do `OSError: [Errno 9] Bad file descriptor` khi khởi chạy lại qua `nohup`
- **Thời gian**: 2026-11-06 23:38:00
- **Triệu chứng**:
  1. Trên kênh `baby_AI` xuất hiện 2 video không phải em bé AI (`20261105_183812_douyin_story_7581027567194298984.mp4` — `"Có vấn đề gì với việc chọn ảnh Live của iPhone không?"` và `20261102_124818_youtube_shorts_story_ChBS_r_mC5Y.mp4`), trong khi các video chuẩn của kênh đều là em bé AI dễ thương/hài hước (`#AI萌娃`, `小胖`, `大胖`, `大头菠萝`, `@andy76243`, `@lucas86868686`, `@funybaby199`, `@waikityaw`, `@jean131419`, `@aamm14114pf`).
  2. Dòng 1 (`main.py --stream-upload`) trên Mac Intel bị dừng (`stream1_running: false`) với lỗi `Fatal Python error: init_sys_streams: can't initialize sys standard streams / OSError: [Errno 9] Bad file descriptor` trong `logs/daemon_24_7.log`.
- **Nguyên nhân gốc rễ**:
  1. `is_valid_baby_ai_topic()` trong [`tiktok_pipeline/crawler.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/crawler.py) chứa các từ đơn quá rộng (`"宝"` khớp `"宝子们"`/`"淘宝"`, `"孩"` khớp `"女孩子"`, `"姐姐"`/`"哥哥"` khớp `"小姐姐"`/`"小哥哥"`) khiến video hướng dẫn chọn ảnh Live iPhone của người lớn vượt qua bộ lọc. Đồng thời `channel_2_life_comedy` bật `multi_platform_keywords` nhưng `_discover_multi_platform_shorts()` không kiểm tra `is_valid_baby_ai_topic()`, làm lọt trích đoạn phim từ YouTube Shorts.
  2. `_ensure_both_streams_alive()` trong [`tiktok_pipeline/agy_intel_bridge.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/agy_intel_bridge.py) và `_auto_revive_stream1_upload_if_dead()` trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py) gọi `subprocess.Popen("nohup caffeinate ... </dev/null >> log 2>&1 &", shell=True, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)` từ bên trong tiến trình daemon trên macOS, làm tiến trình con bị đóng mất file descriptor chuẩn (`fd 0/1/2`) khi khởi tạo Python interpreter.
- **Cách khắc phục & File đã sửa**:
  - Sửa `CHANNEL_PROFILES["channel_2_life_comedy"]`, `is_valid_baby_ai_topic()`, `_discover_tiktok_by_keywords()`, `verify_voice_density()`, và `purge_foreign_and_offtopic_ready_videos()` trong [`tiktok_pipeline/crawler.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/crawler.py): bổ sung 29 seed URL em bé AI chuẩn (`@andy76243`, `@funybaby199`, `@jean131419`, `@lin_cen_ling`, `@tinyverse78827`, `@waikityaw`, `#大头菠萝`), tắt `multi_platform_keywords` cho `baby_AI`, chặn tuyệt đối từ khóa người lớn/công nghệ/phim ảnh (`iphone`, `live图`, `小姐姐`, `女孩子`, `宝子`, `电影`, v.v.), bắt buộc có dấu hiệu em bé rõ ràng cả ở bước tìm kiếm lẫn bước hậu kiểm giọng đọc Vietsub, và ưu tiên cộng `+5,000,000` điểm cho các kênh chuyên em bé AI.
  - Sửa `_deduplicate_existing_posts_in_content_page()` trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py) để tự động xóa bài đăng lạc đề `"ảnh live của iphone"` trên trang `tiktokstudio/content` của `baby_AI`.
  - Sửa `_ensure_both_streams_alive()` trong [`tiktok_pipeline/agy_intel_bridge.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/agy_intel_bridge.py) và `_auto_revive_stream1_upload_if_dead()` trong [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py): truyền trực tiếp file handle đang mở (`stdout=log_fh, stderr=subprocess.STDOUT, close_fds=True, start_new_session=True`) cho `subprocess.Popen` mà không qua `shell=True` + `nohup`.
- **Luật Miễn Dịch Vĩnh Viễn**: Với kênh `baby_AI`, tuyệt đối không dùng từ đơn `"宝"`, `"孩"`, `"姐姐"`, `"哥哥"` làm từ khóa nhận diện và không cào YouTube Shorts/Reels đa nền tảng; khi khởi chạy tiến trình nền Python trên macOS từ daemon, luôn truyền file handle thực (`open(..., "a")`) vào `stdout`/`stderr` của `subprocess.Popen` với `shell=False` thay vì dùng `nohup ... >> log 2>&1 &` kết hợp `DEVNULL`.

---

## 🔄 3. GIAO THỨC GHI NHẬN LỖI MỚI (AUTO-EVOLUTION PROTOCOL)

Mỗi khi sửa xong một bug mới (bằng tay hoặc tự động qua `agy_intel_bridge.py`), thêm một mục mới theo đúng chuẩn sau ở cuối Mục 2:

```markdown
### [BUG-XXX] <Tên ngắn gọn của lỗi>
- **Thời gian**: YYYY-MM-DD HH:MM:SS
- **Triệu chứng**: <Biểu hiện lỗi trên log / màn hình / telemetry>
- **Nguyên nhân gốc rễ**: <Vì sao lỗi xảy ra ở tầng code / hệ thống>
- **Cách khắc phục & File đã sửa**: <Mô tả bản vá và đường dẫn file>
- **Luật Miễn Dịch Vĩnh Viễn**: <Quy tắc bắt buộc để không bao giờ lặp lại lỗi này>
```
