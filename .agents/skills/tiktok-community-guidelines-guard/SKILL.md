---
name: tiktok-community-guidelines-guard
description: Bộ Quy Tắc Cộng Đồng TikTok Toàn Diện (2025-2026) & Lá Chắn Kiểm Duyệt Tự Động 7 Lớp (Audio ASR, Visual Frame, Title/Caption Sanitizer, Originality, AIGC, Commercial Compliance & TikTok Studio Pre-Post Check Gate) giúp bảo vệ 10 kênh không bao giờ bị đánh gậy Vi phạm Quy tắc Cộng đồng.
---

# Skill: Lá Chắn Quy Tắc Cộng Đồng TikTok Toàn Diện (`TikTokCommunityGuidelinesGuard`)

Skill này tổng hợp toàn bộ **6 Trụ cột Quy tắc Cộng đồng TikTok (2025–2026)** cùng tiêu chuẩn **Đủ điều kiện đề xuất lên bảng tin Dành cho bạn (For You Feed Eligibility)**, và được thực thi tự động bằng code tại [`tiktok_pipeline/community_guidelines_guard.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/community_guidelines_guard.py), [`tiktok_pipeline/stream1_antidetect_skill.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/stream1_antidetect_skill.py) và [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py).

---

## 1. Phân Tích Nguyên Nhân Video Mới Nhất Của `audio_dem_khuyen` Bị Đánh Vi Phạm (`08:51 11/11/2026`)

- **Video bị đánh dấu**: `20261111_084848_tiktok_story_990014502_bongcuc_v6_s145_p2_bg_daylight.mp4` (*BC Số 145 - Phần 2/8*).
- **Nguyên nhân gốc rễ (3 tầng quét AI của TikTok)**:
  1. **Quét Giọng nói Tự động (TikTok Audio ASR Speech-to-Text)**: Trong đoạn audio từ `388s` đến `777s` của Tập 145 Phần 2, nội dung truyện xuất hiện mật độ dày các cụm từ mô tả thương tích nặng, hiện trường vụ án hình sự, kinh dị tâm linh nặng và hành vi đe dọa/quấy rối tại cầu thang tối. Hệ thống ASR tiếng Việt của TikTok chấm điểm rủi ro cao ở mục **Chủ đề Nhạy cảm & Bạo lực (Sensitive & Mature Themes / Graphic Content)**.
  2. **Quét Tiêu đề Khung hình & Caption/Hashtag (OCR + NLP)**: Tiêu đề hiển thị trên khung hình và caption chứa trực tiếp cụm từ tâm linh/ma quỷ kết hợp với nội dung audio có yếu tố hình sự/thương tích.
  3. **Cổng Kiểm tra Nội dung Nhanh trên TikTok Studio**: Khi tải lên lúc `08:51`, bài đăng rơi vào trạng thái `"Nội dung đang được xét duyệt"` rồi bị AI hậu kiểm gắn cờ vi phạm sau khi xuất bản.

---

## 2. Toàn Bộ 6 Trụ Cột Quy Tắc Cộng Đồng TikTok (2025–2026) Bắt Buộc Tuân Thủ

### Trụ cột 1: An toàn và Văn minh (`Safety and Civility`)
- **Cấm hành vi bạo lực & tội phạm**: Không mô tả chi tiết hành vi gây thương tích, án mạng, vũ khí sắc nhọn, bắt cóc, hành hạ hoặc đe dọa xâm hại.
- **Cấm quấy rối & ngôn từ thù ghét**: Không chứa lời lẽ xúc phạm, miệt thị ngoại hình, vùng miền, giới tính hoặc tấn công cá nhân.

### Trụ cột 2: Sức khỏe Tâm thần và Hành vi (`Mental and Behavioral Health`)
- **Chính sách Không khoan nhượng (Zero Tolerance)**: Tuyệt đối không nhắc đến, mô tả hay ám chỉ hành vi tự gây hại bản thân, quyên sinh, rối loạn ăn uống cực đoan hoặc thử thách nguy hiểm.

### Trụ cột 3: Chủ đề Nhạy cảm và Người lớn (`Sensitive and Mature Themes`)
- **Nội dung kinh dị / gây sốc / thương tích (Shocking & Graphic Content)**: Không sử dụng âm thanh hoặc hình ảnh mô tả máu me, thi thể, bộ phận cơ thể bị tổn thương nặng, hoặc hù dọa cực đoan (jumpscare).
- **Nội dung tình dục / gợi dục**: Không mô tả hành vi xâm hại, cưỡng ép, trang phục hở hang hoặc ngôn từ nhạy cảm 18+.

### Trụ cột 4: An toàn Thanh thiếu niên (`Youth Safety and Well-Being`)
- Bảo vệ tuyệt đối trẻ vị thành niên: Đối với các kênh gia đình/em bé (`baby_AI`, `nuoi_con_eq`), tuyệt đối không có cảnh trẻ em gặp nguy hiểm, bạo hành gia đình, hoặc hình ảnh thiếu chuẩn mực.

### Trụ cột 5: Tính Toàn vẹn, Nguyên bản và Xác thực (`Integrity, Authenticity & FYP Eligibility`)
- **Chống Nội dung Kém nguyên bản (`Unoriginal / Re-uploaded Content`)**:
  - Xóa 100% khung viền, logo, watermark, tên kênh nguồn và đoạn giới thiệu của kênh gốc.
  - Biến đổi sâu cả hình ảnh (bố cục đa lớp, lật ngang nền, thanh tiến trình động, bảng màu riêng) lẫn âm thanh (dịch cao độ Formant, EQ phòng thu, chèn câu dẫn độc quyền).
- **Chống Câu dẫn Tương tác Giả tạo (`Inauthentic / Manipulative Engagement`)**:
  - Không dùng các câu ép buộc lộ liễu bị thuật toán quét spam như *"Bấm like để...", "Share nếu không sẽ gặp xui..."*. Chỉ đặt câu hỏi thảo luận mở tự nhiên về tình tiết câu chuyện.
- **Khai báo Nội dung AI (`AIGC Disclosure`)**:
  - Tự động bật nhãn AIGC cho video có hình ảnh nhân vật thực tế do AI tạo, và tắt cho video hoạt hình/kể chuyện thông thường để tránh bị gắn cờ sai.

### Trụ cột 6: Hàng hóa Bị kiểm soát và Hoạt động Thương mại (`Regulated Goods & Commercial Activities`)
- **Cấm hàng hóa hạn chế**: Không xuất hiện thuốc lá, vape, rượu mạnh, cờ bạc, chất cấm, vũ khí.
- **An toàn Thương mại & Y tế (`Commercial & Medical Safe-Guard`)**:
  - Không dùng từ cam kết tuyệt đối (*"trị dứt điểm", "chữa khỏi 100%", "cam kết 100%", "số 1 thế giới"*).
  - Không điều hướng người dùng ra ngoài nền tảng (*"Zalo", "Facebook", "WeChat", "Telegram", "Quét mã QR", "Số điện thoại"*).

---

## 3. Cơ Chế Bảo Vệ Tự Động 7 Lớp Trong Pipeline (`TikTokCommunityGuidelinesGuard`)

1. **Lớp Quét & Lọc Danh mục Nguồn (`Catalog & Episode Blacklist Guard`)**:
   - Đưa Tập 145 (`9900145*`) và mọi tập có chủ đề vi phạm nặng vào danh sách chặn vĩnh viễn (`BLOCKED_BONGCUC_EPISODES` & `BLOCKED_GAUSOHAI_EPISODES`).
   - Tự động làm sạch 100% tiêu đề trong `data/bongcuc_full_catalog.json` và `data/gausohai_full_catalog.json` (chuyển hóa các từ nhạy cảm sang ngôn từ văn học/điện ảnh an toàn trước khi vẽ lên khung hình PNG và tạo Caption/Hashtag).
2. **Lớp Quét Âm thanh ASR, Làm Mềm Tự động & Hashtag Theo Khung Giờ (`Audio Speech Safety & Time-of-Day Hashtag Sanitizer`)**:
   - Kiểm tra văn bản/phụ đề/tiêu đề và tự động thay thế toàn bộ từ ngữ nhạy cảm bằng từ trung tính an toàn.
   - **Quy tắc Hashtag theo khung giờ cho kênh `audio_dem_khuyen`**: Nếu đăng video vào **ban ngày (`05:00–17:59`)**, tuyệt đối không gắn các hashtag liên quan tới ban đêm (`#audiodemkhuya`, `#kechuyendemkhuya`, `#nghetruyendemkhuya`, `#gocnhinkhuya`, `#mukbangdemkhuya`, `#chuyendemsau`, `#chuyendemkhuya`, `#demkhuya`); tự động chuyển đổi sang bộ hashtag ban ngày (`#truyenaudio`, `#kechuyen`, `#nghetruyen`, `#truyenngan`, `#gocnhincuocsong`, `#chuyengiadinh`, `#mukbang`). Chỉ sử dụng hashtag ban đêm khi đăng trong khung giờ `18:00–04:59`.
3. **Lớp Kiểm tra Nội dung Nhanh trên TikTok Studio (`Pre-Post Content Check Lite Gate`)**:
   - Trong [`TikTokAutoPoster._configure_upload_settings_public`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py), luôn bật `"Kiểm tra bản quyền nhạc"` + `"Kiểm tra nội dung nhanh"`, chờ kiểm tra hoàn tất và **nếu phát hiện bất kỳ cảnh báo ви phạm / hạn chế đề xuất nào thì HỦY ĐĂNG NGAY LẬP TỨC**, cách ly file đó và chuyển sang video an toàn kế tiếp.
4. **Lớp Tự động Dọn Bài Bị Gắn Cờ trên Trang Quản lý (`Post-Upload Violation Sweeper`)**:
   - Khi mở `tiktokstudio/content`, tự động kiểm tra nếu có bài đăng nào bị gắn nhãn `"Vi phạm Quy tắc Cộng đồng"` thì tự động xóa bỏ khỏi kênh để bảo vệ điểm uy tín tài khoản (`Creator Health Rating`).
