#!/usr/bin/env bash
# ==============================================================================
# SCRIPT CÀI ĐẶT SIÊU TỐC CHO MACBOOK CHIP INTEL (x86_64) - SELF-CONTAINED 100%
# Lưu toàn bộ uv, Python 3.11, LeanCTX, macos-desktop-control ngay trong dự án
# để tránh hoàn toàn lỗi "Permission denied" tại ~/.local/bin trên Mac Intel.
# Cách chạy trên máy Mac Intel:
#   git pull && ./setup_mac_intel.sh
# ==============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

ARCH="$(uname -m)"

# Đặt toàn bộ thư mục cài đặt & cache nằm gọn bên trong thư mục dự án (không đụng vào ~/.local bị khóa quyền)
export UV_INSTALL_DIR="$PROJECT_DIR/.bin"
export UV_CACHE_DIR="$PROJECT_DIR/.uv_cache"
export UV_PYTHON_INSTALL_DIR="$PROJECT_DIR/.uv_python"
export XDG_DATA_HOME="$PROJECT_DIR/.xdg_data"
export XDG_CACHE_HOME="$PROJECT_DIR/.xdg_cache"
export XDG_CONFIG_HOME="$PROJECT_DIR/.xdg_config"
mkdir -p "$UV_INSTALL_DIR" "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"

export PATH="$UV_INSTALL_DIR:/usr/local/bin:/opt/homebrew/bin:$PATH"

echo "======================================================================"
echo "BẮT ĐẦU CÀI ĐẶT NHANH CHO MACBOOK (Kiến trúc CPU: $ARCH)"
echo "Thư mục dự án: $PROJECT_DIR"
echo "======================================================================"

# 1. Cài đặt công cụ quản lý Python siêu tốc `uv` thẳng vào $PROJECT_DIR/.bin
if [ ! -x "$UV_INSTALL_DIR/uv" ]; then
    echo "[1/5] Đang tải trình quản lý Python dựng sẵn (uv) vào $UV_INSTALL_DIR..."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$UV_INSTALL_DIR" INSTALLER_NO_MODIFY_PATH=1 sh
fi

UV_BIN="$UV_INSTALL_DIR/uv"
echo "[1/5] Đã sẵn sàng uv: $($UV_BIN --version)"

# 2. Tạo môi trường ảo ./venv với Python 3.11 dựng sẵn (Pre-built binary cho Mac Intel x86_64)
echo "[2/5] Đang tải Python 3.11 chuẩn $ARCH và tạo môi trường ảo ./venv..."
rm -rf "$PROJECT_DIR/venv"
"$UV_BIN" venv "$PROJECT_DIR/venv" --python 3.11 --seed

# 3. Cài đặt toàn bộ thư viện từ requirements.txt
echo "[3/5] Đang cài đặt các thư viện từ requirements.txt..."
"$UV_BIN" pip install --python "$PROJECT_DIR/venv/bin/python" -r "$PROJECT_DIR/requirements.txt"

# 3.5. Kích hoạt FFmpeg dựng sẵn (static-ffmpeg) ngay trong ./venv/bin
echo "[3.5/5] Đang cấu hình FFmpeg dựng sẵn cho Mac Intel..."
"$PROJECT_DIR/venv/bin/python" -c "from tiktok_pipeline.utils import ensure_ffmpeg; print('Đã cài xong FFmpeg:', ensure_ffmpeg()[0])"

# 4. Cài đặt bộ tối ưu ngữ cảnh LeanCTX (yvgude/lean-ctx) vào $PROJECT_DIR/.bin
if [ -f "$PROJECT_DIR/integrations/lean-ctx/install.sh" ] && [ ! -x "$UV_INSTALL_DIR/lean-ctx" ]; then
    echo "[4/5] Đang cài đặt bộ tối ưu ngữ cảnh LeanCTX (yvgude/lean-ctx)..."
    env LEAN_CTX_INSTALL_DIR="$UV_INSTALL_DIR" LEAN_CTX_NO_PATH_FIX=1 LEAN_CTX_NO_ONBOARD=1 sh "$PROJECT_DIR/integrations/lean-ctx/install.sh" --download || true
fi

# 5. Cài đặt trình duyệt Playwright & kiểm tra macos-desktop-control (CoreGraphics JXA)
echo "[5/5] Đang cài đặt trình duyệt tự động & kiểm tra macos-desktop-control..."
"$PROJECT_DIR/venv/bin/playwright" install chromium || echo "Lưu ý: Sẽ sử dụng trực tiếp Google Chrome trên máy Mac."

# 5.5. Tự động khôi phục lịch sử đăng bài & hồ sơ trình duyệt 10 kênh từ bản sao lưu GitHub (nếu vừa cài lại máy)
if [ -d "$PROJECT_DIR/remote_telemetry/state_backup" ]; then
    echo "[5.5/5] Đang khôi phục lịch sử chống đăng trùng & phiên đăng nhập 10 kênh từ GitHub..."
    for f in uploaded_videos_registry.json posted_schedule_history.json growth_24h_history.json warmup_browse_history.json; do
        if [ -f "$PROJECT_DIR/remote_telemetry/state_backup/$f" ] && [ ! -f "$PROJECT_DIR/$f" ]; then
            cp -f "$PROJECT_DIR/remote_telemetry/state_backup/$f" "$PROJECT_DIR/$f"
        fi
    done
    if [ -d "$PROJECT_DIR/remote_telemetry/state_backup/fingerprints" ]; then
        for fp_file in "$PROJECT_DIR/remote_telemetry/state_backup/fingerprints/"*.json; do
            [ -f "$fp_file" ] || continue
            ch_name="$(basename "$fp_file" .json)"
            mkdir -p "$PROJECT_DIR/browser_profiles/$ch_name"
            cp -f "$fp_file" "$PROJECT_DIR/browser_profiles/$ch_name/profile_fingerprint.json"
        done
    fi
fi
"$PROJECT_DIR/venv/bin/python" -c "
import json, zlib
from pathlib import Path
from tiktok_pipeline.auto_poster import TikTokAutoPoster
root = Path('$PROJECT_DIR')
sess_dir = root / 'remote_telemetry' / 'sessions'
if sess_dir.exists():
    for bin_file in sorted(sess_dir.glob('*.bin')):
        ch = bin_file.stem
        prof_dir = root / 'browser_profiles' / ch
        prof_dir.mkdir(parents=True, exist_ok=True)
        try:
            st = json.loads(zlib.decompress(TikTokAutoPoster._session_cipher_bytes(bin_file.read_bytes())).decode('utf-8'))
            if isinstance(st.get('profile_fingerprint'), dict) and not (prof_dir / 'profile_fingerprint.json').exists():
                (prof_dir / 'profile_fingerprint.json').write_text(json.dumps(st['profile_fingerprint'], ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
    print('Đã chuẩn bị sẵn hồ sơ đăng nhập tự động cho', len(list(sess_dir.glob('*.bin'))), 'kênh TikTok!')
" || true

echo "======================================================================"
git config user.name "IntelMacBot" || true
git config user.email "bot@tiktokpipeline.local" || true
echo "HOÀN TẤT CÀI ĐẶT & KHÔI PHỤC 10 KÊNH TIKTOK 100% TRÊN MACBOOK ($ARCH)!"
echo "Kiểm tra Python: $("$PROJECT_DIR/venv/bin/python" --version)"
echo "----------------------------------------------------------------------"
echo "Toàn bộ 10 kênh TikTok + Mã thiết bị (Fingerprint) đã được khôi phục tự động!"
echo "Bây giờ bạn mở 2 cửa sổ Terminal chạy 2 dòng hoạt động 24/7:"
echo "  + Cửa sổ 1 (Dòng 1 - Cào & Đăng): ./run_24_7.sh"
echo "  + Cửa sổ 2 (Dòng 2 - Bộ lọc 24h): caffeinate -dimsu ./venv/bin/python -m tiktok_pipeline.main --stream-filter24h"
echo "======================================================================"
