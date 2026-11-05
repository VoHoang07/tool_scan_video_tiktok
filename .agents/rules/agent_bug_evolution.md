---
description: Quy tắc Tự Tiến Hóa & Miễn Dịch Lỗi qua file AGENT.md
---

# Quy Tắc Bắt Buộc: Sổ Tay Tự Tiến Hóa `AGENT.md`

1. **Đọc trước khi sửa**: Trước khi chẩn đoán lỗi hoặc chỉnh sửa các file trong `tiktok_pipeline/`, luôn đối chiếu các **Nguyên tắc bất biến (Core Immunity Invariants)** và **Danh sách lỗi đã tiến hóa** trong [`AGENT.md`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/AGENT.md) để tuyệt đối không làm tái phát bất kỳ lỗi cũ nào.
2. **Ghi nhận sau khi vá lỗi**: Mỗi khi phát hiện một bug mới (trên máy Silicon hoặc máy Intel) và đã xử lý xong, **BẮT BUỘC** cập nhật ngay một mục `[BUG-XXX]` mới vào [`AGENT.md`](file:///Users/vohoang/Documents/tool_scan_video_tiktok/AGENT.md) (gồm: *Thời gian, Triệu chứng, Nguyên nhân gốc rễ, Cách khắc phục & File đã sửa, Luật Miễn Dịch Vĩnh Viễn*) rồi commit kèm bản vá lên GitHub.
