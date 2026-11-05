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
