#!/usr/bin/env bash
# ==============================================================================
# SCRIPT CÀI ĐẶT SIÊU TỐC CHO MACBOOK CHIP INTEL (x86_64) - KHÔNG CẦN BREW COMPILE
# Khắc phục triệt để lỗi "Tier 3 configuration" của Homebrew trên macOS Intel.
# Tự động tải bản dựng sẵn (Pre-built binary) của Python 3.11 + FFmpeg + Thư viện.
# Cách chạy trên máy Mac Intel:
#   git pull && ./setup_mac_intel.sh
# ==============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

ARCH="$(uname -m)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

echo "======================================================================"
echo "BẮT ĐẦU CÀI ĐẶT NHANH CHO MACBOOK (Kiến trúc CPU: $ARCH)"
echo "Thư mục dự án: $PROJECT_DIR"
echo "======================================================================"

# 1. Cài đặt công cụ quản lý Python siêu tốc `uv` (tải file thực thi dựng sẵn, không cần Homebrew)
if ! command -v uv >/dev/null 2>&1; then
    echo "[1/4] Đang tải trình quản lý Python dựng sẵn (uv)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# 2. Tạo môi trường ảo ./venv với Python 3.11 dựng sẵn (Pre-built binary cho Mac Intel x86_64)
echo "[2/4] Đang tạo môi trường ảo ./venv với Python 3.11 chuẩn cho $ARCH..."
rm -rf "$PROJECT_DIR/venv"

if command -v uv >/dev/null 2>&1; then
    uv venv "$PROJECT_DIR/venv" --python 3.11 --seed
    echo "[3/4] Đang cài đặt các thư viện từ requirements.txt..."
    uv pip install --python "$PROJECT_DIR/venv/bin/python" -r "$PROJECT_DIR/requirements.txt"
else
    PYTHON_BIN="$(command -v python3 || true)"
    if [ -z "$PYTHON_BIN" ]; then
        echo "Lỗi: Không tìm thấy python3 hoặc uv."
        exit 1
    fi
    "$PYTHON_BIN" -m venv "$PROJECT_DIR/venv"
    "$PROJECT_DIR/venv/bin/pip" install --upgrade pip wheel setuptools
    "$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
fi

# 3. Kích hoạt FFmpeg dựng sẵn (static-ffmpeg) ngay trong ./venv/bin (không cần brew install ffmpeg)
echo "[3.5/4] Đang cấu hình FFmpeg dựng sẵn cho Mac Intel..."
"$PROJECT_DIR/venv/bin/python" -c "from tiktok_pipeline.utils import ensure_ffmpeg; print('Đã cài xong FFmpeg:', ensure_ffmpeg()[0])"

# 4. Cài đặt trình duyệt Playwright Chromium
echo "[4/4] Đang cài đặt trình duyệt tự động Playwright..."
"$PROJECT_DIR/venv/bin/playwright" install chromium || echo "Lưu ý: Sẽ sử dụng trực tiếp Google Chrome trên máy Mac."

echo "======================================================================"
echo "HOÀN TẤT CÀI ĐẶT 100% TRÊN MACBOOK ($ARCH)!"
echo "Kiểm tra Python: $("$PROJECT_DIR/venv/bin/python" --version)"
echo "----------------------------------------------------------------------"
echo "Bây giờ bạn chạy lần lượt 4 lệnh sau để quét mã QR đăng nhập 4 kênh:"
echo "  ./venv/bin/python main.py --login-channel video_AI_hay"
echo "  ./venv/bin/python main.py --login-channel baby_AI"
echo "  ./venv/bin/python main.py --login-channel audio_dem_khuyen"
echo "  ./venv/bin/python main.py --login-channel tu_chan_gioi"
echo ""
echo "Sau đó bật chế độ chạy 24/7:"
echo "  ./run_24_7.sh"
echo "======================================================================"
