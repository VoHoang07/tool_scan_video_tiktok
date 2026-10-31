#!/usr/bin/env bash
# ==============================================================================
# SCRIPT TREO MÁY MACBOOK CHẠY 24/7 (CHỐNG SLEEP + HOT-RELOAD TỰ ĐỘNG CẬP NHẬT)
# - Sử dụng `caffeinate -dimsu` để ngăn MacBook Intel ngủ khi tắt màn hình.
# - Chạy song song:
#   + Tự động kéo code mới từ GitHub (conflict-free) & Hot-Reload ngay khi sửa từ máy Silicon.
#   + Tích hợp `d-wwei/macos-desktop-control` (CoreGraphics CGEvent OS Input) & `yvgude/lean-ctx`.
#   + Tự động cào & lấp đầy kho chờ (`ready/`) cho cả 4 kênh (giữ tối đa 5 video/kênh).
#   + Tự động đăng bài lên 4 kênh TikTok đúng theo khung giờ trong `schedule_config.json`.
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

export PATH="$PROJECT_DIR/.bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
mkdir -p "$PROJECT_DIR/logs"

MAX_READY="${1:-5}"

echo "======================================================================"
echo "KHỞI ĐỘNG HỆ THỐNG CÀO & ĐĂNG TIKTOK TỰ ĐỘNG 24/7 (CÓ HOT-RELOAD)"
echo "  - Chống ngủ máy (caffeinate): ĐÃ BẬT"
echo "  - Tự động nhận code mới (Hot-Reload): ĐÃ BẬT"
echo "  - Bộ điều khiển máy tính: d-wwei/macos-desktop-control (CoreGraphics CGEvent)"
echo "  - Bộ tối ưu ngữ cảnh AI: yvgude/lean-ctx (SDE Context Gateway)"
echo "  - Số lượng video dự trữ mỗi kênh (max-ready): $MAX_READY"
echo "  - Cấu hình khung giờ đăng: $PROJECT_DIR/schedule_config.json"
echo "  - File log 24/7: $PROJECT_DIR/logs/daemon_24_7.log"
echo "  - Nhấn Ctrl + C để dừng."
echo "======================================================================"

# Chạy dưới caffeinate để Mac không bao giờ Sleep CPU/Wi-Fi/Disk
caffeinate -dimsu bash -c '
    trap "echo \"\"; echo \"[$(date \"+%Y-%m-%d %H:%M:%S\")] Đã nhận lệnh dừng từ người dùng (Ctrl+C).\"; kill 0 2>/dev/null; exit 130" INT TERM

    sync_from_github() {
        if [ -d .git ]; then
            git fetch origin main >/dev/null 2>&1 || true
            LOCAL_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
            REMOTE_SHA="$(git rev-parse origin/main 2>/dev/null || true)"
            if [ -n "$LOCAL_SHA" ] && [ -n "$REMOTE_SHA" ] && [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
                echo "[$(date "+%Y-%m-%d %H:%M:%S")] [Auto-Git-Sync] Phát hiện bản cập nhật mới trên GitHub (${LOCAL_SHA:0:7} -> ${REMOTE_SHA:0:7}), đang đồng bộ..." | tee -a logs/daemon_24_7.log
                cp -f story_audio_history.json /tmp/tiktok_story_audio_history.bak.json 2>/dev/null || true
                cp -f output/danh_sach_video.csv /tmp/tiktok_danh_sach_video.bak.csv 2>/dev/null || true
                git reset --hard origin/main >/dev/null 2>&1 || true
                cp -f /tmp/tiktok_story_audio_history.bak.json story_audio_history.json 2>/dev/null || true
                cp -f /tmp/tiktok_danh_sach_video.bak.csv output/danh_sach_video.csv 2>/dev/null || true
            fi
        fi
    }

    while true; do
        sync_from_github
        echo "[$(date "+%Y-%m-%d %H:%M:%S")] Đang chạy Worker 24/7..." | tee -a logs/daemon_24_7.log
        ./venv/bin/python main.py --stream-upload --max-ready "'"$MAX_READY"'" 2>&1 | tee -a logs/daemon_24_7.log
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
