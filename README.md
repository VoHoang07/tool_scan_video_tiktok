# TikTok & Douyin Multi-Channel Automated Pipeline

Hệ thống tự động hóa tải và xử lý video trên **TikTok** và **Douyin** cho **4 Kênh Chuyên Biệt**:
- **Lấy trực tiếp**: Toàn bộ video trong danh sách link mẫu (`seed_video_urls`), toàn bộ video trong các kênh mẫu (`seed_channels`), và các video liên quan cùng chủ đề / hashtag / âm thanh gốc.
- **Xử lý theo ngôn ngữ**:
  - **Video Tiếng Việt có sẵn (`vi`)**: Giữ nguyên 100% video gốc không logo, giữ nguyên âm thanh gốc và tiêu đề gốc (không tự tạo kịch bản, không lồng tiếng đè).
  - **Video Tiếng Trung / Ngoại ngữ (`zh`, ...)**: Dịch trực tiếp lời thoại sang tiếng Việt, lồng tiếng `edge-tts`, vặn nhỏ âm thanh gốc xuống 12% (`0.12`) để giữ nhạc nền/hiệu ứng, làm mờ sub gốc và đóng phụ đề tiếng Việt.

## 1. Cấu trúc thư mục quản lý Video (`Chờ đăng` vs `Đã đăng`)

```text
output/
├── video_AI_hay/
│   ├── ready/                 # Video CHỜ ĐĂNG của Kênh 1 (AI Hoạt họa / Thú cưng / Phim ngắn AI)
│   └── posted/                # Video ĐÃ ĐĂNG của Kênh 1
├── baby_AI/
│   ├── ready/                 # Video CHỜ ĐĂNG của Kênh 2 (Đời sống / Gia đình hài hước)
│   └── posted/                # Video ĐÃ ĐĂNG của Kênh 2
├── audio_dem_khuyen/
│   ├── ready/                 # Video CHỜ ĐĂNG của Kênh 3 (Truyện Audio Ngôn tình & Đoản kịch)
│   └── posted/                # Video ĐÃ ĐĂNG của Kênh 3
├── tu_chan_gioi/
│   ├── ready/                 # Video CHỜ ĐĂNG của Kênh 4 (AI Tu tiên / Huyền huyễn)
│   └── posted/                # Video ĐÃ ĐĂNG của Kênh 4
└── danh_sach_video.csv        # File quản lý tập trung (channel, filename, source_url, caption, hashtags, status, posted_at)
```

## 2. Quản lý & Tự động đồng bộ trạng thái Chờ đăng (`READY`) & Đã đăng (`POSTED`)

Hệ thống hỗ trợ **4 cách đồng bộ tự động** cho toàn bộ 4 kênh (`video_AI_hay`, `baby_AI`, `audio_dem_khuyen`, `tu_chan_gioi`):
1. **Tự động phát hiện khi bạn chọn file đăng lên TikTok trên trình duyệt (macOS File Upload Dialog)**:
   Khi bạn bấm chọn video trong `output/<ten_kenh>/ready/` từ hộp thoại Upload của trình duyệt (Chrome/Safari/Brave/Arc) để đăng lên TikTok, trình theo dõi `--watch-sync` (hoặc `--status` / `--continuous`) sẽ tự động phát hiện, di chuyển video sang `output/<ten_kenh>/posted/` và cập nhật `status=POSTED` trong `output/danh_sach_video.csv`.
2. **Đánh dấu thủ công bằng đường dẫn file, tên file hoặc ID**:
   ```bash
   ./venv/bin/python main.py --mark-posted "/Users/vohoang/Documents/tool_scan_video_tiktok/output/audio_dem_khuyen/ready/20261022_232340_tiktok_story_7682055125342424341.mp4"
   # Hoặc chỉ cần truyền ID video:
   ./venv/bin/python main.py --mark-posted 7682055125342424341
   ```
3. **Sửa trực tiếp trong file `output/danh_sach_video.csv` hoặc kéo thả trong Finder**:
   - Đổi cột `status` thành `POSTED` (hoặc `x`, `1`, `done`) trong CSV -> hệ thống tự chuyển file từ `ready/` sang `posted/`.
   - Hoặc kéo thả file `.mp4` từ `ready/` sang `posted/` -> hệ thống tự cập nhật CSV thành `POSTED`.
4. **Lệnh kiểm tra tồn kho & chạy ngầm đồng bộ 4 kênh**:
   ```bash
   # Đồng bộ ngay lập tức và xem bảng thống kê 4 kênh
   ./venv/bin/python main.py --status

   # Chạy ngầm tự động theo dõi & chuyển video sang posted/ ngay sau khi bạn upload lên TikTok
   ./venv/bin/python main.py --watch-sync
   ```

## 3. Lệnh cào theo từng kênh hoặc link trực tiếp

```bash
# Cào theo từng kênh (tự động lấy từ link mẫu -> toàn bộ video trong kênh mẫu -> video liên quan)
./venv/bin/python main.py --channel 1 --limit 3
./venv/bin/python main.py --channel 2 --limit 3
./venv/bin/python main.py --channel 3 --limit 5
./venv/bin/python main.py --channel 4 --limit 3

# Đưa trực tiếp 1 link video hoặc link kênh TikTok vào kênh chỉ định
./venv/bin/python main.py --url "https://www.tiktok.com/@tieutin2000/video/7682055125342424341" --channel 3
./venv/bin/python main.py --url "https://www.tiktok.com/@tieutin2000" --channel 3 --limit 5
```

## 4. Chế độ treo máy MacBook Intel / Apple Silicon chạy 24/7 (Tự động Cào & Tự động Đăng theo khung giờ)

### Bước 1: Cài đặt trên máy MacBook Chip Intel (`x86_64`)
Copy toàn bộ thư mục `tool_scan_video_tiktok` sang máy MacBook Intel, mở Terminal tại thư mục đó và chạy:
```bash
chmod +x setup_mac_intel.sh run_24_7.sh
./setup_mac_intel.sh
```
*(Script sẽ tự tạo lại `venv` chuẩn chip Intel `x86_64`, cài `ffmpeg`, `faster-whisper` và `playwright`).*

### Bước 2: Đăng nhập 4 tài khoản TikTok (Chỉ cần làm 1 lần duy nhất)
Mỗi lệnh dưới đây sẽ mở một cửa sổ Chrome độc lập ứng với từng kênh. Bạn chỉ cần **Quét mã QR** trên điện thoại để đăng nhập TikTok rồi đóng cửa sổ lại (phiên đăng nhập sẽ được lưu vĩnh viễn vào `browser_profiles/<ten_kenh>/`):
```bash
./venv/bin/python main.py --login-channel video_AI_hay
./venv/bin/python main.py --login-channel baby_AI
./venv/bin/python main.py --login-channel audio_dem_khuyen
./venv/bin/python main.py --login-channel tu_chan_gioi
```

### Bước 3: Cài đặt khung giờ tự động đăng bài mỗi ngày
Bạn có thể đặt các khung giờ đăng trong ngày bằng lệnh `--set-schedule` (hệ thống tự động giãn cách mỗi kênh 10 phút để máy Mac Intel không phải mở 4 trình duyệt cùng lúc), hoặc chỉnh trực tiếp trong file `schedule_config.json`:
```bash
# Ví dụ đặt lịch đăng 3 khung giờ/ngày: Sáng 08:00, Trưa 12:30, Tối 20:00
./venv/bin/python main.py --set-schedule "08:00,12:30,20:00"

# Hoặc test đăng ngay lập tức 1 video trong ready/ của 1 kênh:
./venv/bin/python main.py --post-now video_AI_hay
```

### Bước 4: Bật chế độ chạy 24/7 (`./run_24_7.sh`)
```bash
./run_24_7.sh
```
- Script tích hợp sẵn `caffeinate -dimsu` để MacBook Intel **không bao giờ bị Sleep** khi tắt màn hình.
- Tự động xoay tua cào & lấp đầy kho `ready/` (luôn giữ sẵn 5 video chờ đăng/kênh).
- Đúng đến khung giờ bạn đã set trong `schedule_config.json`, hệ thống tự mở trình duyệt của kênh đó, lấy video trong `ready/` kèm `caption + hashtags` trong `output/danh_sach_video.csv`, bấm **Đăng (Post)** lên TikTok, rồi tự động chuyển video sang `posted/`!
