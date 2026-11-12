import csv
import hashlib
import json
import os
import platform
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import importlib as _importlib
from . import crawler as _crawler_mod
from . import macos_desktop_controller as _mdc_mod
from . import poster_browser as _pb_mod
from . import poster_studio_ui as _ps_mod
from . import poster_cleanup as _pc_mod
from . import poster_media_prep as _pm_mod
from . import poster_stream2_warmup as _pw_mod

try:
    _importlib.reload(_crawler_mod)
    _importlib.reload(_mdc_mod)
    _importlib.reload(_pb_mod)
    _importlib.reload(_ps_mod)
    _importlib.reload(_pc_mod)
    _importlib.reload(_pm_mod)
    _importlib.reload(_pw_mod)
except Exception:
    pass

from .crawler import CHANNEL_PROFILES, StoryAudioCrawler, resolve_channel_profile
from .growth_24h_filter import TikTok24hFilterEngine
from .macos_desktop_controller import MacOSDesktopController
from .poster_browser import PosterBrowserMixin, STEALTH_INIT_SCRIPT
from .poster_studio_ui import PosterStudioUIMixin
from .poster_cleanup import PosterCleanupMixin
from .poster_media_prep import PosterMediaPrepMixin
from .poster_stream2_warmup import PosterStream2WarmupMixin
from .utils import logger

DEFAULT_SCHEDULE_CONFIG: Dict[str, Any] = {
    "timezone": "Asia/Ho_Chi_Minh",
    "default_post_times": ["08:00", "12:30", "23:30"],
    "channel_post_times": {
        "audio_dem_khuyen": ["08:48", "13:25", "23:00"],
        "video_AI_hay": ["07:25", "12:05", "23:06"],
        "meo_vat_gia_dinh": ["07:10", "11:35", "23:12"],
        "suc_khoe_duong_sinh": ["06:50", "11:55", "23:18"],
        "an_vat_moi_ngay": ["07:38", "12:22", "23:24"],
        "baby_AI": ["08:05", "12:42", "23:30"],
        "nuoi_con_eq": ["08:15", "12:52", "23:36"],
        "phu_nu_khi_chat": ["08:28", "13:05", "23:42"],
        "phu_kien_tien_ich": ["09:05", "13:45", "23:48"],
        "tu_chan_gioi": ["09:18", "14:10", "23:54"],
    },
    "filter_24h_enabled": True,
    "filter_24h_runs_per_day": 2,
    "filter_24h_videos_per_session": 3,
    "channel_filter_24h_times": {
        "video_AI_hay": ["09:40", "17:20"],
        "baby_AI": ["10:15", "18:05"],
        "audio_dem_khuyen": ["10:50", "18:40"],
        "tu_chan_gioi": ["11:25", "19:10"],
        "meo_vat_gia_dinh": ["09:15", "16:45"],
        "suc_khoe_duong_sinh": ["09:55", "17:35"],
        "phu_nu_khi_chat": ["10:35", "18:20"],
        "an_vat_moi_ngay": ["11:05", "18:55"],
        "nuoi_con_eq": ["10:05", "17:50"],
        "phu_kien_tien_ich": ["11:40", "19:25"],
    },
    "random_jitter_minutes": 26,
    "warmup_before_upload": True,
    "use_macos_native_input": True,
    "headless": False,
}


class TikTokAutoPoster(
    PosterBrowserMixin,
    PosterStudioUIMixin,
    PosterCleanupMixin,
    PosterMediaPrepMixin,
    PosterStream2WarmupMixin,
):
    """
    Hệ thống tự động đăng video lên 4 kênh TikTok theo khung giờ cài đặt sẵn (24/7 Anti-Detect Scheduler):
    - Lớp bảo vệ 1 (Anti-Detect Browser): Khởi chạy Google Chrome thật với `ignore_default_args=['--enable-automation']`
      và tiêm `STEALTH_INIT_SCRIPT` xóa toàn bộ dấu vết `navigator.webdriver` / CDP.
    - Lớp bảo vệ 2 (Human Warm-up & Mouse Entropy): Tự động lướt xem 2-3 video trên trang chủ TikTok (15-25s),
      di chuột đường cong Bezier trước khi vào trang Upload để tạo điểm tin cậy hành vi người thật (`mssdk`).
    - Lớp bảo vệ 3 (Human Keystroke & Native Hashtag Pill): Xóa sạch tên file mặc định trong ô Mô tả,
      gõ từng từ kịch bản ngắn với độ trễ ngẫu nhiên (35-95ms/ký tự) và chọn từng `#hashtag` từ menu gợi ý Hot Search.
    - Lớp bảo vệ 4 (Full Upload Settings & 2-Step Public Verification):
      + Bước 1 (Tại trang Upload `tiktokstudio/upload`): Tự động set `Thời điểm đăng = Bây giờ`,
        `Người có thể nhìn thấy bài đăng này = Mọi người (Công khai)`, bật `Nội dung tải lên chất lượng cao`,
        tích chọn `Bình luận`, tắt `Khai báo nội dung bài đăng` & `Nội dung do AI tạo`, bật `Kiểm tra bản quyền nhạc` & `Kiểm tra nội dung nhanh`.
      + Bước 2 (Tại trang Quản lý `tiktokstudio/content` sau khi bấm Đăng): Tự động kiểm tra cột `Quyền riêng tư`
        của bài vừa đăng và các bài trước đó; nếu bài nào đang ở trạng thái `Chỉ mình tôi` / `Chỉ mình...` / `Bạn bè`,
        tự động bấm mở menu và chuyển sang `Mọi người` (Công khai).
    - Lớp bảo vệ 5 (Random Schedule Jitter): Cộng/trừ ngẫu nhiên ±1..9 phút mỗi ngày để không đăng trùng giờ/phút/giây.
    """


    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.profiles_root = self.project_root / "browser_profiles"
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.schedule_config_path = self.project_root / "schedule_config.json"
        self.post_log_path = self.project_root / "posted_schedule_history.json"
        self.crawler = StoryAudioCrawler(project_root=self.project_root)
        try:
            self.crawler.auto_sync_posted_videos()
        except Exception:
            pass
        self.desktop = MacOSDesktopController(project_root=self.project_root)
        self.filter_24h = TikTok24hFilterEngine(
            project_root=self.project_root,
            desktop=self.desktop,
            human_move_fn=self._human_move_mouse,
            os_click_fn=self._os_click_viewport,
        )
        self.ensure_schedule_config()
        self._heal_git_repo_state()
        try:
            import os as _os
            import subprocess as _sp

            if not getattr(TikTokAutoPoster, "_caffeinate_started", False):
                _sp.Popen(
                    ["caffeinate", "-dimsu", "-w", str(_os.getpid())],
                    stdout=_sp.DEVNULL,
                    stderr=_sp.DEVNULL,
                )
                setattr(TikTokAutoPoster, "_caffeinate_started", True)
        except Exception:
            pass
        try:
            self._ensure_intel_bridge_daemon_running()
        except Exception:
            pass
        try:
            self._auto_revive_stream1_upload_if_dead()
        except Exception:
            pass
        try:
            self._auto_revive_stream2_filter24h_if_dead()
        except Exception:
            pass


    def ensure_schedule_config(self) -> Dict[str, Any]:
        """Tạo hoặc đọc file cấu hình khung giờ đăng `schedule_config.json` (tự động bổ sung đủ 10 kênh nếu thiếu)."""
        if not self.schedule_config_path.exists():
            self.schedule_config_path.write_text(
                json.dumps(DEFAULT_SCHEDULE_CONFIG, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return dict(DEFAULT_SCHEDULE_CONFIG)
        try:
            cfg = json.loads(self.schedule_config_path.read_text(encoding="utf-8"))
            changed = False
            for k, v in DEFAULT_SCHEDULE_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
                    changed = True
                elif k in ("channel_post_times", "channel_filter_24h_times") and isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    for ch_k, ch_v in v.items():
                        if ch_k not in cfg[k]:
                            cfg[k][ch_k] = ch_v
                            changed = True
            if changed:
                try:
                    self.schedule_config_path.write_text(
                        json.dumps(cfg, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return cfg
        except Exception:
            return dict(DEFAULT_SCHEDULE_CONFIG)


    def update_schedule_times(self, times_csv: str) -> Dict[str, Any]:
        """
        Cập nhật nhanh khung giờ đăng mỗi ngày từ chuỗi CLI (ví dụ: `"08:00,12:30,20:00"`).
        Tự động giãn cách 10 phút giữa 4 kênh để không mở 4 trình duyệt cùng một lúc trên Mac Intel.
        """
        raw_times = [t.strip() for t in times_csv.split(",") if t.strip()]
        valid_times: List[str] = []
        for t in raw_times:
            parts = t.split(":")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                hh, mm = int(parts[0]), int(parts[1])
                if 0 <= hh <= 23 and 0 <= mm <= 59:
                    valid_times.append(f"{hh:02d}:{mm:02d}")
        if not valid_times:
            raise ValueError(
                f"Khung giờ không hợp lệ: '{times_csv}'. Vui lòng nhập định dạng HH:MM, ví dụ: '08:00,12:30,20:00'"
            )

        cfg = self.ensure_schedule_config()
        cfg["default_post_times"] = valid_times
        ch_folders = [p["folder_name"] for p in CHANNEL_PROFILES.values()]
        ch_times: Dict[str, List[str]] = {}
        for idx, ch_folder in enumerate(ch_folders):
            shifted_list: List[str] = []
            offset_min = idx * 10
            for base_t in valid_times:
                hh, mm = map(int, base_t.split(":"))
                total_m = (hh * 60 + mm + offset_min) % (24 * 60)
                shifted_list.append(f"{total_m // 60:02d}:{total_m % 60:02d}")
            ch_times[ch_folder] = shifted_list

        cfg["channel_post_times"] = ch_times
        self.schedule_config_path.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"Đã cập nhật khung giờ đăng bài vào {self.schedule_config_path}: {ch_times}")
        return cfg


    def _heal_git_repo_state(self) -> None:
        """Dọn dẹp trạng thái kẹt rebase/index.lock nếu có xung đột đẩy telemetry đồng thời."""
        import shutil as _shutil
        import subprocess as _sp

        try:
            git_dir = self.project_root / ".git"
            if not git_dir.exists():
                return
            has_stuck_rebase = (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()
            for lock_f in ("index.lock", "HEAD.lock", "refs/heads/main.lock"):
                lf = git_dir / lock_f
                if lf.exists():
                    try:
                        lf.unlink(missing_ok=True)
                    except Exception:
                        pass
            if has_stuck_rebase:
                _shutil.rmtree(git_dir / "rebase-merge", ignore_errors=True)
                _shutil.rmtree(git_dir / "rebase-apply", ignore_errors=True)
            st_chk = _sp.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            if st_chk.returncode != 0 and "index" in (st_chk.stderr or "").lower():
                (git_dir / "index").unlink(missing_ok=True)
                _sp.run(["git", "reset"], cwd=str(self.project_root), capture_output=True, timeout=15, check=False)
                _sp.run(
                    ["git", "checkout", "-f", "-B", "main", "origin/main"],
                    cwd=str(self.project_root),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
        except Exception:
            pass


    def _save_and_push_telemetry(
        self,
        page: Any,
        event_type: str,
        ch_folder: str,
        details: Dict[str, Any],
    ) -> None:
        """
        Chụp ảnh màn hình trình duyệt hiện tại và ghi báo cáo trạng thái vào `remote_telemetry/`,
        sau đó đẩy `git commit` & `git push` chạy ngầm (Async Background Thread) để đóng trình duyệt
        ngay tức khắc mà không bị treo chờ mạng GitHub.
        """
        import fcntl as _fcntl
        import platform as _plat
        import shutil as _shutil
        import subprocess
        import threading

        try:
            telem_dir = self.project_root / "remote_telemetry"
            telem_dir.mkdir(parents=True, exist_ok=True)
            img_name = f"latest_{event_type}_{ch_folder}.jpg"
            img_path = telem_dir / img_name
            img_bytes: Optional[bytes] = None
            if page:
                try:
                    img_bytes = page.screenshot(path=str(img_path), type="jpeg", quality=60)
                except Exception:
                    pass

            report_path = telem_dir / "intel_status_report.json"
            existing: Dict[str, Any] = {}
            if report_path.exists():
                try:
                    existing = json.loads(report_path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = {
                "timestamp": now_str,
                "machine_arch": _plat.machine(),
                "event_type": event_type,
                "channel": ch_folder,
                "screenshot": f"remote_telemetry/{img_name}",
                "details": details,
            }
            existing["latest_event"] = entry
            history_list = existing.get("recent_events") or []
            history_list.insert(0, entry)
            existing["recent_events"] = history_list[:15]
            report_path.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            proj_str = str(self.project_root)
            git_lock_path = self.project_root / "workspace_temp" / ".tiktok_git_sync.lock"
            git_lock_path.parent.mkdir(parents=True, exist_ok=True)

            def _async_git_push() -> None:
                lock_f = None
                try:
                    lock_f = open(git_lock_path, "w", encoding="utf-8")
                    _fcntl.flock(lock_f.fileno(), _fcntl.LOCK_EX)

                    git_dir = self.project_root / ".git"
                    for lf_name in ("index.lock", "HEAD.lock", "refs/heads/main.lock"):
                        (git_dir / lf_name).unlink(missing_ok=True)
                    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
                        _shutil.rmtree(git_dir / "rebase-merge", ignore_errors=True)
                        _shutil.rmtree(git_dir / "rebase-apply", ignore_errors=True)

                    hist_p = self.project_root / "story_audio_history.json"
                    local_hist: Dict[str, Any] = {}
                    if hist_p.exists():
                        try:
                            local_hist = json.loads(hist_p.read_text(encoding="utf-8"))
                        except Exception:
                            local_hist = {}

                    subprocess.run(
                        ["git", "fetch", "origin", "main"],
                        cwd=proj_str,
                        capture_output=True,
                        timeout=20,
                        check=False,
                    )
                    subprocess.run(
                        ["git", "checkout", "-f", "-B", "main", "origin/main"],
                        cwd=proj_str,
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )

                    # Hợp nhất story_audio_history.json sau khi checkout để KHÔNG BAO GIỜ mất ID video đã cào/đăng trên máy Intel!
                    if local_hist and isinstance(local_hist, dict):
                        try:
                            remote_hist = (
                                json.loads(hist_p.read_text(encoding="utf-8"))
                                if hist_p.exists()
                                else {}
                            )
                            if not isinstance(remote_hist, dict):
                                remote_hist = {}
                            v_union = list(
                                dict.fromkeys(
                                    [*(remote_hist.get("video_ids") or []), *(local_hist.get("video_ids") or [])]
                                )
                            )
                            s_union = list(
                                dict.fromkeys(
                                    [*(remote_hist.get("sound_ids") or []), *(local_hist.get("sound_ids") or [])]
                                )
                            )
                            remote_hist["video_ids"] = v_union
                            remote_hist["sound_ids"] = s_union
                            if local_hist.get("items"):
                                remote_hist["items"] = local_hist["items"]
                            hist_p.write_text(
                                json.dumps(remote_hist, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                        except Exception:
                            pass

                    # Ghi lại ảnh & report mới nhất sau khi đã đồng bộ HEAD với origin/main (không bao giờ xung đột rebase!)
                    if img_bytes:
                        img_path.write_bytes(img_bytes)
                    cur_rep: Dict[str, Any] = {}
                    if report_path.exists():
                        try:
                            cur_rep = json.loads(report_path.read_text(encoding="utf-8"))
                        except Exception:
                            cur_rep = {}
                    cur_rep["latest_event"] = entry
                    cur_hist = cur_rep.get("recent_events") or []
                    cur_hist.insert(0, entry)
                    cur_rep["recent_events"] = cur_hist[:15]
                    report_path.write_text(
                        json.dumps(cur_rep, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                    add_targets = ["remote_telemetry"]
                    if hist_p.exists():
                        add_targets.append("story_audio_history.json")
                    subprocess.run(
                        ["git", "add", *add_targets],
                        cwd=proj_str,
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                    subprocess.run(
                        [
                            "git",
                            "-c",
                            "user.name=IntelMacBot",
                            "-c",
                            "user.email=bot@tiktokpipeline.local",
                            "commit",
                            "-m",
                            f"Auto-Telemetry [{event_type}] {ch_folder} at {now_str}",
                        ],
                        cwd=proj_str,
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                    subprocess.run(
                        ["git", "push", "origin", "HEAD:main"],
                        cwd=proj_str,
                        capture_output=True,
                        timeout=25,
                        check=False,
                    )
                except Exception:
                    pass
                finally:
                    if lock_f is not None:
                        try:
                            _fcntl.flock(lock_f.fileno(), _fcntl.LOCK_UN)
                            lock_f.close()
                        except Exception:
                            pass

            threading.Thread(target=_async_git_push, daemon=False).start()
            logger.info(
                f"[{ch_folder}] [Remote Telemetry] Đã lưu ảnh ({img_name}) & đẩy đồng bộ GitHub chạy ngầm!"
            )
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Remote Telemetry] Bỏ qua đồng bộ telemetry phụ: {exc}")


    def _load_post_history(self) -> Dict[str, str]:
        if not self.post_log_path.exists():
            return {}
        try:
            return json.loads(self.post_log_path.read_text(encoding="utf-8"))
        except Exception:
            return {}


    def _save_post_history(self, data: Dict[str, str]) -> None:
        self.post_log_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


    @staticmethod
    def _compute_daily_jitter_minutes(today_str: str, ch_folder: str, slot: str, max_jitter: int = 9) -> int:
        """
        Tạo độ lệch phút ngẫu nhiên mỗi ngày (từ -3 đến +max_jitter phút) cho từng khung giờ
        để tài khoản không bao giờ đăng bài trùng khớp từng phút/giây giữa các ngày liên tiếp.
        Riêng ca khuya (từ 23:00): kênh `audio_dem_khuyen` cố định đúng 23:00 (jitter=0) để mở màn đầu tiên,
        còn 9 kênh còn lại chỉ lệch dương nhỏ (0..+3 phút) để không bao giờ nhảy lên trước 23:00.
        """
        if str(slot).strip() >= "22:00":
            if ch_folder == "audio_dem_khuyen":
                return 0
            seed_str = f"{today_str}:{ch_folder}:{slot}"
            h_val = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:8], 16)
            return h_val % 4
        if max_jitter <= 0:
            return 0
        seed_str = f"{today_str}:{ch_folder}:{slot}"
        h_val = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:8], 16)
        return (h_val % (max_jitter + 4)) - 3


    def check_and_run_stream1_upload_schedule(self) -> List[Dict[str, Any]]:
        """
        [DÒNG HOẠT ĐỘNG 1 — TỰ ĐỘNG ĐĂNG VIDEO THEO KHUNG GIỜ CỐ ĐỊNH CHO 4 KÊNH]:
        Giữ nguyên 100% khung giờ đăng bài mỗi ngày của 4 kênh trong `schedule_config.json`
        (`channel_post_times`) và lệnh kích hoạt đăng ngay từ xa (`remote_trigger_post_now`).
        """
        from .master_switch_controller import MasterSwitchController

        try:
            self._ensure_intel_bridge_daemon_running()
        except Exception:
            pass
        try:
            self._auto_revive_stream2_filter24h_if_dead()
        except Exception:
            pass

        ms_ctrl = MasterSwitchController(project_root=self.project_root)
        if not ms_ctrl.is_upload_enabled("tiktok"):
            return []

        cfg = self.ensure_schedule_config()
        ch_times_map: Dict[str, List[str]] = cfg.get("channel_post_times") or {}
        default_times: List[str] = cfg.get("default_post_times") or ["08:00", "12:30", "20:00"]
        max_jitter = int(cfg.get("random_jitter_minutes", 9))
        headless = bool(cfg.get("headless", False))

        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        now_minutes = now.hour * 60 + now.minute
        history = self._load_post_history()
        posted_results: List[Dict[str, Any]] = []

        # 0a. Kiểm tra lệnh kích hoạt ĐĂNG NGAY TỪ XA (Remote Trigger Post-Now)
        remote_post = cfg.get("remote_trigger_post_now")
        if isinstance(remote_post, dict) and remote_post.get("trigger_id"):
            import platform as _plat

            target_arch = str(remote_post.get("target_arch") or "").strip()
            if not target_arch or _plat.machine() == target_arch:
                p_trig_id = str(remote_post["trigger_id"]).strip()
                p_trig_key = f"remote_post_{p_trig_id}"
                if p_trig_id and p_trig_key not in history:
                    p_trig_ch = str(remote_post.get("channel") or "tu_chan_gioi").strip()
                    if p_trig_ch.lower() == "all":
                        target_channels = [p["folder_name"] for p in CHANNEL_PROFILES.values()]
                    else:
                        target_channels = [c.strip() for c in p_trig_ch.split(",") if c.strip()]
                    logger.info(
                        f"[Dòng 1 - Đăng Video] Nhận lệnh ĐĂNG NGAY LẬP TỨC từ máy Silicon ({p_trig_id}) -> "
                        f"Kênh {target_channels}!"
                    )
                    ensure_pub_raw = str(remote_post.get("ensure_public_channels") or "").strip()
                    ensure_pub_list = [c.strip() for c in ensure_pub_raw.split(",") if c.strip()]
                    for pub_ch in ensure_pub_list:
                        pub_trig_key = f"remote_pub_{p_trig_id}_{pub_ch}"
                        lock_fh = self._acquire_browser_process_lock()
                        try:
                            history = self._load_post_history()
                            if pub_trig_key in history:
                                continue
                            history[pub_trig_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self._save_post_history(history)
                            logger.info(
                                f"[Dòng 1 - Đăng Video] Đang mở trang Quản lý Bài đăng kênh [{pub_ch}] để chuyển toàn bộ bài Chỉ mình tôi -> Mọi người (Công khai)..."
                            )
                            self.ensure_channel_posts_public(pub_ch, headless=headless)
                        except Exception as pub_exc:
                            logger.error(f"Lỗi khi chuyển Công khai kênh [{pub_ch}]: {pub_exc}")
                        finally:
                            self._release_browser_process_lock(lock_fh)

                    all_ch_succeeded = True
                    for single_ch in target_channels:
                        ch_trig_key = f"remote_post_{p_trig_id}_{single_ch}"
                        if ch_trig_key in self._load_post_history():
                            continue
                        if str(remote_post.get("action") or "").strip().lower() != "delete_only":
                            try:
                                self._get_next_ready_video_for_channel(single_ch)
                            except Exception:
                                pass
                        lock_fh = self._acquire_browser_process_lock()
                        try:
                            history = self._load_post_history()
                            if ch_trig_key in history:
                                continue
                            history[ch_trig_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self._save_post_history(history)
                            try:
                                self.crawler.auto_sync_posted_videos()
                            except Exception:
                                pass
                            if str(remote_post.get("action") or "").strip().lower() == "delete_only":
                                del_res = self.delete_channel_posts_by_keywords(
                                    single_ch,
                                    match_keywords=remote_post.get("delete_match_keywords") or [],
                                    headless=headless,
                                )
                                posted_results.append({"channel": single_ch, "deleted_posts": del_res})
                            else:
                                res = self.upload_one_channel_video(
                                    single_ch,
                                    headless=headless,
                                    force=bool(remote_post.get("force", True)),
                                    delete_match_keywords=remote_post.get("delete_match_keywords"),
                                    delete_top_before_upload=bool(remote_post.get("delete_top_before_upload", False)),
                                    skip_warmup=bool(remote_post.get("skip_warmup", False)),
                                )
                                if res:
                                    posted_results.append(res)
                                else:
                                    all_ch_succeeded = False
                                    history = self._load_post_history()
                                    history.pop(ch_trig_key, None)
                                    self._save_post_history(history)
                        except Exception as exc:
                            all_ch_succeeded = False
                            try:
                                history = self._load_post_history()
                                history.pop(ch_trig_key, None)
                                self._save_post_history(history)
                            except Exception:
                                pass
                            logger.error(
                                f"[Dòng 1 - Đăng Video] Lỗi khi đăng ngay ({p_trig_id}) kênh [{single_ch}]: {exc}"
                            )
                        finally:
                            self._release_browser_process_lock(lock_fh)
                    if all_ch_succeeded:
                        history = self._load_post_history()
                        history[p_trig_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self._save_post_history(history)
                    if bool(remote_post.get("auto_pause_master_after", False)):
                        try:
                            cfg_after = self.ensure_schedule_config()
                            sw_after = cfg_after.setdefault("system_switches", {})
                            sw_after["master_switch"] = False
                            if isinstance(sw_after.get("switch_1_factory"), dict):
                                sw_after["switch_1_factory"]["enabled"] = True
                            if isinstance(sw_after.get("switch_3_interact"), dict):
                                sw_after["switch_3_interact"]["tiktok"] = True
                            self.schedule_config_path.write_text(
                                json.dumps(cfg_after, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                            logger.info(
                                f"[Auto-Pause After Batch Upload] Đã hoàn tất đăng video cho {len(target_channels)} kênh ({target_channels}) -> "
                                "Tự động đóng lại Khóa Tổng (`master_switch: false`) theo yêu cầu!"
                            )
                            try:
                                import subprocess as _sp

                                _sp.run(
                                    ["git", "add", "schedule_config.json"],
                                    cwd=str(self.project_root),
                                    timeout=15,
                                    check=False,
                                )
                                _sp.run(
                                    ["git", "commit", "-m", "chore(auto-pause): lock master_switch=false after completing 7-channel upload batch"],
                                    cwd=str(self.project_root),
                                    timeout=15,
                                    check=False,
                                )
                                _sp.run(
                                    ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                                    cwd=str(self.project_root),
                                    timeout=30,
                                    check=False,
                                )
                                _sp.run(
                                    ["git", "push", "origin", "main"],
                                    cwd=str(self.project_root),
                                    timeout=30,
                                    check=False,
                                )
                            except Exception:
                                pass
                        except Exception as pause_exc:
                            logger.error(f"Lỗi khi tự động khóa lại master_switch: {pause_exc}")
                        return posted_results

        active_ch_set = set(cfg.get("active_channels") or ["video_AI_hay", "baby_AI", "audio_dem_khuyen", "tu_chan_gioi"])
        # Ca khuya (từ 22h trở đi): luôn xếp `audio_dem_khuyen` đứng ĐẦU TIÊN để mở màn đúng 23:00 trước các kênh còn lại
        is_night_batch = datetime.now().hour >= 22
        ordered_profiles = sorted(
            CHANNEL_PROFILES.values(),
            key=lambda p: (
                0 if (is_night_batch and p["folder_name"] == "audio_dem_khuyen") else 1,
                0
                if any((self.project_root / "output" / p["folder_name"] / "ready").glob("*.mp4"))
                else 1,
            ),
        )
        for profile in ordered_profiles:
            ch_folder = profile["folder_name"]
            if ch_folder not in active_ch_set:
                continue
            slots = ch_times_map.get(ch_folder) or default_times
            for slot in slots:
                now = datetime.now()
                today_str = now.strftime("%Y-%m-%d")
                now_minutes = now.hour * 60 + now.minute
                history = self._load_post_history()
                try:
                    sh, sm = map(int, slot.split(":"))
                except Exception:
                    continue

                # Hỗ trợ cửa sổ 180 phút xuyên qua mốc 00:00 đêm cho khung giờ khuya (như 23:30)
                from datetime import timedelta as _td

                yesterday_str = (now - _td(days=1)).strftime("%Y-%m-%d")
                y_jitter = self._compute_daily_jitter_minutes(yesterday_str, ch_folder, slot, max_jitter)
                y_eff_min = max(0, min(23 * 60 + 59, sh * 60 + sm + y_jitter))
                y_slot_key = f"{yesterday_str}_{ch_folder}_{slot}"

                if y_slot_key not in history and 0 <= (now_minutes + 1440 - y_eff_min) <= 180:
                    slot_key = y_slot_key
                    jitter = y_jitter
                    effective_slot_min = y_eff_min
                    is_due = True
                else:
                    slot_key = f"{today_str}_{ch_folder}_{slot}"
                    if slot_key in history:
                        continue
                    jitter = self._compute_daily_jitter_minutes(today_str, ch_folder, slot, max_jitter)
                    effective_slot_min = max(0, min(23 * 60 + 59, sh * 60 + sm + jitter))
                    is_due = 0 <= (now_minutes - effective_slot_min) <= 180

                # Kích hoạt nếu thời gian hiện tại nằm trong khoảng [effective_slot_min, effective_slot_min + 180 phút]
                if is_due:
                    if self._was_channel_uploaded_recently(ch_folder, min_interval_minutes=180.0) is None:
                        try:
                            self._get_next_ready_video_for_channel(ch_folder)
                        except Exception:
                            pass
                    lock_fh = self._acquire_browser_process_lock()
                    try:
                        # Đọc lại lịch sử sau khi đã giữ khóa độc quyền để không bao giờ bị 2 luồng/2 cửa sổ đăng trùng
                        history = self._load_post_history()
                        if slot_key in history:
                            continue
                        recent_min = self._was_channel_uploaded_recently(ch_folder, min_interval_minutes=180.0)
                        if recent_min is not None:
                            history[slot_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self._save_post_history(history)
                            logger.info(
                                f"[{ch_folder}] [Anti-Double-Post Guard] Khung {slot} đã được đáp ứng cách đây {recent_min:.1f} phút -> Ghi nhận hoàn tất."
                            )
                            continue

                        eff_hh, eff_mm = effective_slot_min // 60, effective_slot_min % 60
                        logger.info(
                            f"[Dòng 1 - Lịch Đăng Video] Đã điểm giờ đăng cố định hôm nay của kênh [{ch_folder}]: "
                            f"{eff_hh:02d}:{eff_mm:02d} (khung gốc {slot}, lệch {jitter:+d} phút)"
                        )
                        # Đặt chỗ trước vào posted_schedule_history.json trước khi mở Chrome
                        prev_last_ts = history.get(f"last_upload_ts_{ch_folder}")
                        claim_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        history[slot_key] = claim_ts
                        history[f"last_upload_ts_{ch_folder}"] = claim_ts
                        self._save_post_history(history)

                        res = self.upload_one_channel_video(ch_folder, headless=headless, force=True)
                        if res:
                            done_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            history = self._load_post_history()
                            history[slot_key] = done_ts
                            history[f"last_upload_ts_{ch_folder}"] = done_ts
                            self._save_post_history(history)
                            try:
                                v_name = Path(str(res.get("moved_to") or res.get("filename") or "")).name
                                if v_name:
                                    ms_ctrl.mark_platform_uploaded(ch_folder, v_name, "tiktok")
                                ms_ctrl.run_switch_4_clear()
                            except Exception:
                                pass
                            posted_results.append(res)
                        else:
                            # Nếu kho ready/ trống chưa có video để đăng, trả lại slot_key để thử lại khi có video mới
                            history = self._load_post_history()
                            history.pop(slot_key, None)
                            if prev_last_ts:
                                history[f"last_upload_ts_{ch_folder}"] = prev_last_ts
                            else:
                                history.pop(f"last_upload_ts_{ch_folder}", None)
                            self._save_post_history(history)
                    except Exception as exc:
                        logger.error(f"[Dòng 1 - Lịch Đăng Video] Lỗi tự động đăng kênh [{ch_folder}] khung {slot}: {exc}")
                        try:
                            history = self._load_post_history()
                            history.pop(slot_key, None)
                            if locals().get("prev_last_ts"):
                                history[f"last_upload_ts_{ch_folder}"] = locals()["prev_last_ts"]
                            else:
                                history.pop(f"last_upload_ts_{ch_folder}", None)
                            self._save_post_history(history)
                        except Exception:
                            pass
                    finally:
                        self._release_browser_process_lock(lock_fh)

        return posted_results


    def _ensure_intel_bridge_daemon_running(self) -> None:
        """Đảm bảo tiến trình `tiktok_pipeline.agy_intel_bridge --daemon` luôn sống trên Mac Intel (`x86_64`)."""
        import fcntl
        import platform as _plat
        import subprocess
        import sys as _sys

        if _plat.machine() != "x86_64":
            return
        try:
            lock_path = self.project_root / "workspace_temp" / ".agy_intel_bridge_daemon.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            probe_fh = open(lock_path, "a+", encoding="utf-8")
            try:
                fcntl.flock(probe_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe_fh.fileno(), fcntl.LOCK_UN)
            except BlockingIOError:
                probe_fh.close()
                return
            probe_fh.close()

            py_bin = self.project_root / "venv" / "bin" / "python"
            exe_str = str(py_bin) if py_bin.exists() else _sys.executable
            log_dir = self.project_root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_fh = open(log_dir / "agy_intel_bridge.log", "a", encoding="utf-8")
            subprocess.Popen(
                [exe_str, "-m", "tiktok_pipeline.agy_intel_bridge", "--daemon", "--poll", "20"],
                cwd=str(self.project_root),
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
            logger.info("[AGY Intel Bridge] Đã tự động kích hoạt lại Trạm Giao Tiếp AGY CLI Độc Lập (-m tiktok_pipeline.agy_intel_bridge)!")
        except Exception:
            pass


    def _is_stream1_upload_process_running(self, require_tty: bool = False) -> bool:
        """Kiểm tra xem tiến trình Dòng 1 (`--stream-upload` hoặc `--continuous`) có đang sống trên máy hay không."""
        import subprocess
        import sys as _sys

        if not require_tty and ("--stream-upload" in _sys.argv or "--continuous" in _sys.argv):
            return True
        try:
            out = subprocess.run(
                ["ps", "-Ao", "pid,tty,command"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
            for line in out.splitlines():
                s = line.strip()
                if not s or ("main.py" not in s and "tiktok_pipeline.main" not in s):
                    continue
                if "grep" in s or "pkill" in s or "osascript" in s or "sh -c" in s or "caffeinate" in s:
                    continue
                if "--stream-upload" in s or "--continuous" in s:
                    parts = s.split(None, 2)
                    if len(parts) >= 3 and parts[0].isdigit():
                        tty_val = parts[1].strip()
                        if require_tty and (tty_val.startswith("?") or tty_val == "-"):
                            continue
                        return True
        except Exception:
            pass
        return False


    def _is_stream2_filter24h_process_running(self, require_tty: bool = False) -> bool:
        """Kiểm tra xem tiến trình Dòng 2 (`--stream-filter24h`) có đang sống trên máy (hoặc trong cửa sổ Terminal) hay không."""
        import subprocess
        import sys as _sys

        if not require_tty and "--stream-filter24h" in _sys.argv:
            return True
        try:
            out = subprocess.run(
                ["ps", "-Ao", "pid,tty,command"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
            for line in out.splitlines():
                s = line.strip()
                if not s or ("main.py" not in s and "tiktok_pipeline.main" not in s):
                    continue
                if "grep" in s or "pkill" in s or "osascript" in s or "sh -c" in s or "caffeinate" in s:
                    continue
                if "--stream-filter24h" in s:
                    parts = s.split(None, 2)
                    if len(parts) >= 3 and parts[0].isdigit():
                        tty_val = parts[1].strip()
                        if require_tty and (tty_val.startswith("?") or tty_val == "-"):
                            continue
                        return True
        except Exception:
            pass
        return False


    def _auto_revive_stream1_upload_if_dead(self) -> None:
        """
        Tự động hồi sinh Cửa sổ 1 (`--stream-upload`) từ xa trên máy Intel nếu Cửa sổ 1 bị dừng
        khi người dùng không có ở nhà (nhờ Cửa sổ 2 `--stream-filter24h` vẫn đang sống).
        """
        import platform as _plat
        import subprocess
        import sys as _sys

        if _plat.machine() != "x86_64":
            return
        if "--stream-upload" in _sys.argv or "--continuous" in _sys.argv:
            return
        if self._is_stream1_upload_process_running(require_tty=True):
            return
        last_try = getattr(TikTokAutoPoster, "_last_s1_revive_ts", 0.0)
        if self._is_stream1_upload_process_running(require_tty=False) and (time.time() - last_try < 180.0):
            return
        setattr(TikTokAutoPoster, "_last_s1_revive_ts", time.time())

        proj_str = str(self.project_root)
        logger.info(
            "[Auto-Revive Watchdog] Phát hiện Cửa sổ Terminal 1 (--stream-upload) đang dừng trên máy Intel -> "
            "Đang tự động khởi động lại Cửa sổ 1 trực tiếp trên Terminal..."
        )

        # 1. Thử gõ lệnh trực tiếp vào cửa sổ Terminal đang rảnh trên màn hình máy Intel qua AppleScript
        apple_script = f'''
        tell application "Terminal"
            repeat with w in windows
                repeat with t in tabs of w
                    set procText to (processes of t as text)
                    if (not busy of t) or (procText does not contain "python" and procText does not contain "Python" and procText does not contain "caffeinate" and procText does not contain "agy") then
                        do script "cd '{proj_str}' && ./venv/bin/python main.py --stream-upload" in t
                        return "REVIVED_IN_TAB"
                    end if
                end repeat
            end repeat
            do script "cd '{proj_str}' && ./venv/bin/python main.py --stream-upload"
            return "REVIVED_IN_NEW_WINDOW"
        end tell
        '''
        try:
            subprocess.run(
                ["osascript", "-e", apple_script],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            time.sleep(2.5)
        except Exception:
            pass

        # 2. Nếu cửa sổ Terminal chưa nhận lệnh (hoặc chưa cấp quyền Automation cho Terminal), chạy ngầm Popen trực tiếp với file descriptor chuẩn
        if not self._is_stream1_upload_process_running(require_tty=False):
            try:
                py_bin = self.project_root / "venv" / "bin" / "python"
                exe_str = str(py_bin) if py_bin.exists() else _sys.executable
                log_path = self.project_root / "logs" / "daemon_24_7.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_fh = open(log_path, "a", encoding="utf-8")
                subprocess.Popen(
                    ["caffeinate", "-dimsu", exe_str, str(self.project_root / "main.py"), "--stream-upload", "--max-ready", "5"],
                    cwd=proj_str,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
                time.sleep(1.5)
                logger.info("[Auto-Revive Watchdog] Đã tự động bật lại tiến trình Dòng 1 (--stream-upload) thành công!")
            except Exception as exc:
                logger.debug(f"[Auto-Revive Watchdog] Lỗi phụ khi Popen Dòng 1: {exc}")


    def _auto_revive_stream2_filter24h_if_dead(self) -> None:
        """
        Tự động hồi sinh Cửa sổ Terminal Công tắc 3 (`main.py --stream-filter24h`) trên máy Mac Intel
        nếu cửa sổ Terminal Dòng 2 bị tắt (nhờ Cửa sổ 1 `--stream-upload` vẫn đang sống).
        """
        import os as _os
        import platform as _plat
        import subprocess
        import sys as _sys

        if _plat.machine() != "x86_64":
            return
        if "--stream-filter24h" in _sys.argv:
            return
        if self._is_stream2_filter24h_process_running(require_tty=True):
            return
        last_try = getattr(TikTokAutoPoster, "_last_s2_revive_ts", 0.0)
        if (time.time() - last_try) < 45.0:
            return
        if self._is_stream2_filter24h_process_running(require_tty=False) and (time.time() - last_try < 180.0):
            return
        setattr(TikTokAutoPoster, "_last_s2_revive_ts", time.time())

        proj_str = str(self.project_root)
        logger.info(
            "[Auto-Revive Watchdog] Phát hiện Cửa sổ Terminal Công tắc 3 (--stream-filter24h) đang dừng trên máy Intel -> "
            "Đang tự động khởi động lại Cửa sổ Công tắc 3 trực tiếp trên Terminal..."
        )

        # 1. Gõ lệnh trực tiếp vào tab Terminal đang rảnh (hoặc mở cửa sổ Terminal mới) trên màn hình Mac Intel qua AppleScript
        apple_script = f'''
        tell application "Terminal"
            repeat with w in windows
                repeat with t in tabs of w
                    set procText to (processes of t as text)
                    if (not busy of t) or (procText does not contain "python" and procText does not contain "Python" and procText does not contain "caffeinate" and procText does not contain "agy") then
                        do script "cd '{proj_str}' && ./venv/bin/python main.py --stream-filter24h" in t
                        return "REVIVED_IN_TAB"
                    end if
                end repeat
            end repeat
            do script "cd '{proj_str}' && ./venv/bin/python main.py --stream-filter24h"
            return "REVIVED_IN_NEW_WINDOW"
        end tell
        '''
        try:
            osa_res = subprocess.run(
                ["osascript", "-e", apple_script],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            time.sleep(2.5)
            if self._is_stream2_filter24h_process_running(require_tty=True):
                logger.info(
                    f"[Auto-Revive Watchdog] ✅ Đã hồi sinh Cửa sổ Terminal Công tắc 3 (--stream-filter24h) thành công ({(osa_res.stdout or '').strip()})!"
                )
                # Nếu có bản chạy ngầm không gắn TTY (??) từ trước thì dọn sạch để chỉ giữ đúng cửa sổ Terminal hiển thị
                try:
                    out = subprocess.run(
                        ["ps", "-Ao", "pid,tty,command"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    ).stdout
                    for line in out.splitlines():
                        if "--stream-filter24h" in line and ("main.py" in line or "tiktok_pipeline.main" in line):
                            parts = line.strip().split(None, 2)
                            if len(parts) >= 3 and parts[0].isdigit():
                                tty_val = parts[1].strip()
                                if tty_val.startswith("?") or tty_val == "-":
                                    try:
                                        _os.kill(int(parts[0]), 9)
                                    except Exception:
                                        pass
                except Exception:
                    pass
                return
        except Exception:
            pass

        # 2. Fallback: nếu chưa chạy được trong Terminal, khởi chạy ngầm Popen để đảm bảo Công tắc 3 không bao giờ đứt quãng
        if not self._is_stream2_filter24h_process_running(require_tty=False):
            try:
                py_bin = self.project_root / "venv" / "bin" / "python"
                exe_str = str(py_bin) if py_bin.exists() else _sys.executable
                log_path = self.project_root / "logs" / "stream2_filter24h.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_fh = open(log_path, "a", encoding="utf-8")
                subprocess.Popen(
                    ["caffeinate", "-dimsu", exe_str, str(self.project_root / "main.py"), "--stream-filter24h"],
                    cwd=proj_str,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
                time.sleep(1.5)
                logger.info("[Auto-Revive Watchdog] Đã tự động bật lại tiến trình Công tắc 3 (--stream-filter24h) dự phòng thành công!")
            except Exception as exc:
                logger.debug(f"[Auto-Revive Watchdog] Lỗi phụ khi Popen Dòng 2: {exc}")


    def check_and_run_scheduled_posts(self) -> List[Dict[str, Any]]:
        """
        Điều phối cả 2 dòng hoạt động:
        - Dòng 1: Giữ nguyên khung giờ đăng video cho 4 kênh (`check_and_run_stream1_upload_schedule`).
        - Dòng 2: Mở 4 kênh chạy Bộ lọc 24 giờ 7 lần/ngày × 10 video/kênh (`check_and_run_stream2_filter24h_schedule`).
        """
        posted_results = self.check_and_run_stream1_upload_schedule()
        self.check_and_run_stream2_filter24h_schedule()
        return posted_results

