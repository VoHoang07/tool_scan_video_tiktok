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
        try:
            r1 = subprocess.run(["pgrep", "-f", "--", "--stream-upload"], capture_output=True, timeout=4, check=False)
            if r1.returncode == 0 and (r1.stdout or b"").strip():
                health["stream1_running"] = True
            r2 = subprocess.run(["pgrep", "-f", "--", "--stream-filter24h"], capture_output=True, timeout=4, check=False)
            if r2.returncode == 0 and (r2.stdout or b"").strip():
                health["stream2_running"] = True
        except Exception:
            pass

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


def _ensure_both_streams_alive() -> Optional[Dict[str, Any]]:
    """Tự động kiểm tra và khởi động lại Dòng 1 (`--stream-upload`) hoặc Dòng 2 (`--stream-filter24h`) nếu bị tắt trên Mac Intel."""
    if platform.machine() != "x86_64":
        return None
    _deduplicate_stream_processes()
    health = _collect_system_health()
    if health.get("ps_error") and not health.get("stream1_running") and not health.get("stream2_running"):
        return None
    restarted: List[str] = []
    py_bin = str(PROJECT_ROOT / "venv" / "bin" / "python")
    if not Path(py_bin).exists():
        py_bin = sys.executable

    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if not health.get("stream1_running"):
        fh1 = open(log_dir / "daemon_24_7.log", "a", encoding="utf-8")
        subprocess.Popen(
            ["caffeinate", "-dimsu", py_bin, "main.py", "--stream-upload", "--max-ready", "5"],
            cwd=str(PROJECT_ROOT),
            stdout=fh1,
            stderr=fh1,
            start_new_session=True,
        )
        restarted.append("stream1_upload")

    if not health.get("stream2_running"):
        fh2 = open(log_dir / "stream2_filter24h.log", "a", encoding="utf-8")
        subprocess.Popen(
            ["caffeinate", "-dimsu", py_bin, "-m", "tiktok_pipeline.main", "--stream-filter24h"],
            cwd=str(PROJECT_ROOT),
            stdout=fh2,
            stderr=fh2,
            start_new_session=True,
        )
        restarted.append("stream2_filter24h")

    if restarted:
        return {"restarted_streams": restarted, "timestamp": _now_str()}
    return None


def _auto_self_evolve_on_log_errors(last_ai_heal_ts: float) -> Optional[Dict[str, Any]]:
    """
    Cơ chế Tự Tiến Hóa Kép (Dual Self-Evolving Engine):
    1. Quét `logs/daemon_24_7.log` và `logs/stream2_filter24h.log` tìm lỗi `Traceback` hoặc `[ERROR]` mới.
    2. Kiểm toán đầu ra thực tế (KPI / Silent Logic Auditor) trong `remote_telemetry/intel_status_report.json`:
       - Nếu phiên `filter24h` kết thúc mà không Follow được ai (`5_followed_creator: false` trên mọi video khi không bị `is_digg=1` hay `follow_api_blocked`),
         hoặc điều hướng nhầm sang `/photo/` thay vì trang Hồ sơ `/@creator`.
       - Nếu phiên `warmup_fyp` kết thúc mà `scrolled_videos < 2`.
       -> Tự động gọi `agy -p` ngay trên máy Intel để phân tích JSON telemetry + code, tự vá lỗi và đẩy lên GitHub.
    """
    if platform.machine() != "x86_64" or (time.time() - last_ai_heal_ts < 900):
        return None
    try:
        from .master_switch_controller import MasterSwitchController

        if not MasterSwitchController(project_root=PROJECT_ROOT).is_master_enabled():
            return None
    except Exception:
        pass

    import hashlib

    healed_db_path = PROJECT_ROOT / "workspace_temp" / ".agy_healed_error_hashes.json"
    healed_hashes: List[str] = []
    if healed_db_path.exists():
        try:
            healed_hashes = list(json.loads(healed_db_path.read_text(encoding="utf-8")))
        except Exception:
            healed_hashes = []

    # TẦNG 1: Kiểm toán KPI / Silent Logic Bug từ remote_telemetry/intel_status_report.json
    telem_path = PROJECT_ROOT / "remote_telemetry" / "intel_status_report.json"
    if telem_path.exists():
        try:
            telem_data = json.loads(telem_path.read_text(encoding="utf-8"))
            latest_ev = telem_data.get("latest_event") or {}
            ev_type = str(latest_ev.get("event_type") or "")
            ev_ts = str(latest_ev.get("timestamp") or "")
            ev_ch = str(latest_ev.get("channel") or "")
            det = latest_ev.get("details") or {}
            anomaly_desc = ""

            if ev_type == "filter24h" and ev_ch in ("video_AI_hay", "baby_AI", "audio_dem_khuyen", "tu_chan_gioi"):
                recs = det.get("records") or []
                any_followed = any(
                    bool((r.get("steps_completed") or {}).get("5_followed_creator"))
                    for r in recs
                )
                any_cb = any(bool(r.get("circuit_breaker_tripped")) for r in recs)
                if recs and not any_followed and not any_cb:
                    anomaly_desc = (
                        f"Silent KPI Bug: Phiên filter24h của kênh [{ev_ch}] lúc {ev_ts} đã tương tác {len(recs)} video "
                        f"nhưng 5_followed_creator=False trên tất cả video (không bị circuit_breaker)."
                    )
            elif ev_type == "warmup_fyp":
                if int(det.get("scrolled_videos") or 0) < 2:
                    anomaly_desc = (
                        f"Silent KPI Bug: Phiên warmup_fyp của kênh [{ev_ch}] lúc {ev_ts} chỉ lướt được "
                        f"{det.get('scrolled_videos')} video (< 2 video)."
                    )

            if anomaly_desc:
                anom_hash = hashlib.md5(f"{ev_type}:{ev_ch}:{ev_ts}".encode("utf-8")).hexdigest()[:12]
                if anom_hash not in healed_hashes:
                    healed_hashes.append(anom_hash)
                    healed_db_path.parent.mkdir(parents=True, exist_ok=True)
                    healed_db_path.write_text(json.dumps(healed_hashes[-100:], indent=2), encoding="utf-8")
                    ev_json_str = json.dumps(latest_ev, ensure_ascii=False, indent=2)[-2400:]
                    prompt = (
                        f"Phát hiện lỗi nghiệp vụ ngầm (Silent KPI Anomaly) trong `remote_telemetry/intel_status_report.json`:\n"
                        f"{anomaly_desc}\n```json\n{ev_json_str}\n```\n"
                        "Hãy đọc `tiktok_pipeline/growth_24h_filter.py` và `tiktok_pipeline/auto_poster.py`, "
                        "tìm nguyên nhân vì sao thao tác Follow/Lướt không đạt KPI, tự động sửa code, "
                        "kiểm tra py_compile, commit & push lên origin/main."
                    )
                    agy_bin = _find_agy_bin()
                    proc = subprocess.run(
                        [
                            agy_bin,
                            "-p",
                            prompt,
                            "--dangerously-skip-permissions",
                            "--print-timeout",
                            "240s",
                        ],
                        cwd=str(PROJECT_ROOT),
                        capture_output=True,
                        text=True,
                        timeout=260,
                        check=False,
                    )
                    return {
                        "error_hash": anom_hash,
                        "log_file": "remote_telemetry/intel_status_report.json",
                        "error_line": anomaly_desc,
                        "agy_exit_code": proc.returncode,
                        "agy_fix_summary": (proc.stdout or "")[-4000:],
                        "timestamp": _now_str(),
                    }
        except Exception:
            pass

    # TẦNG 2: Quét lỗi Traceback / [ERROR] trong logs/daemon_24_7.log và logs/stream2_filter24h.log
    for log_name in ("daemon_24_7.log", "stream2_filter24h.log"):
        log_path = PROJECT_ROOT / "logs" / log_name
        if not log_path.exists():
            continue
        try:
            tail_txt = subprocess.run(
                ["tail", "-n", "120", str(log_path)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout or ""
            if "Traceback (most recent call last):" not in tail_txt and "[ERROR]" not in tail_txt:
                continue
            err_lines = [
                ln.strip()
                for ln in tail_txt.splitlines()
                if ("Error:" in ln or "Exception:" in ln or "[ERROR]" in ln)
            ]
            if not err_lines:
                continue
            latest_err = err_lines[-1]
            # Chuẩn hóa chữ ký lỗi (bỏ timestamp đầu dòng)
            sig_clean = latest_err[-140:]
            err_hash = hashlib.md5(sig_clean.encode("utf-8")).hexdigest()[:12]
            if err_hash in healed_hashes:
                continue

            healed_hashes.append(err_hash)
            healed_db_path.parent.mkdir(parents=True, exist_ok=True)
            healed_db_path.write_text(json.dumps(healed_hashes[-100:], indent=2), encoding="utf-8")

            prompt = (
                f"Phát hiện lỗi mới trong `{log_name}` trên máy Mac Intel:\n"
                f"```\n{tail_txt[-1800:]}\n```\n"
                "Hãy kiểm tra nguyên nhân gốc rễ, tự động sửa lỗi (nếu là lỗi code hoặc trạng thái kẹt), "
                "kiểm tra cú pháp bằng py_compile, commit & push lên origin/main nếu có sửa file .py, "
                "và tóm tắt ngắn gọn cách đã xử lý."
            )
            agy_bin = _find_agy_bin()
            proc = subprocess.run(
                [
                    agy_bin,
                    "-p",
                    prompt,
                    "--dangerously-skip-permissions",
                    "--print-timeout",
                    "240s",
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=260,
                check=False,
            )
            return {
                "error_hash": err_hash,
                "log_file": log_name,
                "error_line": latest_err,
                "agy_exit_code": proc.returncode,
                "agy_fix_summary": (proc.stdout or "")[-4000:],
                "timestamp": _now_str(),
            }
        except Exception:
            continue
    return None


def _deduplicate_stream_processes() -> List[str]:
    """Đảm bảo trên máy Mac Intel chỉ có tối đa 1 tiến trình Dòng 1 và 1 tiến trình Dòng 2 (tránh chạy trùng lặp)."""
    killed: List[str] = []
    if platform.machine() != "x86_64":
        return killed
    try:
        ps_res = subprocess.run(
            ["ps", "-Ao", "pid,command"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        s1_pids: List[int] = []
        s2_pids: List[int] = []
        for line in (ps_res.stdout or "").splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) < 2 or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            cmd = parts[1]
            if "grep" in cmd or "caffeinate" in cmd:
                continue
            if "--stream-upload" in cmd and "python" in cmd.lower():
                s1_pids.append(pid)
            elif "--stream-filter24h" in cmd and "python" in cmd.lower():
                s2_pids.append(pid)

        # Giữ lại tiến trình mới nhất (PID cuối danh sách), tắt các bản chạy trùng cũ
        for dup_pid in s1_pids[:-1]:
            try:
                os.kill(dup_pid, 9)
                killed.append(f"stream1_dup_pid={dup_pid}")
            except Exception:
                pass
        for dup_pid in s2_pids[:-1]:
            try:
                os.kill(dup_pid, 9)
                killed.append(f"stream2_dup_pid={dup_pid}")
            except Exception:
                pass
    except Exception:
        pass
    return killed


def _broadcast_to_open_terminals(msg: str) -> None:
    """In thông báo trực tiếp lên các cửa sổ Terminal đang mở trên Mac Intel để người dùng nhìn thấy ngay."""
    try:
        for tty_path in sorted(Path("/dev").glob("ttys00*")):
            try:
                with open(tty_path, "w", encoding="utf-8", errors="ignore") as tty_fh:
                    tty_fh.write(f"\n{msg}\n")
            except Exception:
                pass
    except Exception:
        pass


def run_bridge_daemon(poll_seconds: int = 20) -> None:
    """Vòng lặp Daemon độc lập trên máy Mac Intel: nhận lệnh từ GitHub, tự chữa lỗi khóa treo & gọi AGY CLI."""
    import fcntl

    daemon_lock_path = PROJECT_ROOT / "workspace_temp" / ".agy_intel_bridge_daemon.lock"
    daemon_lock_path.parent.mkdir(parents=True, exist_ok=True)
    daemon_lock_fh = open(daemon_lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(daemon_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        daemon_lock_fh.seek(0)
        daemon_lock_fh.truncate()
        daemon_lock_fh.write(f"pid={os.getpid()} started={_now_str()}\n")
        daemon_lock_fh.flush()
    except BlockingIOError:
        # Đã có 1 tiến trình AGY Bridge Daemon đang giữ khóa -> thoát ngay để không chạy trùng
        sys.exit(0)

    print(
        f"[{_now_str()}] === KHỞI ĐỘNG AGY INTEL BRIDGE DAEMON SINGLETON (pid={os.getpid()}, arch={platform.machine()}, poll={poll_seconds}s) ===",
        flush=True,
    )
    last_heartbeat_push = time.time()
    last_ai_heal_ts = 0.0

    while True:
        try:
            _deduplicate_stream_processes()
            # 1a. Tự động đảm bảo cả Dòng 1 và Dòng 2 luôn sống trên Mac Intel
            restart_info = _ensure_both_streams_alive()
            if restart_info:
                banner = (
                    f"[{_now_str()}] [AGY Bridge Watchdog] ✅ Đã tự động bật lại dòng bị tắt: "
                    f"{restart_info['restarted_streams']} (xem log tại logs/stream2_filter24h.log)"
                )
                print(banner, flush=True)
                _broadcast_to_open_terminals(banner)
                _git_sync_and_push_outbox(
                    {
                        "updated_at": _now_str(),
                        "latest_result": {
                            "cmd_id": f"auto_restart_{int(time.time())}",
                            "action": "auto_restart_dead_streams",
                            "executed_at": _now_str(),
                            "restart_info": restart_info,
                            "system_health": _collect_system_health(),
                        },
                    }
                )

            # 1b. Tự động phát hiện lỗi mới trong log và gọi AGY CLI tự sửa (Self-Evolving)
            ai_fix_report = _auto_self_evolve_on_log_errors(last_ai_heal_ts)
            if ai_fix_report:
                last_ai_heal_ts = time.time()
                banner = (
                    f"[{_now_str()}] [AGY Self-Evolving] ✅ Đã tự động dùng AGY CLI phân tích & vá lỗi mới "
                    f"({ai_fix_report['error_hash']}: {ai_fix_report.get('error_line', '')[:80]})!"
                )
                print(banner, flush=True)
                _broadcast_to_open_terminals(banner)
                _git_sync_and_push_outbox(
                    {
                        "updated_at": _now_str(),
                        "latest_result": {
                            "cmd_id": f"auto_evolve_{ai_fix_report['error_hash']}",
                            "action": "auto_self_evolve_fix",
                            "executed_at": _now_str(),
                            "ai_fix_report": ai_fix_report,
                            "system_health": _collect_system_health(),
                        },
                    }
                )

            # 1c. Tự động kiểm tra khóa trình duyệt bị giữ thực sự (`fcntl.flock`) quá 7 phút (420s) để tự giải cứu tại chỗ
            lock_file = PROJECT_ROOT / "workspace_temp" / ".tiktok_browser_session.lock"
            if lock_file.exists():
                try:
                    probe_fh = open(lock_file, "a+", encoding="utf-8")
                    is_actively_locked = False
                    try:
                        fcntl.flock(probe_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(probe_fh.fileno(), fcntl.LOCK_UN)
                    except BlockingIOError:
                        is_actively_locked = True
                    finally:
                        probe_fh.close()

                    if is_actively_locked:
                        content_txt = lock_file.read_text(encoding="utf-8", errors="ignore").strip()
                        lock_age = time.time() - lock_file.stat().st_mtime
                        if "ts=" in content_txt:
                            try:
                                ts_val = int(content_txt.split("ts=")[1].split()[0])
                                lock_age = time.time() - ts_val
                            except Exception:
                                pass
                        if lock_age > 420:
                            print(
                                f"[{_now_str()}] [AGY Bridge Auto-Healer] Phát hiện khóa trình duyệt bị giữ treo {int(lock_age)}s -> Tự động giải phóng Chrome!",
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
