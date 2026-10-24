#!/usr/bin/env bash
# ==============================================================================
# ĐỒNG BỘ TỰ ĐỘNG TỪ MÁY APPLE SILICON SANG MÁY MACBOOK INTEL QUA WI-FI (RSYNC)
# Cách dùng trên máy Silicon:
#   1. Đồng bộ ngay 1 lần:
#      ./sync_to_intel.sh ten_user@ip_may_intel
#   2. Chế độ tự động canh file (bấm Cmd+S sửa file ở máy Silicon là tự đẩy sang máy Intel):
#      ./sync_to_intel.sh ten_user@ip_may_intel --watch
# ==============================================================================
set -e

TARGET_HOST="$1"
MODE="$2"

if [ -z "$TARGET_HOST" ]; then
    echo "Cách dùng:"
    echo "  ./sync_to_intel.sh <user_may_intel>@<ip_may_intel> [--watch]"
    echo "Ví dụ:"
    echo "  ./sync_to_intel.sh vohoang@192.168.1.50 --watch"
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_DIR="~/Documents/tool_scan_video_tiktok"

do_sync() {
    rsync -azP --delete \
        --exclude 'venv/' \
        --exclude 'browser_profiles/' \
        --exclude 'workspace_temp/' \
        --exclude 'logs/' \
        --exclude '.DS_Store' \
        --exclude '__pycache__/' \
        --exclude 'output/*/*/*.mp4' \
        "$PROJECT_DIR/" "${TARGET_HOST}:${REMOTE_DIR}/"
    echo "[$(date "+%H:%M:%S")] Đã đồng bộ code & cấu hình sang máy Intel (${TARGET_HOST})!"
}

do_sync

if [ "$MODE" = "--watch" ]; then
    echo "=== ĐANG BẬT CHẾ ĐỘ TỰ ĐỘNG ĐỒNG BỘ SANG MÁY INTEL (${TARGET_HOST}) ==="
    echo "Bất kỳ thay đổi nào bạn lưu trên máy Silicon sẽ tự động cập nhật sang máy Intel sau 3 giây..."
    LAST_HASH=""
    while true; do
        CUR_HASH="$(find "$PROJECT_DIR/tiktok_pipeline" "$PROJECT_DIR/main.py" "$PROJECT_DIR/schedule_config.json" -type f -exec stat -f "%m %N" {} + 2>/dev/null | shasum)"
        if [ -n "$LAST_HASH" ] && [ "$CUR_HASH" != "$LAST_HASH" ]; then
            echo "[$(date "+%H:%M:%S")] Phát hiện thay đổi mới -> Đang đẩy sang máy Intel..."
            do_sync || true
        fi
        LAST_HASH="$CUR_HASH"
        sleep 3
    done
fi
