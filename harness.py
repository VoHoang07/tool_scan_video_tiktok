#!/usr/bin/env python3
"""
harness.py — Bộ Kiểm Định Tự Động (Pre-Push Verification Gate) & Điều Khiển Nhanh 1 Bước
Dành cho Hệ Thống Tự Động Hóa 10 Kênh TikTok (`tool_scan_video_tiktok`).

Cách dùng nhanh:
  1. Kiểm tra toàn bộ hệ thống (chạy < 1 giây):
     ./venv/bin/python harness.py --verify
  2. Xem nhanh trạng thái 4 công tắc & báo cáo máy Intel mới nhất:
     ./venv/bin/python harness.py --status
  3. Bật/tắt công tắc và đẩy thẳng lên GitHub trong 1 bước:
     ./venv/bin/python harness.py --switch 2 on --push
     ./venv/bin/python harness.py --switch 2 off --switch 3 off --push
  4. Kiểm định toàn bộ và đẩy code lên origin/main nếu đạt 100%:
     ./venv/bin/python harness.py --verify-and-push -m "feat: mô tả commit"
"""

import argparse
import inspect
import json
import py_compile
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent

EXPECTED_POSTER_METHODS = {
    "__init__",
    "ensure_schedule_config",
    "update_schedule_times",
    "_cleanup_stale_chrome_profile_locks",
    "_refresh_browser_process_lock",
    "_acquire_browser_process_lock",
    "_release_browser_process_lock",
    "_get_or_create_channel_fingerprint",
    "_parse_playwright_proxy",
    "_launch_stealth_chrome_context",
    "_session_cipher_bytes",
    "_save_synced_channel_session",
    "_restore_synced_channel_session_if_needed",
    "_human_move_mouse",
    "_os_click_viewport",
    "_dismiss_all_studio_popovers",
    "_simulate_human_warmup",
    "_scroll_upload_form",
    "_move_caret_to_end_of_editor",
    "_select_hashtag_suggestion_if_open",
    "_clear_and_type_caption_hashtags",
    "_click_public_option_in_open_dropdown",
    "_set_switch_near_label",
    "detect_video_aigc_disclosure_needed",
    "_configure_upload_settings_public",
    "_verify_upload_privacy_is_everyone",
    "_is_video_upload_100_percent_done",
    "_find_exact_post_button",
    "_click_confirm_modal_if_present",
    "_ensure_posts_public_in_content_page",
    "_deduplicate_existing_posts_in_content_page",
    "_delete_post_in_content_page",
    "delete_channel_posts_by_keywords",
    "ensure_channel_posts_public",
    "login_channel_profile",
    "_quick_video_fingerprint",
    "_audio_story_fingerprint",
    "_record_uploaded_video_in_registry",
    "_get_next_ready_video_for_channel",
    "_was_channel_uploaded_recently",
    "upload_one_channel_video",
    "_heal_git_repo_state",
    "_save_and_push_telemetry",
    "run_24h_filter_for_channel",
    "_load_post_history",
    "_save_post_history",
    "_compute_daily_jitter_minutes",
    "check_and_run_stream1_upload_schedule",
    "_ensure_intel_bridge_daemon_running",
    "_is_stream1_upload_process_running",
    "_is_stream2_filter24h_process_running",
    "_auto_revive_stream1_upload_if_dead",
    "_auto_revive_stream2_filter24h_if_dead",
    "check_and_run_stream2_filter24h_schedule",
    "_focus_and_play_tiktok_video",
    "_scroll_to_next_fyp_video",
    "warmup_browse_channel",
    "check_and_run_warmup_browse",
    "check_and_run_scheduled_posts",
}

EXPECTED_10_CHANNELS = {
    "video_AI_hay",
    "baby_AI",
    "audio_dem_khuyen",
    "tu_chan_gioi",
    "meo_vat_gia_dinh",
    "suc_khoe_duong_sinh",
    "phu_nu_khi_chat",
    "an_vat_moi_ngay",
    "nuoi_con_eq",
    "phu_kien_tien_ich",
}


def run_verification_suite() -> Tuple[bool, List[str]]:
    """Chạy 4 lớp kiểm định tự động trước khi commit/push."""
    t0 = time.time()
    errors: List[str] = []
    passed_checks: List[str] = []

    # 1. Kiểm tra cú pháp toàn bộ file .py
    py_files = sorted(PROJECT_ROOT.glob("*.py")) + sorted((PROJECT_ROOT / "tiktok_pipeline").glob("*.py"))
    for py_file in py_files:
        try:
            py_compile.compile(str(py_file), doraise=True)
        except Exception as exc:
            errors.append(f"[SyntaxError] {py_file.relative_to(PROJECT_ROOT)}: {exc}")
    if not errors:
        passed_checks.append(f"Syntax & Bytecode: {len(py_files)} file .py hợp lệ 100%")

    # 2. Kiểm tra cấu hình schedule_config.json & 4 công tắc
    cfg_path = PROJECT_ROOT / "schedule_config.json"
    if not cfg_path.exists():
        errors.append("[ConfigError] Thiếu file schedule_config.json")
    else:
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            ch_times = cfg.get("channel_post_times") or {}
            missing_ch = EXPECTED_10_CHANNELS - set(ch_times.keys())
            if missing_ch:
                errors.append(f"[ConfigError] Thiếu lịch đăng của kênh: {sorted(missing_ch)}")
            sw = cfg.get("system_switches") or {}
            for k in ("master_switch", "switch_1_factory", "switch_2_upload", "switch_3_interact", "switch_4_clear"):
                if k not in sw:
                    errors.append(f"[ConfigError] Thiếu khóa '{k}' trong system_switches")
            if not errors:
                passed_checks.append("Schedule & Switches: Đủ 10 kênh và 4 công tắc trong schedule_config.json")
        except Exception as exc:
            errors.append(f"[ConfigError] schedule_config.json lỗi JSON: {exc}")

    # 3. Kiểm tra kiến trúc Mixin & đủ 59 hàm của TikTokAutoPoster
    try:
        from tiktok_pipeline.auto_poster import TikTokAutoPoster

        actual_methods = {
            name
            for name in dir(TikTokAutoPoster)
            if (not name.startswith("__") or name == "__init__") and callable(getattr(TikTokAutoPoster, name))
        }
        missing_m = EXPECTED_POSTER_METHODS - actual_methods
        if missing_m:
            errors.append(f"[PosterMixinError] TikTokAutoPoster thiếu hàm: {sorted(missing_m)}")
        else:
            passed_checks.append(
                f"Modular TikTokAutoPoster: Đủ {len(EXPECTED_POSTER_METHODS)}/59 hàm trên 6 file Mixin"
            )
    except Exception as exc:
        errors.append(f"[ImportError] Không thể import TikTokAutoPoster: {exc}")

    # 4. Kiểm tra Lá chắn Quy tắc Cộng đồng & Catalog
    try:
        from tiktok_pipeline.community_guidelines_guard import TikTokCommunityGuidelinesGuard

        if not TikTokCommunityGuidelinesGuard.is_episode_blocked("audio_dem_khuyen", 145):
            errors.append("[CGGuardError] Tập 145 của audio_dem_khuyen chưa bị chặn!")
        if not TikTokCommunityGuidelinesGuard.is_episode_blocked("video_AI_hay", 84):
            errors.append("[CGGuardError] Tập nhạy cảm 84 của video_AI_hay chưa bị chặn!")

        bc_path = PROJECT_ROOT / "data" / "bongcuc_full_catalog.json"
        if bc_path.exists():
            bc_items = json.loads(bc_path.read_text(encoding="utf-8"))
            missing_dur = [x.get("ep_num") for x in bc_items if int(x.get("duration") or 0) <= 0]
            blocked_in_bc = [
                x.get("ep_num")
                for x in bc_items
                if TikTokCommunityGuidelinesGuard.is_episode_blocked("audio_dem_khuyen", int(x.get("ep_num") or 0))
            ]
            if missing_dur:
                errors.append(f"[CatalogError] bongcuc_full_catalog.json có tập thiếu duration: {missing_dur}")
            if blocked_in_bc:
                errors.append(f"[CatalogError] bongcuc_full_catalog.json còn chứa tập bị chặn: {blocked_in_bc}")

        gsh_path = PROJECT_ROOT / "data" / "gausohai_full_catalog.json"
        if gsh_path.exists():
            gsh_items = json.loads(gsh_path.read_text(encoding="utf-8"))
            blocked_in_gsh = [
                x.get("ep_num")
                for x in gsh_items
                if TikTokCommunityGuidelinesGuard.is_episode_blocked("video_AI_hay", int(x.get("ep_num") or 0))
            ]
            if blocked_in_gsh:
                errors.append(f"[CatalogError] gausohai_full_catalog.json còn chứa tập bị chặn: {blocked_in_gsh}")

        if not errors:
            passed_checks.append(
                f"Community Guidelines & Catalogs: {len(bc_items)} tập BC + {len(gsh_items)} tập GSH đạt chuẩn 100%"
            )
    except Exception as exc:
        errors.append(f"[CGGuardError] Lỗi kiểm tra CommunityGuidelinesGuard: {exc}")

    elapsed = (time.time() - t0) * 1000.0
    print(f"=== HARNESS VERIFICATION REPORT ({elapsed:.0f} ms) ===")
    for msg in passed_checks:
        print(f"  [PASS] {msg}")
    for err in errors:
        print(f"  [FAIL] {err}")
    return (len(errors) == 0), errors


def print_quick_status() -> None:
    """In tóm tắt siêu tốc trạng thái công tắc và báo cáo máy Intel mới nhất."""
    from tiktok_pipeline.master_switch_controller import MasterSwitchController

    ctrl = MasterSwitchController(project_root=PROJECT_ROOT)
    sw = ctrl.get_switches()
    print("=== TRẠNG THÁI CÔNG TẮC HỆ THỐNG (schedule_config.json) ===")
    print(f"  • Master Switch          : {'BẬT (ON)' if sw.get('master_switch') else 'TẮT (OFF)'}")
    print(f"  • Công tắc 1 (Factory)   : {'BẬT (ON)' if (sw.get('switch_1_factory') or {}).get('enabled') else 'TẮT (OFF)'}")
    print(f"  • Công tắc 2 (Upload TT) : {'BẬT (ON)' if (sw.get('switch_2_upload') or {}).get('tiktok') else 'TẮT (OFF)'}")
    print(f"  • Công tắc 3 (Interact)  : {'BẬT (ON)' if (sw.get('switch_3_interact') or {}).get('tiktok') else 'TẮT (OFF)'}")
    print(f"  • Công tắc 4 (AutoClear) : {'BẬT (ON)' if (sw.get('switch_4_clear') or {}).get('enabled') else 'TẮT (OFF)'}")

    report_path = PROJECT_ROOT / "remote_telemetry" / "intel_status_report.json"
    if report_path.exists():
        try:
            rep = json.loads(report_path.read_text(encoding="utf-8"))
            print("\n=== BÁO CÁO TELEMETRY MÁY MAC INTEL MỚI NHẤT ===")
            print(f"  • Cập nhật lúc : {rep.get('timestamp') or rep.get('generated_at')}")
            print(f"  • Hành động    : [{rep.get('action_type')}] kênh {rep.get('channel')} -> {rep.get('status')}")
            ph = rep.get("post_history_today") or {}
            if isinstance(ph, dict) and ph:
                print("  • Lịch sử đăng hôm nay:")
                for ch, times in sorted(ph.items()):
                    print(f"      - {ch:20s}: {times}")
        except Exception as exc:
            print(f"  [!] Không đọc được intel_status_report.json: {exc}")


def toggle_switches(pairs: List[List[str]], auto_push: bool = False) -> int:
    """Bật/tắt nhanh công tắc trong schedule_config.json và tùy chọn đẩy Git 1 bước."""
    from tiktok_pipeline.master_switch_controller import MasterSwitchController

    ctrl = MasterSwitchController(project_root=PROJECT_ROOT)
    changed_desc: List[str] = []
    for target, state_str in pairs:
        t_clean = str(target).strip().lower()
        val = str(state_str).strip().lower() in ("on", "true", "1", "bat", "bật")
        if t_clean in ("master", "0"):
            ctrl.set_switches(master_switch=val)
            changed_desc.append(f"master={'ON' if val else 'OFF'}")
        elif t_clean in ("1", "factory", "switch1"):
            ctrl.set_switches(switch_1_enabled=val)
            changed_desc.append(f"switch_1={'ON' if val else 'OFF'}")
        elif t_clean in ("2", "upload", "switch2"):
            ctrl.set_switches(switch_2_tiktok=val)
            changed_desc.append(f"switch_2={'ON' if val else 'OFF'}")
        elif t_clean in ("3", "interact", "switch3"):
            ctrl.set_switches(switch_3_tiktok=val)
            changed_desc.append(f"switch_3={'ON' if val else 'OFF'}")
        elif t_clean in ("4", "clear", "switch4"):
            ctrl.set_switches(switch_4_enabled=val)
            changed_desc.append(f"switch_4={'ON' if val else 'OFF'}")
        else:
            print(f"[Lỗi] Công tắc không hợp lệ: {target} (chọn 1, 2, 3, 4 hoặc master)")
            return 1

    print_quick_status()
    if auto_push and changed_desc:
        msg = f"chore(switch): toggle {', '.join(changed_desc)}"
        cmd = (
            f'git add schedule_config.json && '
            f'git commit -m "{msg}" && '
            f'git pull --rebase --autostash origin main && '
            f'git push origin main'
        )
        res = subprocess.run(cmd, shell=True, cwd=str(PROJECT_ROOT))
        return res.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Harness Kiểm Định & Điều Khiển Nhanh 10 Kênh TikTok")
    parser.add_argument("--verify", action="store_true", help="Chạy 4 lớp kiểm định hệ thống")
    parser.add_argument("--status", action="store_true", help="Xem nhanh trạng thái công tắc & máy Intel")
    parser.add_argument(
        "--switch",
        nargs=2,
        action="append",
        metavar=("TARGET", "STATE"),
        help="Bật/tắt công tắc: --switch <1|2|3|4|master> <on|off>",
    )
    parser.add_argument("--push", action="store_true", help="Tự động commit & push sau khi đổi công tắc")
    parser.add_argument(
        "--verify-and-push",
        action="store_true",
        help="Chạy kiểm định 4 lớp; nếu đạt 100% thì commit và push lên origin/main",
    )
    parser.add_argument("-m", "--message", type=str, default="chore: verified update via harness", help="Commit message")

    args = parser.parse_args()

    if args.switch:
        return toggle_switches(args.switch, auto_push=args.push)

    if args.status:
        print_quick_status()
        return 0

    if args.verify_and_push:
        ok, _ = run_verification_suite()
        if not ok:
            print("[ABORT] Kiểm định thất bại, hủy đẩy code lên origin/main!")
            return 1
        cmd = (
            f'git add -u && '
            f'git commit -m "{args.message}" && '
            f'git pull --rebase --autostash origin main && '
            f'git push origin main'
        )
        return subprocess.run(cmd, shell=True, cwd=str(PROJECT_ROOT)).returncode

    ok, _ = run_verification_suite()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
