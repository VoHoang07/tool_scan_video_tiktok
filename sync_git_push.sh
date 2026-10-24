#!/usr/bin/env bash
# ==============================================================================
# ĐẨY NHANH CẬP NHẬT LÊN GITHUB ĐỂ MÁY MACBOOK INTEL TỰ ĐỘNG KÉO VỀ (AUTO-PULL)
# Cách dùng trên máy Silicon sau khi sửa code / lịch đăng:
#   ./sync_git_push.sh
# ==============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

MSG="${1:-Auto sync update from Silicon Mac $(date '+%Y-%m-%d %H:%M:%S')}"

git add -A
if ! git diff --cached --quiet; then
    git commit -m "$MSG"
fi

git push -u origin main
echo "======================================================================"
echo "ĐÃ ĐẨY BẢN CẬP NHẬT LÊN GITHUB THÀNH CÔNG!"
echo "Máy MacBook Intel đang chạy ./run_24_7.sh sẽ tự động kéo về (git pull)"
echo "và tự khởi động lại Worker (Hot-Reload) trong vòng 30 giây tới."
echo "======================================================================"
