# HANDOFF.md — Tài Liệu Bàn Giao & Trạng Thái Hệ Thống (`tool_scan_video_tiktok`)

> **Cập nhật lần cuối**: `2026-11-11 13:51:00 +07:00`
> **Commit mốc hiện tại**: Đã bổ sung bộ kiểm định & điều khiển 1 bước [`harness.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/harness.py) + tách `auto_poster.py` thành 6 module Mixin chuẩn hóa + Lá chắn Quy tắc Cộng đồng 7 lớp `[BUG-025]`

---

## 1. Kiến Trúc, Luồng Dữ Liệu & Các Quyết Định Kỹ Thuật Đã Chốt

### 1.1. Mô hình vận hành 2 máy (Hybrid Silicon Control -> Intel Execution)
- **Máy Điều Khiển (Local Mac M-Series / Apple Silicon)**:
  - Chỉnh sửa cấu hình, cập nhật công tắc trong [`schedule_config.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/schedule_config.json), phát triển tính năng/bộ lọc và đẩy code lên nhánh `origin/main`.
- **Máy Thực Thi 24/7 (Remote Mac Intel `x86_64`)**:
  - Chạy ngầm tiến trình cầu nối [`tiktok_pipeline/agy_intel_bridge.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/agy_intel_bridge.py) và 2 tiến trình chính từ [`tiktok_pipeline/main.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/main.py):
    1. **Dòng 1 (`--stream-upload`)**: Tự động tải video nguồn, cắt tập, render FFmpeg chống phát hiện 6 lớp, kiểm duyệt Quy tắc cộng đồng 7 lớp và đăng lên TikTok Studio theo khung giờ riêng của 10 kênh.
    2. **Dòng 2 (`--stream-filter24h`)**: Chạy bộ lọc tương tác 24h và lướt FYP nuôi 10 kênh như người dùng thật.
  - Tự động phát hiện thay đổi file `.py` hoặc [`schedule_config.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/schedule_config.json) qua `_get_codebase_mtime_snapshot()` và nạp nóng (**Hot-Reload In-Place**) mà không làm đứt tiến trình đang chạy.
  - Sau mỗi ca đăng hoặc tương tác, tự động ghi báo cáo trạng thái vào [`remote_telemetry/intel_status_report.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/remote_telemetry/intel_status_report.json) và push ngược lên GitHub.

### 1.2. Hệ thống Khóa Master & 4 Công Tắc Điều Khiển (`system_switches` trong `schedule_config.json`)
Quản lý bởi [`MasterSwitchController`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/master_switch_controller.py#L43-L402):
- **`master_switch`**: Khóa tổng bật/tắt toàn bộ hệ thống trên máy Intel.
- **Công tắc 1 (`switch_1_factory`)**: Cào đa nền tảng & lấp đầy kho `output/<channel>/ready/` (tối đa 5 video/ngách).
- **Công tắc 2 (`switch_2_upload`)**: Điều khiển đăng bài tự động (`tiktok`, `fb_reels`, `yt_shorts`) — **Hiện đang BẬT (`tiktok: true`)**.
- **Công tắc 3 (`switch_3_interact`)**: Điều khiển luồng tương tác 24h & lướt FYP nuôi kênh — **Hiện đang TẮT (`tiktok: false`)**.
- **Công tắc 4 (`switch_4_clear`)**: Giữ lại đúng Top 8 video nhiều view nhất trong `posted/` mỗi kênh, tự động dọn dẹp các file `.mp4` cũ và `workspace_temp` quá 2 giờ để bảo vệ ổ cứng.

### 1.3. Luồng dữ liệu Dòng 1 (Stream 1 Auto-Upload Pipeline)
1. **Chuẩn bị nguồn & Cắt tập sole `1:1` (Ngoài khóa trình duyệt)**:
   - Kênh **`audio_dem_khuyen` (`bongcuc_v6_s`)**: [`BongCucSplitter`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/bongcuc_splitter.py) lấy danh sách từ [`data/bongcuc_full_catalog.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/data/bongcuc_full_catalog.json) (147 tập sạch, đã nạp sẵn 100% `duration`), cắt thành các phần dọc `1080x1920`, hiển thị nhãn `"BC SỐ <tập>"` trong khung dẫn chuyện nhưng tuyệt đối không để lộ tên kênh nguồn, đăng đan xen `1:1` (1 phần tập bù : 1 phần tập mới).
   - Kênh **`video_AI_hay` (`gausohai_v1_s`)**: [`GauSoHaiSplitter`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/gausohai_splitter.py) lấy danh sách từ [`data/gausohai_full_catalog.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/data/gausohai_full_catalog.json) (353 tập sạch), cắt bỏ hoàn toàn intro/outro và watermark góc phải trên của kênh nguồn, đăng đan xen `1:1` (`Phần 1` : `Phần 2+`).
   - **8 kênh còn lại**: [`StoryAudioCrawler`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/crawler.py) cào video theo ngách, lọc trùng pHash/vHash/AudioPrint và xuất vào `output/<channel>/ready/`.
2. **Hậu kỳ chống phát hiện 6 lớp & Kiểm duyệt Cộng đồng 7 lớp**:
   - [`Stream1AntiDetectSkill`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/stream1_antidetect_skill.py) kết hợp [`TikTokCommunityGuidelinesGuard`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/community_guidelines_guard.py) kiểm tra mã tập (`story_id`), làm sạch tiêu đề trên màn hình, phụ đề, caption, hashtag và đóng dấu metadata iPhone 16 Pro Max trước khi xuất file `.mp4` cuối cùng.
3. **Mở Chrome Anti-Detect & Đăng qua TikTok Studio**:
   - Chỉ khi đã có sẵn file `.mp4` đạt chuẩn trong `ready/`, [`TikTokAutoPoster.upload_one_channel_video()`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_media_prep.py#L928-L1312) mới chiếm khóa `.tiktok_browser_session.lock`, mở Chrome profile riêng của kênh, tải file lên TikTok Studio, bật `"Kiểm tra bản quyền nhạc"` + `"Kiểm tra nội dung nhanh"`.
   - Nếu `"Kiểm tra nội dung nhanh"` báo bất kỳ cảnh báo rủi ro nào (`cg_blocked: True`), hệ thống **hủy đăng ngay lập tức**, cách ly file vào `quarantine_cg_violation/` và chuyển sang video sạch tiếp theo.

### 1.4. Quyết định kiến trúc Modular Mixin cho `TikTokAutoPoster`
- Chia nhỏ file [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py) (từ 5.753 dòng xuống 1.096 dòng) thành 5 Mixin độc lập + 1 file điều phối trung tâm, giữ nguyên 100% chữ ký của 59 hàm để tương thích ngược tuyệt đối với mọi tiến trình đang chạy.

---

## 2. Danh Sách Từng File Đã Tạo Hoặc Sửa Kèm Vai Trò Cụ Thể

### 2.1. Nhóm Module Điều Phối Đăng Bài (Modular `TikTokAutoPoster`)
| File | Số dòng | Vai trò cụ thể |
| :--- | :---: | :--- |
| [`harness.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/harness.py) | 265 | Bộ kiểm định 4 lớp tự động (`--verify`, chạy < 1 giây), xem nhanh trạng thái (`--status`), và bật/tắt công tắc đẩy thẳng lên GitHub trong 1 bước (`--switch <1..4> <on|off> --push`). |
| [`tiktok_pipeline/auto_poster.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py) | 1.096 | Class chính [`TikTokAutoPoster`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/auto_poster.py#L46-L1096), quản lý lịch trình 10 kênh (`DEFAULT_SCHEDULE_CONFIG`), tự động Hot-Reload 5 Mixin, hồi sinh daemon và đẩy telemetry Git. |
| [`tiktok_pipeline/poster_browser.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_browser.py) | 795 | [`PosterBrowserMixin`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_browser.py#L76-L795) — Quản lý khóa `.tiktok_browser_session.lock`, khởi chạy Chrome Anti-Detect (`STEALTH_INIT_SCRIPT`), cô lập vân tay phần cứng và đồng bộ cookie/session. |
| [`tiktok_pipeline/poster_studio_ui.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_studio_ui.py) | 1.108 | [`PosterStudioUIMixin`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_studio_ui.py#L19-L1108) — Thao tác DOM trên TikTok Studio: đóng popup, nhập caption/hashtag, chọn quyền riêng tư `"Mọi người"`, bật công tắc bản quyền & kiểm tra nội dung nhanh (`_configure_upload_settings_public`), bấm nút `"Đăng"`. |
| [`tiktok_pipeline/poster_cleanup.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_cleanup.py) | 809 | [`PosterCleanupMixin`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_cleanup.py#L18-L809) — Quét trang `tiktokstudio/content`, xóa video trùng lặp (`_deduplicate_existing_posts_in_content_page`), xóa video bị gắn cờ hạn chế/vi phạm (`_delete_post_in_content_page`) và mở công khai bài viết. |
| [`tiktok_pipeline/poster_media_prep.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_media_prep.py) | 1.312 | [`PosterMediaPrepMixin`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_media_prep.py#L18-L1312) — Chuẩn bị file `ready/` (`_get_next_ready_video_for_channel`), kiểm tra trùng lặp pHash/AudioPrint và thực thi ca đăng (`upload_one_channel_video`). |
| [`tiktok_pipeline/poster_stream2_warmup.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_stream2_warmup.py) | 805 | [`PosterStream2WarmupMixin`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_stream2_warmup.py#L19-L805) — Điều phối Dòng 2 (`check_and_run_stream2_filter24h_schedule`) và chế độ lướt FYP nuôi 10 kênh (`warmup_browse_channel`). |

### 2.2. Nhóm Bộ Lọc Chống Phát Hiện, Kiểm Duyệt Cộng Đồng & Cắt Tập
| File | Số dòng | Vai trò cụ thể |
| :--- | :---: | :--- |
| [`tiktok_pipeline/community_guidelines_guard.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/community_guidelines_guard.py) | 120 | [`TikTokCommunityGuidelinesGuard`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/community_guidelines_guard.py#L12-L120) — Lá chắn kiểm duyệt 7 lớp: chặn vĩnh viễn các tập có rủi ro quy tắc cộng đồng (`BLOCKED_BONGCUC_EPISODES`, `BLOCKED_GAUSOHAI_EPISODES`), làm sạch văn bản hiển thị trên video, caption và hashtag. |
| [`tiktok_pipeline/stream1_antidetect_skill.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/stream1_antidetect_skill.py) | 424 | [`Stream1AntiDetectSkill`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/stream1_antidetect_skill.py) — Thực thi 6 lớp bảo vệ kỹ thuật (Visual Hash, AudioPrint, Device Metadata, Retention Hook, Commercial Safe-Guard, AIGC & Interleaved Anti-Spam). |
| [`tiktok_pipeline/bongcuc_splitter.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/bongcuc_splitter.py) | 1.701 | [`BongCucSplitter`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/bongcuc_splitter.py) — Cắt tập kể chuyện đêm khuya cho kênh `audio_dem_khuyen` theo tỷ lệ đan xen `1:1`, tối ưu FFmpeg `-preset superfast` và lọc từ ngữ nhạy cảm. |
| [`tiktok_pipeline/gausohai_splitter.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/gausohai_splitter.py) | 993 | [`GauSoHaiSplitter`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/gausohai_splitter.py) — Cắt tập hoạt hình Gấu Trúc Đen cho kênh `video_AI_hay` theo tỷ lệ đan xen `1:1`, xóa sạch dấu vết kênh nguồn và lọc nội dung nhạy cảm. |
| [`tiktok_pipeline/master_switch_controller.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/master_switch_controller.py) | 402 | [`MasterSwitchController`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/master_switch_controller.py) — Quản lý trạng thái 4 công tắc trong [`schedule_config.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/schedule_config.json) và dọn dẹp ổ cứng tự động. |
| [`.agents/skills/tiktok-community-guidelines-guard/SKILL.md`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/.agents/skills/tiktok-community-guidelines-guard/SKILL.md) | — | Tài liệu Skill chuẩn hóa Bộ Quy Tắc Cộng Đồng TikTok (2025–2026) & Lá chắn tự động 7 lớp. |
| [`.agents/skills/stream1-tiktok-6layer-filter/SKILL.md`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/.agents/skills/stream1-tiktok-6layer-filter/SKILL.md) | — | Tài liệu Skill 6 lớp chống phát hiện kỹ thuật tích hợp cùng `TikTokCommunityGuidelinesGuard`. |
| [`data/bongcuc_full_catalog.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/data/bongcuc_full_catalog.json) | — | Danh mục 147 tập sạch của `audio_dem_khuyen` (đã loại bỏ tập 145 và điền sẵn 100% `duration`). |
| [`data/gausohai_full_catalog.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/data/gausohai_full_catalog.json) | — | Danh mục 353 tập sạch của `video_AI_hay` (đã loại bỏ 10 tập chứa yếu tố nhạy cảm và làm sạch tiêu đề). |

---

## 3. Các Trường Hợp Biên (Edge Cases), Ràng Buộc Ngầm & Lỗi Cần Tránh

1. **Ràng buộc bộ lọc an toàn của AI Host (Safety Filter Hygiene)**:
   - **Tuyệt đối không** trích dẫn nguyên văn các từ ngữ mô tả hiện trường vụ án, thương tích hoặc kinh dị nặng từ phụ đề/tiêu đề video trong phần suy nghĩ (`thinking`), câu trả lời hay chuỗi code thuần. Luôn sử dụng mô tả chính sách ở mức khái quát và mã Unicode escape (`\uXXXX`) trong biểu thức chính quy tại [`community_guidelines_guard.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/community_guidelines_guard.py).
2. **Tránh giữ khóa `.tiktok_browser_session.lock` khi đang tải/render FFmpeg (`[BUG-024]`)**:
   - Trên máy Mac Intel (`x86_64`), quá trình tải YouTube và render FFmpeg một tập dài mất 3–6 phút. Nếu gọi `_get_next_ready_video_for_channel()` bên trong khóa trình duyệt, các kênh khác đến giờ đăng sẽ bị kẹt chờ khóa.
   - **Quy tắc bắt buộc**: Luôn chuẩn bị xong file `ready/*.mp4` **trước khi** gọi `_acquire_browser_process_lock()`.
3. **Tránh gọi `yt-dlp --dump-json` tuần tự khi khởi tạo danh mục (`[BUG-024]`)**:
   - Mọi tập trong [`data/bongcuc_full_catalog.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/data/bongcuc_full_catalog.json) và [`data/gausohai_full_catalog.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/data/gausohai_full_catalog.json) phải có sẵn trường `"duration"`. Trong [`bongcuc_splitter.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/bongcuc_splitter.py), chỉ được phép probe tối đa 2 tập mỗi chu kỳ nếu thiếu `duration`.
4. **Cổng chặn cứng trên TikTok Studio (`[BUG-025]`)**:
   - Không bao giờ chỉ ghi log cảnh báo khi `"Kiểm tra nội dung nhanh"` trên TikTok Studio hiển thị `"Nội dung đang được xét duyệt"` hoặc `"Không đủ điều kiện cho đề xuất"`. Hàm [`_configure_upload_settings_public()`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_studio_ui.py#L719-L859) bắt buộc trả về `cg_blocked: True` để [`upload_one_channel_video()`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_media_prep.py#L928-L1312) hủy bấm nút `"Đăng"` và cách ly video.
5. **Xung đột Git Rebase khi máy Intel liên tục đẩy Telemetry**:
   - Máy Intel tự động commit file [`remote_telemetry/intel_status_report.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/remote_telemetry/intel_status_report.json) sau mỗi ca đăng. Khi đẩy code từ máy điều khiển, luôn dùng `git pull --rebase --autostash origin main && git push origin main`.
6. **Quy tắc phản hồi nhanh 1 bước khi bật/tắt công tắc**:
   - Khi người dùng yêu cầu bật/tắt công tắc hoặc chỉnh lịch đơn giản, chỉ thực hiện cập nhật trực tiếp vào [`schedule_config.json`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/schedule_config.json) và push lên Git trong **1 bước duy nhất**, không tự ý đọc log dài trừ khi được yêu cầu.

---

## 4. Checklist Chi Tiết Các Việc Tiếp Theo Theo Thứ Tự Ưu Tiên

- [ ] **Ưu tiên 1 — Giám sát ca đăng chiều & tối của 2 kênh cắt tập sole `1:1` (`audio_dem_khuyen` & `video_AI_hay`)**:
  - **File & Hàm liên quan**:
    - [`tiktok_pipeline/poster_media_prep.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_media_prep.py) -> [`_get_next_ready_video_for_channel()`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_media_prep.py#L177-L852) & [`upload_one_channel_video()`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_media_prep.py#L928-L1312).
    - [`tiktok_pipeline/bongcuc_splitter.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/bongcuc_splitter.py) -> [`prepare_next_ready_clip()`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/bongcuc_splitter.py#L1486-L1701).
    - [`tiktok_pipeline/gausohai_splitter.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/gausohai_splitter.py) -> [`prepare_next_ready_clip()`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/gausohai_splitter.py#L770-L993).
  - **Mục tiêu**: Đảm bảo các phần tiếp theo được cắt đúng thứ tự `1:1` và vượt qua `"Kiểm tra nội dung nhanh"` trên TikTok Studio.

- [ ] **Ưu tiên 2 — Tự động dọn dẹp bài đăng cũ bị gắn cờ hạn chế trên `tiktokstudio/content`**:
  - **File & Hàm liên quan**:
    - [`tiktok_pipeline/poster_cleanup.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_cleanup.py) -> [`_delete_post_in_content_page()`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_cleanup.py#L410-L682) & [`_deduplicate_existing_posts_in_content_page()`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/poster_cleanup.py#L175-L408).
  - **Mục tiêu**: Xác nhận trong lần mở trình duyệt kế tiếp của kênh `audio_dem_khuyen`, bài đăng cũ bị gắn cờ được tự động gỡ bỏ khỏi trang quản lý nội dung.

- [ ] **Ưu tiên 3 — Tách nhỏ tiếp file `tiktok_pipeline/crawler.py` (4.236 dòng) nếu cần tối ưu thêm tốc độ sửa phần Cào video**:
  - **File & Hàm liên quan**:
    - [`tiktok_pipeline/crawler.py`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/crawler.py) -> class [`StoryAudioCrawler`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/tiktok_pipeline/crawler.py).
  - **Mục tiêu**: Áp dụng cùng mô hình Mixin như `auto_poster.py` nếu cần chỉnh sửa sâu logic cào Douyin/YouTube của 8 kênh còn lại.
