#!/usr/bin/env python3
"""
Cầu nối Điều Khiển Từ Xa GitHub <-> Antigravity CLI (AGY) trên Mac Intel (`x86_64`).

Kiến trúc hoạt động:
1. Máy Silicon (hoặc người dùng) ghi lệnh vào `remote_telemetry/agy_bridge_inbox.json` và push lên GitHub.
2. Trên máy Mac Intel ở nhà, tiến trình độc lập `agy_intel_bridge.py --daemon` chạy ngầm (tách biệt hoàn toàn
   khỏi Dòng 1 & Dòng 2 nên không bao giờ bị treo theo Chrome/Playwright):
   - Mỗi 20 giây tự động `git fetch origin main` và đọc `remote_telemetry/agy_bridge_inbox.json`.
   - Nếu phát hiện `cmd_id` mới chưa thực thi:
     + `action == "status_and_screenshot"`: Chụp màn hình Desktop Mac Intel (`screencapture -x`), kiểm tra
       trạng thái Dòng 1, Dòng 2, tiến trình Chrome và khóa `.tiktok_browser_session.lock`.
     + `action == "unstick_chrome"`: Đóng cưỡng bức các tiến trình `Google Chrome for Testing` bị treo,
       xóa `SingletonLock` và giải phóng `.tiktok_browser_session.lock` để cứu Dòng 1 & Dòng 2.
     + `action == "shell"`: Chạy câu lệnh hệ thống được chỉ định và thu thập `stdout` / `stderr`.
     + `action == "agy_prompt"`: Gọi trực tiếp `agy -p "<prompt>" --dangerously-skip-permissions` trên máy Intel
       để AI Agent tại chỗ tự phân tích, tự điều khiển máy Intel và trả kết quả về.
   - Ghi toàn bộ kết quả + ảnh chụp màn hình Desktop vào `remote_telemetry/agy_bridge_outbox.json`
     và tự động `git commit` + `git push origin main` để máy Silicon đọc tức thì.
3. Tự động Tuần Tra (Auto-Healer): Mỗi 60 giây kiểm tra nếu khóa `.tiktok_browser_session.lock` bị giữ quá
   420 giây (7 phút) thì tự động giải phóng Chrome bị đơ ngay tại chỗ.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INBOX_PATH = PROJECT_ROOT / "remote_telemetry" / "agy_bridge_inbox.json"
OUTBOX_PATH = PROJECT_ROOT / "remote_telemetry" / "agy_bridge_outbox.json"
DESKTOP_SCREENSHOT_PATH = PROJECT_ROOT / "remote_telemetry" / "latest_intel_desktop.jpg"
STATE_FILE = PROJECT_ROOT / "workspace_temp" / ".agy_bridge_last_cmd.json"


def _now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _find_agy_bin() -> str:
    candidates = [
        shutil.which("agy"),
        str(Path.home() / ".local" / "bin" / "agy"),
        "/usr/local/bin/agy",
        "/opt/homebrew/bin/agy",
    ]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return str(c)
    return "agy"


def _collect_system_health() -> Dict[str, Any]:
    """Thu thập trạng thái thực tế của Dòng 1, Dòng 2, Chrome và khóa trình duyệt trên máy Mac Intel."""
    health: Dict[str, Any] = {
        "timestamp": _now_str(),
        "machine_arch": platform.machine(),
        "hostname": platform.node(),
        "stream1_running": False,
        "stream2_running": False,
        "chrome_testing_count": 0,
        "lock_file_age_seconds": None,
        "processes": [],
        "recent_daemon_log_tail": [],
    }
    try:
        ps_res = subprocess.run(
            ["ps", "-Ao", "pid,etime,command"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        matched_procs: List[str] = []
        chrome_count = 0
        for line in (ps_res.stdout or "").splitlines():
            if "--stream-upload" in line and "grep" not in line:
                health["stream1_running"] = True
                matched_procs.append(line.strip())
            elif "--stream-filter24h" in line and "grep" not in line:
                health["stream2_running"] = True
                matched_procs.append(line.strip())
            elif "Google Chrome for Testing" in line and "grep" not in line:
                chrome_count += 1
        health["chrome_testing_count"] = chrome_count
        health["processes"] = matched_procs[:15]
    except Exception as exc:
        health["ps_error"] = str(exc)

    lock_file = PROJECT_ROOT / "workspace_temp" / ".tiktok_browser_session.lock"
    if lock_file.exists():
        try:
            age = round(time.time() - lock_file.stat().st_mtime, 1)
            health["lock_file_age_seconds"] = age
            health["lock_file_content"] = lock_file.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            pass

    log_file = PROJECT_ROOT / "logs" / "daemon_24_7.log"
    if log_file.exists():
        try:
            tail_res = subprocess.run(
                ["tail", "-n", "20", str(log_file)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            health["recent_daemon_log_tail"] = [
                ln for ln in (tail_res.stdout or "").splitlines()[-20:] if ln.strip()
            ]
        except Exception:
            pass

    return health


def _capture_desktop_screenshot() -> Optional[str]:
    """Chụp toàn màn hình Desktop Mac Intel nén nhẹ thành JPG để đẩy về GitHub."""
    DESKTOP_SCREENSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_png = PROJECT_ROOT / "workspace_temp" / "_tmp_desktop_cap.png"
    tmp_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["screencapture", "-x", str(tmp_png)],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if tmp_png.exists() and tmp_png.stat().st_size > 0:
            subprocess.run(
                [
                    "sips",
                    "-s",
                    "format",
                    "jpeg",
                    "-s",
                    "formatOptions",
                    "55",
                    "--resampleWidth",
                    "1280",
                    str(tmp_png),
                    "--out",
                    str(DESKTOP_SCREENSHOT_PATH),
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
            tmp_png.unlink(missing_ok=True)
            if DESKTOP_SCREENSHOT_PATH.exists():
                return str(DESKTOP_SCREENSHOT_PATH.relative_to(PROJECT_ROOT))
    except Exception:
        pass
    return None


def _unstick_chrome_and_locks() -> Dict[str, Any]:
    """Đóng các tiến trình Chrome for Testing bị treo và dọn sạch SingletonLock / browser lock."""
    killed_chrome = False
    removed_locks: List[str] = []
    try:
        res = subprocess.run(
            ["pkill", "-9", "-f", "Google Chrome for Testing"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        killed_chrome = res.returncode == 0
    except Exception:
        pass

    profiles_root = PROJECT_ROOT / "workspace_temp" / "tiktok_profiles"
    if profiles_root.exists():
        for lock_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            for lf in profiles_root.glob(f"*/{lock_name}"):
                try:
                    lf.unlink()
                    removed_locks.append(str(lf.relative_to(PROJECT_ROOT)))
                except Exception:
                    pass

    sess_lock = PROJECT_ROOT / "workspace_temp" / ".tiktok_browser_session.lock"
    if sess_lock.exists():
        try:
            sess_lock.unlink()
            removed_locks.append(str(sess_lock.relative_to(PROJECT_ROOT)))
        except Exception:
            pass

    return {
        "killed_chrome_for_testing": killed_chrome,
        "removed_locks": removed_locks,
        "timestamp": _now_str(),
    }


def _execute_inbox_command(cmd: Dict[str, Any]) -> Dict[str, Any]:
    """Thực thi lệnh từ `agy_bridge_inbox.json` trên máy Mac Intel."""
    cmd_id = str(cmd.get("cmd_id", "")).strip()
    action = str(cmd.get("action", "status_and_screenshot")).strip()
    prompt = str(cmd.get("agy_prompt", "")).strip()
    shell_cmd = str(cmd.get("shell_command", "")).strip()
    timeout_sec = int(cmd.get("timeout_seconds", 240))

    result: Dict[str, Any] = {
        "cmd_id": cmd_id,
        "action": action,
        "executed_at": _now_str(),
        "machine_arch": platform.machine(),
        "hostname": platform.node(),
        "status": "SUCCESS",
    }

    try:
        if action in ("unstick_chrome", "check_and_heal"):
            result["unstick_report"] = _unstick_chrome_and_locks()

        if action == "shell" and shell_cmd:
            proc = subprocess.run(
                shell_cmd,
                shell=True,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            result["exit_code"] = proc.returncode
            result["stdout"] = (proc.stdout or "")[-4000:]
            result["stderr"] = (proc.stderr or "")[-2000:]

        if action == "agy_prompt" or prompt:
            agy_bin = _find_agy_bin()
            proc = subprocess.run(
                [
                    agy_bin,
                    "-p",
                    prompt,
                    "--dangerously-skip-permissions",
                    "--print-timeout",
                    f"{timeout_sec}s",
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=timeout_sec + 15,
                check=False,
            )
            result["agy_exit_code"] = proc.returncode
            result["agy_response"] = (proc.stdout or "")[-6000:]
            if proc.stderr:
                result["agy_stderr"] = (proc.stderr or "")[-2000:]

        screenshot_rel = _capture_desktop_screenshot()
        if screenshot_rel:
            result["desktop_screenshot"] = screenshot_rel
        result["system_health"] = _collect_system_health()
    except Exception as exc:
        result["status"] = "ERROR"
        result["error"] = str(exc)

    return result


def _git_sync_and_push_outbox(outbox_payload: Dict[str, Any]) -> None:
    """Lưu `agy_bridge_outbox.json` và đẩy thẳng lên GitHub từ máy Mac Intel."""
    OUTBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTBOX_PATH.write_text(
        json.dumps(outbox_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    files_to_add = [str(OUTBOX_PATH.relative_to(PROJECT_ROOT))]
    if DESKTOP_SCREENSHOT_PATH.exists():
        files_to_add.append(str(DESKTOP_SCREENSHOT_PATH.relative_to(PROJECT_ROOT)))

    try:
        subprocess.run(
            ["git", "add", "-f", *files_to_add],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=10,
            check=False,
        )
        cmd_id = outbox_payload.get("latest_result", {}).get("cmd_id", "heartbeat")
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=IntelAGYBridge",
                "-c",
                "user.email=bridge@tiktokpipeline.local",
                "commit",
                "-m",
                f"AGY-Bridge [outbox] {cmd_id} at {_now_str()}",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=15,
            check=False,
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=25,
            check=False,
        )
    except Exception:
        pass


def _load_last_executed_cmd_id() -> str:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return str(data.get("last_cmd_id", "")).strip()
        except Exception:
            pass
    if OUTBOX_PATH.exists():
        try:
            data = json.loads(OUTBOX_PATH.read_text(encoding="utf-8"))
            return str(data.get("latest_result", {}).get("cmd_id", "")).strip()
        except Exception:
            pass
    return ""


def _save_last_executed_cmd_id(cmd_id: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"last_cmd_id": cmd_id, "updated_at": _now_str()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_bridge_daemon(poll_seconds: int = 20) -> None:
    """Vòng lặp Daemon độc lập trên máy Mac Intel: nhận lệnh từ GitHub, tự chữa lỗi khóa treo & gọi AGY CLI."""
    print(
        f"[{_now_str()}] === KHỞI ĐỘNG AGY INTEL BRIDGE DAEMON (arch={platform.machine()}, poll={poll_seconds}s) ===",
        flush=True,
    )
    last_heartbeat_push = 0.0

    while True:
        try:
            # 1. Tự động kiểm tra khóa trình duyệt bị treo quá 7 phút (420s) để tự giải cứu tại chỗ
            lock_file = PROJECT_ROOT / "workspace_temp" / ".tiktok_browser_session.lock"
            if lock_file.exists():
                try:
                    lock_age = time.time() - lock_file.stat().st_mtime
                    if lock_age > 420:
                        print(
                            f"[{_now_str()}] [AGY Bridge Auto-Healer] Phát hiện khóa trình duyệt treo {int(lock_age)}s -> Tự động giải phóng Chrome!",
                            flush=True,
                        )
                        heal_report = _unstick_chrome_and_locks()
                        outbox = {
                            "updated_at": _now_str(),
                            "latest_result": {
                                "cmd_id": f"auto_heal_{int(time.time())}",
                                "action": "auto_heal_stuck_browser_lock",
                                "executed_at": _now_str(),
                                "unstick_report": heal_report,
                                "system_health": _collect_system_health(),
                            },
                        }
                        _git_sync_and_push_outbox(outbox)
                except Exception:
                    pass

            # 2. Fetch code & inbox mới nhất từ GitHub
            subprocess.run(
                ["git", "fetch", "origin", "main"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                timeout=15,
                check=False,
            )
            local_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()
            remote_sha = subprocess.run(
                ["git", "rev-parse", "origin/main"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()

            if local_sha and remote_sha and local_sha != remote_sha:
                # Đồng bộ an toàn không mất dữ liệu local
                hist_bak = Path("/tmp/agy_bridge_hist.bak.json")
                csv_bak = Path("/tmp/agy_bridge_csv.bak.csv")
                if (PROJECT_ROOT / "story_audio_history.json").exists():
                    shutil.copy2(PROJECT_ROOT / "story_audio_history.json", hist_bak)
                if (PROJECT_ROOT / "output" / "danh_sach_video.csv").exists():
                    shutil.copy2(PROJECT_ROOT / "output" / "danh_sach_video.csv", csv_bak)
                subprocess.run(
                    ["git", "reset", "--hard", "origin/main"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                if hist_bak.exists():
                    shutil.copy2(hist_bak, PROJECT_ROOT / "story_audio_history.json")
                if csv_bak.exists():
                    shutil.copy2(csv_bak, PROJECT_ROOT / "output" / "danh_sach_video.csv")

            # 3. Kiểm tra lệnh mới trong `remote_telemetry/agy_bridge_inbox.json`
            if INBOX_PATH.exists():
                inbox_data = json.loads(INBOX_PATH.read_text(encoding="utf-8"))
                cmd_id = str(inbox_data.get("cmd_id", "")).strip()
                target_arch = str(inbox_data.get("target_arch", "x86_64")).strip()
                last_done = _load_last_executed_cmd_id()

                if cmd_id and cmd_id != last_done and target_arch in ("any", platform.machine()):
                    print(
                        f"[{_now_str()}] [AGY Bridge] Nhận lệnh mới `{cmd_id}` (action={inbox_data.get('action')}) -> Đang thực thi...",
                        flush=True,
                    )
                    res = _execute_inbox_command(inbox_data)
                    _save_last_executed_cmd_id(cmd_id)

                    history: List[Dict[str, Any]] = []
                    if OUTBOX_PATH.exists():
                        try:
                            old_out = json.loads(OUTBOX_PATH.read_text(encoding="utf-8"))
                            history = list(old_out.get("history", []))
                        except Exception:
                            history = []
                    history.insert(0, res)
                    outbox_payload = {
                        "updated_at": _now_str(),
                        "latest_result": res,
                        "history": history[:15],
                    }
                    _git_sync_and_push_outbox(outbox_payload)
                    last_heartbeat_push = time.time()
                    print(
                        f"[{_now_str()}] [AGY Bridge] Đã hoàn tất lệnh `{cmd_id}` và đẩy báo cáo lên GitHub!",
                        flush=True,
                    )

            # 4. Định kỳ mỗi 30 phút gửi 1 bản chụp Heartbeat nếu không có lệnh mới
            if platform.machine() == "x86_64" and (time.time() - last_heartbeat_push >= 1800):
                last_heartbeat_push = time.time()
                hb_res = {
                    "cmd_id": f"heartbeat_{int(time.time())}",
                    "action": "periodic_heartbeat",
                    "executed_at": _now_str(),
                    "machine_arch": platform.machine(),
                    "desktop_screenshot": _capture_desktop_screenshot(),
                    "system_health": _collect_system_health(),
                }
                _git_sync_and_push_outbox({"updated_at": _now_str(), "latest_result": hb_res})

        except KeyboardInterrupt:
            print(f"\n[{_now_str()}] Dừng AGY Intel Bridge Daemon.", flush=True)
            break
        except Exception as loop_exc:
            print(f"[{_now_str()}] [AGY Bridge] Lỗi phụ: {loop_exc}", flush=True)

        time.sleep(poll_seconds)


def send_command_from_silicon(
    action: str,
    agy_prompt: str = "",
    shell_command: str = "",
    target_arch: str = "x86_64",
) -> Dict[str, Any]:
    """Hàm tiện ích để máy Silicon tạo lệnh mới vào `agy_bridge_inbox.json` và push lên GitHub."""
    cmd_id = f"cmd_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{action}"
    payload = {
        "cmd_id": cmd_id,
        "created_at": _now_str(),
        "from_machine": platform.machine(),
        "target_arch": target_arch,
        "action": action,
        "agy_prompt": agy_prompt,
        "shell_command": shell_command,
        "timeout_seconds": 240,
    }
    INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INBOX_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Cầu nối GitHub <-> AGY CLI trên máy Mac Intel")
    parser.add_argument("--daemon", action="store_true", help="Chạy vòng lặp nhận lệnh ngầm trên máy Mac Intel")
    parser.add_argument("--poll", type=int, default=20, help="Chu kỳ kiểm tra lệnh mới (giây)")
    parser.add_argument(
        "--send-action",
        type=str,
        default="",
        help="Gửi lệnh từ máy Silicon (status_and_screenshot | unstick_chrome | check_and_heal | shell | agy_prompt)",
    )
    parser.add_argument("--prompt", type=str, default="", help="Câu lệnh giao cho AGY CLI trên máy Intel thực hiện")
    parser.add_argument("--shell", type=str, default="", help="Câu lệnh shell chạy trên máy Intel")
    args = parser.parse_args()

    if args.send_action or args.prompt:
        act = args.send_action or "agy_prompt"
        payload = send_command_from_silicon(
            action=act,
            agy_prompt=args.prompt,
            shell_command=args.shell,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.daemon:
        run_bridge_daemon(poll_seconds=args.poll)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
