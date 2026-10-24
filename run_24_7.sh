#!/usr/bin/env bash
# ==============================================================================
# SCRIPT TREO MÁY MACBOOK CHẠY 24/7 (CHỐNG SLEEP + HOT-RELOAD TỰ ĐỘNG CẬP NHẬT)
# - Sử dụng `caffeinate -dimsu` để ngăn MacBook Intel ngủ khi tắt màn hình.
# - Chạy song song:
#   + Tự động kéo code mới từ Git (nếu có) & Hot-Reload ngay khi bạn sửa code từ máy Silicon.
#   + Tự động cào & lấp đầy kho chờ (`ready/`) cho cả 4 kênh (giữ tối đa 5 video/kênh).
#   + Tự động đăng bài lên 4 kênh TikTok đúng theo khung giờ trong `schedule_config.json`.
#   + Tự động chuyển video đã đăng sang `posted/` và cập nhật `output/danh_sach_video.csv`.
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
mkdir -p "$PROJECT_DIR/logs"

MAX_READY="${1:-5}"

echo "======================================================================"
echo "KHỞI ĐỘNG HỆ THỐNG CÀO & ĐĂNG TIKTOK TỰ ĐỘNG 24/7 (CÓ HOT-RELOAD)"
echo "  - Chống ngủ máy (caffeinate): ĐÃ BẬT"
echo "  - Tự động nhận code mới (Hot-Reload): ĐÃ BẬT"
echo "  - Số lượng video dự trữ mỗi kênh (max-ready): $MAX_READY"
echo "  - Cấu hình khung giờ đăng: $PROJECT_DIR/schedule_config.json"
echo "  - File log 24/7: $PROJECT_DIR/logs/daemon_24_7.log"
echo "  - Nhấn Ctrl + C để dừng."
echo "======================================================================"

# Chạy dưới caffeinate để Mac không bao giờ Sleep CPU/Wi-Fi/Disk
caffeinate -dimsu bash -c '
    while true; do
        if [ -d .git ]; then
            git pull --rebase --autostash >/dev/null 2>&1 || true
        fi
        echo "[$(date "+%Y-%m-%d %H:%M:%S")] Đang chạy Worker 24/7..." | tee -a logs/daemon_24_7.log
        ./venv/bin/python main.py --continuous --max-ready "'"$MAX_READY"'" 2>&1 | tee -a logs/daemon_24_7.log
        EXIT_CODE=${PIPESTATUS[0]}
        if [ "$EXIT_CODE" -eq 42 ]; then
            echo "[$(date "+%Y-%m-%d %H:%M:%S")] [Hot-Reload] Đã nhận bản cập nhật mới từ máy Silicon! Khởi động lại ngay sau 2 giây..." | tee -a logs/daemon_24_7.log
            sleep 2
            continue
        fi
        if [ "$EXIT_CODE" -eq 130 ] || [ "$EXIT_CODE" -eq 143 ]; then
            echo "[$(date "+%Y-%m-%d %H:%M:%S")] Đã nhận lệnh dừng từ người dùng (Ctrl+C)." | tee -a logs/daemon_24_7.log
            break
        fi
        echo "[$(date "+%Y-%m-%d %H:%M:%S")] Tiến trình tạm ngắt (mã $EXIT_CODE), tự động khởi động lại sau 30 giây..." | tee -a logs/daemon_24_7.log
        sleep 30
    done
'
