---
description: Quy tắc Tự Tiến Hóa, Phản Hồi Tốc Độ Cao (harness.py) & Bàn Giao Tự Động (HANDOFF.md / AGENT.md)
---

# Quy Tắc Bắt Buộc: Vận Hành Tốc Độ Cao, Kiểm Định `harness.py` & Bàn Giao `HANDOFF.md` / `AGENT.md`

1. **Đọc trước khi sửa**: Trước khi chẩn đoán lỗi hoặc chỉnh sửa các file trong `tiktok_pipeline/`, luôn đối chiếu các **Nguyên tắc bất biến (Core Immunity Invariants)** và **Danh sách lỗi đã tiến hóa** trong [`AGENT.md`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/AGENT.md) cùng kiến trúc tại [`HANDOFF.md`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/HANDOFF.md) để tuyệt đối không làm tái phát bất kỳ lỗi cũ nào.
2. **Ghi nhận sau khi vá lỗi**: Mỗi khi phát hiện một bug mới (trên máy Silicon hoặc máy Intel) và đã xử lý xong, **BẮT BUỘC** cập nhật ngay một mục `[BUG-XXX]` mới vào [`AGENT.md`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/AGENT.md) (gồm: *Thời gian, Triệu chứng, Nguyên nhân gốc rễ, Cách khắc phục & File đã sửa, Luật Miễn Dịch Vĩnh Viễn*).
3. **Phản hồi 1 bước cho lệnh Bật/Tắt Công Tắc**: Khi người dùng yêu cầu bật/tắt công tắc, chạy ngay lệnh `./venv/bin/python harness.py --switch <1|2|3|4|master> <on|off> --push` trong **1 bước duy nhất** (không tự ý đọc log telemetry dài dòng trừ khi được hỏi).
4. **Sửa đúng Module Mixin nhỏ**: Khi sửa logic của `TikTokAutoPoster`, chỉ đọc và sửa đúng file Mixin phụ trách (`poster_browser.py`, `poster_studio_ui.py`, `poster_cleanup.py`, `poster_media_prep.py`, `poster_stream2_warmup.py`, hoặc `auto_poster.py`), giữ nguyên 100% chữ ký 59 hàm.
5. **Kiểm định `harness.py` & Cập nhật `HANDOFF.md` sau mỗi đầu việc**: Cứ sau mỗi đầu việc hoàn thành, chạy `./venv/bin/python harness.py --verify`, tự động cập nhật [`HANDOFF.md`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/HANDOFF.md) và gộp chung code + `HANDOFF.md` vào **1 git commit gọn gàng** đẩy lên `origin/main` (`git pull --rebase --autostash origin main && git push origin main`).
