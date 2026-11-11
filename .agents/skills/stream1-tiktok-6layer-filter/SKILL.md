---
name: stream1-tiktok-6layer-filter
description: Skill 6 Lớp Bộ Lọc Chống Phát Hiện & Hạn Chế TikTok dành riêng cho Dòng 1 (Stream 1 - Auto Upload). Tự động kiểm tra, xác thực và tự động sửa chữa (Auto-Heal) toàn bộ 6 lớp bảo vệ (Visual pHash/vHash, AudioPrint, Device Metadata Signature, Retention/Duration, Commercial Safe-Guard, AIGC & Interleaved Anti-Spam) ngay trước khi đăng bất kỳ video nào lên TikTok.
---

# Skill Dòng 1: 6 Lớp Bộ Lọc Chống Phát Hiện TikTok (`Stream1SixLayerAntiDetectSkill`)

Skill này được tích hợp trực tiếp vào **Dòng Hoạt Động 1 (`--stream-upload`)** tại [`tiktok_pipeline/stream1_antidetect_skill.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/stream1_antidetect_skill.py) và được gọi tự động trong [`TikTokAutoPoster.upload_one_channel_video`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py) trước khi trình duyệt nạp bất kỳ file video nào lên TikTok Studio.

## Quy trình Kiểm tra & Tự động Khắc phục 6 Lớp trước khi đăng (`verify_and_enforce_before_upload`)

1. **Lớp 1 — Chống Quét Trùng Lặp Hình Ảnh (`Visual pHash / vHash & Frame Geometry`)**:
   - Kiểm tra codec `h264` (`yuv420p`), độ phân giải dọc hợp lệ (`>= 540x960`), và kiểm tra xem video đã qua xử lý biến thiên hình ảnh (lật ngang `hflip` tranh vẽ + hạt phim động `Film Grain` + viền `Vignette` đối với `audio_dem_khuyen`, hoặc xóa sub/biến thiên khung hình đối với các kênh lồng tiếng) hay chưa.
   - Nếu phát hiện video thô chưa qua xử lý chống trùng lặp, tự động kích hoạt bộ lọc tái mã hóa chống vân tay hình ảnh ngay tại chỗ.
2. **Lớp 2 — Chống Quét Bản Quyền & Trùng Lặp Âm Thanh (`AudioPrint Evasion & Studio EQ`)**:
   - Kiểm tra kênh âm thanh `aac` (`44100 Hz`), đảm bảo đã dịch tần số vi mô (`Micro Pitch-Shift`) hoặc trộn lồng tiếng Việt kèm lọc dải tần `55Hz–13.5kHz` để triệt tiêu sóng mang watermark âm thanh.
3. **Lớp 3 — Tẩy Rửa Metadata Gốc & Gắn Chữ Ký Thiết Bị Theo Kênh (`Metadata, Container & macOS xattr Scrubbing`)**:
   - Dùng `ffprobe` quét toàn bộ thẻ `format.tags` và `stream.tags`.
   - Chặn tuyệt đối các dấu vết nguồn (`Google`, `YouTube`, `Douyin`, `TikTok`, `Lavf61.x` thô).
   - Bắt buộc gắn đúng chữ ký phần mềm dựng phim độc lập của từng kênh (`CapCut 14.4.0 (iOS 17.5.1)`, `VN Video Editor 2.24.1 (iOS)`, `Adobe Premiere Pro 2024.5 (macOS)`...) cùng `CoreMedia Video` / `CoreMedia Audio` và xóa sạch cờ `com.apple.macl` / `com.apple.quarantine`. Nếu thiếu, tự động đóng gói lại (`bitexact`) ngay trước khi đăng.
4. **Lớp 4 — Kiểm Tra Thời Lượng & Giữ Chân Người Xem (`Retention & Duration Guard`)**:
   - Đảm bảo thời lượng `>= 15.0s`, dung lượng `>= 500 KB`, riêng kênh `audio_dem_khuyen` bắt buộc phải là chuẩn `bongcuc_v6_s` và kênh `video_AI_hay` bắt buộc là chuẩn `gautrucden_v1_s`.
5. **Lớp 5 — Bộ Lọc Quy Tắc Cộng Đồng TikTok & Từ Khóa Thương Mại (`TikTokCommunityGuidelinesGuard` + `Commercial Safe-Guard`)**:
   - Kết hợp trực tiếp với skill [`tiktok-community-guidelines-guard`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/.agents/skills/tiktok-community-guidelines-guard/SKILL.md) tại [`tiktok_pipeline/community_guidelines_guard.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/community_guidelines_guard.py) để chặn các tập có nội dung nhạy cảm/kinh dị nặng, làm sạch 100% tiêu đề trên khung hình PNG, caption và hashtags, đồng thời kiểm tra cổng `"Kiểm tra nội dung nhanh"` trên TikTok Studio trước khi bấm `"Đăng"`.
6. **Lớp 6 — Kiểm Soát Nhãn AIGC & Chống Spam Đăng Liên Tiếp (`AIGC Disclosure & Interleaved Guard`)**:
   - Chặn đăng trùng `story_id` đã có trong lịch sử đã đăng, đảm bảo kênh `audio_dem_khuyen` và `video_AI_hay` đăng **xen kẽ đa tập** và đúng thứ tự phần nhỏ trước, đồng thời cấu hình chính xác nút gạt AIGC.
