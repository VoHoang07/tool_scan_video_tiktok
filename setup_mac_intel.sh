#!/usr/bin/env bash
# ==============================================================================
# SCRIPT CÀI ĐẶT TỰ ĐỘNG CHO MACBOOK CHIP INTEL (x86_64) & APPLE SILICON
# Cách chạy trên máy Mac Intel:
#   chmod +x setup_mac_intel.sh run_24_7.sh
#   ./setup_mac_intel.sh
# ==============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

ARCH="$(uname -m)"
echo "======================================================================"
echo "BẮT ĐẦU CÀI ĐẶT HỆ THỐNG CHO MACBOOK (Kiến trúc CPU: $ARCH)"
echo "Thư mục dự án: $PROJECT_DIR"
echo "======================================================================"

# 1. Cài đặt Homebrew & FFmpeg (Trên Mac Intel, Homebrew nằm ở /usr/local/bin)
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
if ! command -v brew >/dev/null 2>&1; then
    echo "[1/4] Chưa có Homebrew -> Đang cài đặt Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[1/4] Đang cài đặt ffmpeg qua Homebrew..."
    brew install ffmpeg
else
    echo "[1/4] Đã có ffmpeg: $(command -v ffmpeg)"
fi

# 2. Kiểm tra Python 3.10+ (Nếu Mac Intel chỉ có Python cũ thì cài python@3.11)
PYTHON_BIN=""
for candidate in /usr/local/bin/python3.11 /usr/local/bin/python3 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v "$candidate")"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "[2/4] Đang cài đặt Python 3.11 qua Homebrew..."
    brew install python@3.11
    PYTHON_BIN="/usr/local/bin/python3.11"
fi
echo "[2/4] Sử dụng Python: $PYTHON_BIN ($($PYTHON_BIN --version))"

# 3. Tạo mới môi trường ảo venv chuẩn theo kiến trúc CPU của máy hiện tại (x86_64 trên Mac Intel)
echo "[3/4] Đang khởi tạo lại môi trường ảo ./venv cho kiến trúc $ARCH..."
rm -rf "$PROJECT_DIR/venv"
"$PYTHON_BIN" -m venv "$PROJECT_DIR/venv"
"$PROJECT_DIR/venv/bin/pip" install --upgrade pip wheel setuptools
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# 4. Cài đặt trình duyệt tự động Playwright (dùng để tự động đăng TikTok theo khung giờ)
echo "[4/4] Đang cài đặt trình duyệt Playwright Chromium..."
"$PROJECT_DIR/venv/bin/playwright" install chromium

echo "======================================================================"
echo "HOÀN TẤT CÀI ĐẶT TRÊN MACBOOK ($ARCH)!"
echo "Bước tiếp theo:"
echo "  1. Đăng nhập 4 kênh TikTok 1 lần duy nhất:"
echo "     ./venv/bin/python main.py --login-channel video_AI_hay"
echo "     ./venv/bin/python main.py --login-channel baby_AI"
echo "     ./venv/bin/python main.py --login-channel audio_dem_khuyen"
echo "     ./venv/bin/python main.py --login-channel tu_chan_gioi"
echo "  2. Cài khung giờ tự động đăng bài mỗi ngày:"
echo "     ./venv/bin/python main.py --set-schedule '08:00,12:30,20:00'"
echo "  3. Bật chế độ chạy 24/7 (tự động cào + tự động đăng theo giờ):"
echo "     ./run_24_7.sh"
echo "======================================================================"
