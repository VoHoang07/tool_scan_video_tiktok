#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "======================================================================"
echo "[1/3] Đang sao lưu lịch sử đăng bài & mã thiết bị (Fingerprint) 10 kênh..."
echo "======================================================================"
mkdir -p remote_telemetry/state_backup/fingerprints remote_telemetry/sessions

for f in uploaded_videos_registry.json posted_schedule_history.json growth_24h_history.json warmup_browse_history.json; do
    if [ -f "$f" ]; then
        cp -f "$f" "remote_telemetry/state_backup/$f"
        echo "  + Đã sao lưu: $f"
    fi
done

for prof_dir in browser_profiles/*/; do
    [ -d "$prof_dir" ] || continue
    ch_name="$(basename "$prof_dir")"
    if [ -f "$prof_dir/profile_fingerprint.json" ]; then
        cp -f "$prof_dir/profile_fingerprint.json" "remote_telemetry/state_backup/fingerprints/${ch_name}.json"
        echo "  + Đã sao lưu mã thiết bị kênh: $ch_name"
    fi
done

echo "======================================================================"
echo "[2/3] Đang mã hóa phiên đăng nhập 4 kênh chính (video_AI_hay, baby_AI, audio_dem_khuyen, tu_chan_gioi)..."
echo "======================================================================"
pkill -f "Google Chrome for Testing" 2>/dev/null || true

./venv/bin/python -c '
from pathlib import Path
from playwright.sync_api import sync_playwright
from tiktok_pipeline.auto_poster import TikTokAutoPoster

ap = TikTokAutoPoster(project_root=Path("."))
with sync_playwright() as p:
    for ch in ["video_AI_hay", "baby_AI", "audio_dem_khuyen", "tu_chan_gioi", "suc_khoe_duong_sinh", "phu_nu_khi_chat", "an_vat_moi_ngay", "meo_vat_gia_dinh", "nuoi_con_eq", "phu_kien_tien_ich"]:
        ud = ap.profiles_root / ch
        bin_file = ap.project_root / "remote_telemetry" / "sessions" / f"{ch}.bin"
        if not ud.exists():
            continue
        if bin_file.exists() and ch not in ("video_AI_hay", "baby_AI", "audio_dem_khuyen", "tu_chan_gioi"):
            print(f"  + Kênh [{ch}]: Đã có sẵn bản sao lưu ({bin_file.stat().st_size} bytes)")
            continue
        try:
            ap._cleanup_stale_chrome_profile_locks(ud)
            ctx = ap._launch_stealth_chrome_context(p, ud, headless=True)
            st = ctx.storage_state()
            out = ap._save_synced_channel_session(ch, st)
            ctx.close()
            if out and out.exists():
                print(f"  + ĐÃ SAO LƯU THÀNH CÔNG kênh [{ch}] -> {out.name} ({out.stat().st_size} bytes)")
            else:
                print(f"  ! Kênh [{ch}] chưa có cookie đăng nhập TikTok trong profile.")
        except Exception as exc:
            print(f"  ! Bỏ qua kênh [{ch}]: {exc}")
'

echo "======================================================================"
echo "[3/3] Đang đẩy toàn bộ bản sao lưu lên GitHub..."
echo "======================================================================"
git add remote_telemetry/sessions/ remote_telemetry/state_backup/
git commit -m "backup(intel): save all 10 channel sessions, fingerprints, and state histories before reset" || echo "Không có thay đổi mới cần commit."
git pull --rebase --autostash origin main
git push origin main

echo "======================================================================"
echo "✅ HOÀN TẤT 100%! Toàn bộ 10 kênh & lịch sử đã nằm an toàn trên GitHub."
echo "Bây giờ bạn có thể yên tâm Reset máy Intel!"
echo "======================================================================"
