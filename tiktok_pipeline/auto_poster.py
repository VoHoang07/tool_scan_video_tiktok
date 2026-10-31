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

try:
    _importlib.reload(_crawler_mod)
    _importlib.reload(_mdc_mod)
except Exception:
    pass

from .crawler import CHANNEL_PROFILES, StoryAudioCrawler, resolve_channel_profile
from .growth_24h_filter import TikTok24hFilterEngine
from .macos_desktop_controller import MacOSDesktopController
from .utils import logger

DEFAULT_SCHEDULE_CONFIG: Dict[str, Any] = {
    "timezone": "Asia/Ho_Chi_Minh",
    "default_post_times": ["07:45", "12:15", "19:45"],
    "channel_post_times": {
        "video_AI_hay": ["07:25", "12:05", "19:20"],
        "baby_AI": ["08:05", "12:42", "20:05"],
        "audio_dem_khuyen": ["08:48", "13:25", "21:15"],
        "tu_chan_gioi": ["09:18", "14:10", "20:42"],
        "meo_vat_gia_dinh": ["07:10", "11:35", "18:50"],
        "suc_khoe_duong_sinh": ["06:50", "11:55", "19:35"],
        "phu_nu_khi_chat": ["08:28", "13:05", "20:25"],
        "an_vat_moi_ngay": ["07:38", "12:22", "21:00"],
        "nuoi_con_eq": ["08:15", "12:52", "19:50"],
        "phu_kien_tien_ich": ["09:05", "13:45", "21:35"],
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

# Script dọn dẹp biến toàn cục Playwright (KHÔNG ghi đè Proxy lên console/prototype để giữ 100% [native code] cho webmssdk.js)
STEALTH_INIT_SCRIPT = """
(() => {
    if (window.__ttStealthGuardApplied) return;
    window.__ttStealthGuardApplied = true;
    const automationProps = [
        '__playwright', '__pw_manual', '__PW_inspect',
        'cdc_adoQpoasnfa76pfcZLmcfl_Array',
        'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
        'cdc_adoQpoasnfa76pfcZLmcfl_Symbol'
    ];
    for (const prop of automationProps) {
        try { delete window[prop]; } catch (e) {}
    }
    try {
        const nativeToString = Function.prototype.toString;
        const spoofedFns = new WeakMap();
        window.__ttRegisterNativeFn = (fn, origFn) => {
            try { spoofedFns.set(fn, nativeToString.call(origFn)); } catch (e) {}
        };
        const wrappedToString = function() {
            if (spoofedFns.has(this)) return spoofedFns.get(this);
            return nativeToString.apply(this, arguments);
        };
        spoofedFns.set(wrappedToString, nativeToString.call(nativeToString));
        Object.defineProperty(Function.prototype, 'toString', {
            value: wrappedToString,
            configurable: true,
            writable: true
        });

        const hasTrapGetter = (arg) => {
            if (!arg || (typeof arg !== 'object' && typeof arg !== 'function')) return false;
            const dStack = Object.getOwnPropertyDescriptor(arg, 'stack');
            const dId = Object.getOwnPropertyDescriptor(arg, 'id');
            return Boolean((dStack && dStack.get) || (dId && dId.get) || arg instanceof Error);
        };
        for (const m of ['debug', 'dir', 'dirxml', 'table', 'trace', 'log']) {
            const orig = console[m];
            if (typeof orig !== 'function') continue;
            const origStr = nativeToString.call(orig);
            const safeFn = function() {
                for (let i = 0; i < arguments.length; i++) {
                    if (hasTrapGetter(arguments[i])) return undefined;
                }
                return orig.apply(this, arguments);
            };
            spoofedFns.set(safeFn, origStr);
            Object.defineProperty(console, m, {
                value: safeFn,
                configurable: true,
                writable: true
            });
        }
    } catch (e) {}
})();
"""


class TikTokAutoPoster:
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
            self._auto_revive_stream1_upload_if_dead()
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

    @staticmethod
    def _cleanup_stale_chrome_profile_locks(user_data_dir: Path) -> None:
        """
        Dọn dẹp tiến trình Chrome bị treo (orphan) và các file khóa SingletonLock/SingletonSocket
        trong `user_data_dir` trước khi mở phiên trình duyệt mới để không bao giờ bị lỗi Profile In Use.
        """
        import subprocess

        try:
            subprocess.run(
                ["pkill", "-9", "-f", str(user_data_dir)],
                capture_output=True,
                timeout=5,
                check=False,
            )
            time.sleep(0.6)
        except Exception:
            pass
        for lock_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            try:
                lock_file = user_data_dir / lock_name
                if lock_file.exists() or lock_file.is_symlink():
                    lock_file.unlink()
            except Exception:
                pass

    def _acquire_browser_process_lock(self) -> Any:
        """
        Khóa đồng bộ liên tiến trình (`fcntl.flock`) để khi chạy 2 cửa sổ Terminal độc lập
        (`--stream-upload` và `--stream-filter24h`), 2 dòng hoạt động tuần tự nhường Chrome & chuột OS cho nhau.
        """
        import fcntl

        lock_path = self.project_root / "workspace_temp" / ".tiktok_browser_session.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return fh

    def _release_browser_process_lock(self, fh: Any = None) -> None:
        import fcntl

        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass

    @staticmethod
    def _get_or_create_channel_fingerprint(user_data_dir: Path) -> Dict[str, Any]:
        """
        Tạo và lưu trữ cấu hình Anti-Detect Fingerprint cố định riêng biệt cho từng kênh (`profile_fingerprint.json`)
        nhằm cô lập hoàn toàn 10 kênh trên cùng 1 máy Mac (khác độ phân giải cửa sổ, số nhân CPU, RAM,
        WebGL Renderer, hạt giống nhiễu Canvas/AudioContext và Proxy riêng).
        """
        import os

        user_data_dir.mkdir(parents=True, exist_ok=True)
        fp_file = user_data_dir / "profile_fingerprint.json"
        ch_name = user_data_dir.name
        seed_int = int(hashlib.md5(ch_name.encode("utf-8")).hexdigest()[:8], 16)

        window_presets = [
            (1366, 860, 20, 20),
            (1440, 880, 36, 28),
            (1512, 910, 48, 24),
            (1480, 890, 28, 34),
            (1400, 870, 42, 18),
            (1536, 864, 16, 30),
            (1420, 900, 32, 22),
            (1460, 880, 24, 26),
            (1390, 850, 38, 32),
            (1500, 895, 30, 20),
        ]
        webgl_renderers = [
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Pro, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M3 Pro, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Max, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Max, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M3 Max, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
            "ANGLE (Apple, ANGLE Metal Renderer: Apple M4, Unspecified Version)",
        ]
        screen_presets = [
            (1440, 900),
            (1512, 982),
            (1680, 1050),
            (1728, 1117),
            (1920, 1080),
            (2560, 1440),
            (1600, 900),
            (1536, 960),
            (1800, 1169),
            (2048, 1152),
        ]
        cores_pool = [8, 10, 12, 14, 16]
        mem_pool = [8, 16, 32]

        idx = seed_int % len(window_presets)
        w, h, px, py = window_presets[idx]
        scr_w, scr_h = screen_presets[idx]
        default_fp = {
            "channel": ch_name,
            "device_hash_salt": hashlib.sha256(f"tt_dev_{ch_name}".encode("utf-8")).hexdigest()[:16],
            "screen_width": scr_w,
            "screen_height": scr_h,
            "window_width": w,
            "window_height": h,
            "window_x": px,
            "window_y": py,
            "hardware_concurrency": cores_pool[seed_int % len(cores_pool)],
            "device_memory": mem_pool[(seed_int // 3) % len(mem_pool)],
            "webgl_vendor": "Google Inc. (Apple)",
            "webgl_renderer": webgl_renderers[idx],
            "canvas_noise_r": ((seed_int % 5) + 1),
            "canvas_noise_g": (((seed_int >> 4) % 5) + 1),
            "canvas_noise_b": (((seed_int >> 8) % 5) + 1),
            "audio_noise_seed": round(((seed_int % 97) + 1) * 1e-7, 10),
            "proxy_server": "",
        }

        if fp_file.exists():
            try:
                loaded = json.loads(fp_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    default_fp.update(loaded)
            except Exception:
                pass

        cfg_proxy = ""
        try:
            sched_path = Path(__file__).resolve().parent.parent / "schedule_config.json"
            if sched_path.exists():
                sched_data = json.loads(sched_path.read_text(encoding="utf-8"))
                ch_proxies = sched_data.get("channel_proxies") or {}
                if isinstance(ch_proxies, dict):
                    cfg_proxy = str(ch_proxies.get(ch_name) or "").strip()
        except Exception:
            pass

        # Ưu tiên lấy Proxy riêng của kênh từ schedule_config.json hoặc biến môi trường (TIKTOK_PROXY_<ten_kenh>)
        env_proxy = (
            cfg_proxy
            or os.getenv(f"TIKTOK_PROXY_{ch_name}")
            or os.getenv(f"TIKTOK_PROXY_{ch_name.upper()}")
            or os.getenv("TIKTOK_PROXY_DEFAULT")
            or default_fp.get("proxy_server")
            or ""
        ).strip()
        default_fp["proxy_server"] = env_proxy

        try:
            fp_file.write_text(json.dumps(default_fp, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return default_fp

    @staticmethod
    def _parse_playwright_proxy(proxy_raw: str) -> Optional[Dict[str, str]]:
        """Phân tách chuỗi Proxy (hỗ trợ `http://user:pass@ip:port`, `socks5://user:pass@ip:port`, `ip:port:user:pass`)."""
        from urllib.parse import urlsplit

        s = (proxy_raw or "").strip()
        if not s:
            return None
        # Hỗ trợ định dạng phổ biến khi mua Proxy Việt Nam: ip:port:user:pass
        if "://" not in s and s.count(":") == 3:
            host, port, user, pw = s.split(":", 3)
            return {
                "server": f"http://{host}:{port}",
                "username": user,
                "password": pw,
            }
        if "://" not in s:
            s = f"http://{s}"
        parsed = urlsplit(s)
        if not parsed.hostname or not parsed.port:
            return {"server": s}
        out: Dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            out["username"] = parsed.username
        if parsed.password:
            out["password"] = parsed.password
        return out

    def _launch_stealth_chrome_context(self, playwright_obj: Any, user_data_dir: Path, headless: bool = False) -> Any:
        """
        Khởi chạy Google Chrome thật với cấu hình Anti-Detect cô lập theo từng kênh:
        - Đọc `profile_fingerprint.json` riêng của từng kênh (Screen, Window, CPU/RAM, WebGL1/2, Canvas 2D, AudioContext, MediaDevices ID).
        - Tự động gắn Proxy riêng của kênh (nếu cấu hình) và chặn rò rỉ IP thật qua WebRTC.
        """
        TikTokAutoPoster._cleanup_stale_chrome_profile_locks(user_data_dir)
        fp = TikTokAutoPoster._get_or_create_channel_fingerprint(user_data_dir)
        win_w = int(fp.get("window_width", 1366))
        win_h = int(fp.get("window_height", 860))
        win_x = int(fp.get("window_x", 20))
        win_y = int(fp.get("window_y", 20))

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            "--enable-gpu-rasterization",
            "--enable-zero-copy",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--enforce-webrtc-ip-permission-check",
            f"--window-size={win_w},{win_h}",
            f"--window-position={win_x},{win_y}",
            "--lang=vi-VN",
        ]
        ignore_args = [
            "--enable-automation",
            "--no-sandbox",
            "--disable-field-trial-config",
            "--force-color-profile=srgb",
            "--metrics-recording-only",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--allow-pre-commit-input",
            "--export-tagged-pdf",
            "--no-service-autorun",
            "--disable-sync",
        ]

        ctx_kwargs: Dict[str, Any] = {
            "user_data_dir": str(user_data_dir),
            "headless": headless,
            "no_viewport": True,
            "locale": "vi-VN",
            "timezone_id": "Asia/Ho_Chi_Minh",
            "args": launch_args,
            "ignore_default_args": ignore_args,
        }
        proxy_cfg = TikTokAutoPoster._parse_playwright_proxy(str(fp.get("proxy_server") or ""))
        if proxy_cfg:
            ctx_kwargs["proxy"] = proxy_cfg

        try:
            context = playwright_obj.chromium.launch_persistent_context(
                channel="chrome",
                **ctx_kwargs,
            )
        except Exception:
            TikTokAutoPoster._cleanup_stale_chrome_profile_locks(user_data_dir)
            context = playwright_obj.chromium.launch_persistent_context(**ctx_kwargs)

        hw_cores = int(fp.get("hardware_concurrency", 8))
        dev_mem = int(fp.get("device_memory", 16))
        scr_w = int(fp.get("screen_width", 1440))
        scr_h = int(fp.get("screen_height", 900))
        webgl_vend = str(fp.get("webgl_vendor", "Google Inc. (Apple)"))
        webgl_rend = str(fp.get("webgl_renderer", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)"))
        noise_r = int(fp.get("canvas_noise_r", 1))
        noise_g = int(fp.get("canvas_noise_g", 2))
        noise_b = int(fp.get("canvas_noise_b", 1))
        audio_seed = float(fp.get("audio_noise_seed", 1e-7))
        dev_salt = str(fp.get("device_hash_salt", "a1b2c3d4e5f60718"))

        per_channel_fp_script = f"""
        (() => {{
            try {{
                const reg = window.__ttRegisterNativeFn || (() => {{}});
                Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw_cores}, configurable: true }});
                Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {dev_mem}, configurable: true }});

                // 1. Cô lập Screen Resolution theo từng kênh
                if (window.screen) {{
                    Object.defineProperty(window.screen, 'width', {{ get: () => {scr_w}, configurable: true }});
                    Object.defineProperty(window.screen, 'height', {{ get: () => {scr_h}, configurable: true }});
                    Object.defineProperty(window.screen, 'availWidth', {{ get: () => {scr_w}, configurable: true }});
                    Object.defineProperty(window.screen, 'availHeight', {{ get: () => {scr_h - 28}, configurable: true }});
                }}

                // 2. Cô lập WebGL1 & WebGL2 GPU Renderer/Vendor
                const patchWebGL = (proto) => {{
                    if (!proto || !proto.getParameter) return;
                    const origGetParam = proto.getParameter;
                    const wrappedGetParam = function(p) {{
                        if (p === 37445) return {json.dumps(webgl_vend)};
                        if (p === 37446) return {json.dumps(webgl_rend)};
                        return origGetParam.apply(this, arguments);
                    }};
                    reg(wrappedGetParam, origGetParam);
                    Object.defineProperty(proto, 'getParameter', {{
                        value: wrappedGetParam, configurable: true, writable: true
                    }});
                }};
                if (typeof WebGLRenderingContext !== 'undefined') patchWebGL(WebGLRenderingContext.prototype);
                if (typeof WebGL2RenderingContext !== 'undefined') patchWebGL(WebGL2RenderingContext.prototype);

                // 3. Cô lập Canvas 2D Fingerprint (dịch vi điểm ảnh cố định riêng từng kênh)
                if (typeof CanvasRenderingContext2D !== 'undefined' && CanvasRenderingContext2D.prototype.getImageData) {{
                    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
                    const wrappedGetImageData = function() {{
                        const imgData = origGetImageData.apply(this, arguments);
                        if (imgData && imgData.data && imgData.data.length >= 40) {{
                            imgData.data[0] = (imgData.data[0] + {noise_r}) & 255;
                            imgData.data[1] = (imgData.data[1] + {noise_g}) & 255;
                            imgData.data[2] = (imgData.data[2] + {noise_b}) & 255;
                        }}
                        return imgData;
                    }};
                    reg(wrappedGetImageData, origGetImageData);
                    Object.defineProperty(CanvasRenderingContext2D.prototype, 'getImageData', {{
                        value: wrappedGetImageData, configurable: true, writable: true
                    }});
                }}

                // 4. Cô lập AudioContext / OfflineAudioContext Fingerprint
                if (typeof AudioBuffer !== 'undefined' && AudioBuffer.prototype.getChannelData) {{
                    const origGetChannelData = AudioBuffer.prototype.getChannelData;
                    const wrappedGetChannelData = function() {{
                        const data = origGetChannelData.apply(this, arguments);
                        if (data && data.length > 10 && !data.__ttSalted) {{
                            for (let i = 0; i < Math.min(data.length, 64); i += 7) {{
                                data[i] = data[i] + {audio_seed};
                            }}
                            try {{ Object.defineProperty(data, '__ttSalted', {{ value: true, enumerable: false }}); }} catch (e) {{}}
                        }}
                        return data;
                    }};
                    reg(wrappedGetChannelData, origGetChannelData);
                    Object.defineProperty(AudioBuffer.prototype, 'getChannelData', {{
                        value: wrappedGetChannelData, configurable: true, writable: true
                    }});
                }}

                // 5. Cô lập MediaDevices Hardware ID (Microphone/Camera deviceId & groupId)
                if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {{
                    const origEnum = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
                    const rawEnum = MediaDevices.prototype.enumerateDevices;
                    const salt = {json.dumps(dev_salt)};
                    const wrappedEnum = function() {{
                        return origEnum().then((list) => list.map((d, idx) => ({{
                            deviceId: d.deviceId === 'default' ? 'default' : (salt + idx + (d.deviceId || '').slice(0, 48)),
                            kind: d.kind,
                            label: d.label,
                            groupId: salt + 'g' + idx + (d.groupId || '').slice(0, 46),
                            toJSON() {{ return this; }}
                        }})));
                    }};
                    if (rawEnum) reg(wrappedEnum, rawEnum);
                    Object.defineProperty(MediaDevices.prototype, 'enumerateDevices', {{
                        value: wrappedEnum, configurable: true, writable: true
                    }});
                }}
            }} catch (e) {{}}
        }})();
        """
        context.add_init_script(STEALTH_INIT_SCRIPT + "\n" + per_channel_fp_script)
        TikTokAutoPoster._restore_synced_channel_session_if_needed(context, user_data_dir)
        return context

    @staticmethod
    def _session_cipher_bytes(data: bytes) -> bytes:
        """Mã hóa / giải mã đối xứng (SHA-256 stream XOR) dùng khóa nội bộ từ `.env` để đồng bộ phiên Silicon -> Intel."""
        import hashlib

        secret = os.environ.get("DOUYIN_COOKIES", "") + "::tiktok_channel_session_sync_v1"
        seed = hashlib.sha256(secret.encode("utf-8")).digest()
        out = bytearray(len(data))
        block = b""
        counter = 0
        for idx, b in enumerate(data):
            if idx % 32 == 0:
                block = hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
                counter += 1
            out[idx] = b ^ block[idx % 32]
        return bytes(out)

    @staticmethod
    def _save_synced_channel_session(ch_folder: str, state_dict: Dict[str, Any]) -> Optional[Path]:
        """Lưu trạng thái đăng nhập (cookies + localStorage) đã mã hóa vào `remote_telemetry/sessions/<ch_folder>.bin`."""
        import hashlib
        import zlib

        try:
            cookies = state_dict.get("cookies") or []
            has_tt_auth = any(
                c.get("name") in ("sessionid", "sessionid_ss", "sid_tt", "uid_tt")
                for c in cookies
                if isinstance(c, dict)
            )
            if not cookies or not has_tt_auth:
                return None
            sess_dir = Path(__file__).resolve().parent.parent / "remote_telemetry" / "sessions"
            sess_dir.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(state_dict, ensure_ascii=False).encode("utf-8")
            compressed = zlib.compress(raw, level=9)
            enc = TikTokAutoPoster._session_cipher_bytes(compressed)
            out_path = sess_dir / f"{ch_folder}.bin"
            out_path.write_bytes(enc)
            # Đồng thời cập nhật dấu vết tại profile local để không tự ghi đè lại chính nó
            local_prof = Path(__file__).resolve().parent.parent / "browser_profiles" / ch_folder
            if local_prof.exists():
                sig = hashlib.sha256(enc).hexdigest()[:24]
                (local_prof / "_synced_session_hash.txt").write_text(sig, encoding="utf-8")
            return out_path
        except Exception as exc:
            logger.debug(f"[{ch_folder}] Không thể lưu phiên đồng bộ: {exc}")
            return None

    @staticmethod
    def _restore_synced_channel_session_if_needed(context: Any, user_data_dir: Path) -> bool:
        """
        Tự động nạp phiên đăng nhập TikTok (Cookies + LocalStorage) đã đồng bộ từ máy Silicon sang máy Intel
        nếu `browser_profiles/<ten_kenh>` trên máy Intel chưa có phiên hoặc có bản cập nhật mới.
        """
        import hashlib
        import zlib

        try:
            ch_folder = user_data_dir.name
            sess_path = Path(__file__).resolve().parent.parent / "remote_telemetry" / "sessions" / f"{ch_folder}.bin"
            if not sess_path.exists():
                return False
            enc = sess_path.read_bytes()
            if not enc:
                return False
            sig = hashlib.sha256(enc).hexdigest()[:24]
            stamp_file = user_data_dir / "_synced_session_hash.txt"
            if stamp_file.exists():
                try:
                    if stamp_file.read_text(encoding="utf-8").strip() == sig:
                        return False
                except Exception:
                    pass

            compressed = TikTokAutoPoster._session_cipher_bytes(enc)
            raw = zlib.decompress(compressed)
            state_dict = json.loads(raw.decode("utf-8"))
            cookies = state_dict.get("cookies") or []
            if cookies:
                context.add_cookies(cookies)
            origins = state_dict.get("origins") or []
            if origins:
                ls_map = {
                    o.get("origin"): o.get("localStorage") or []
                    for o in origins
                    if isinstance(o, dict) and o.get("origin") and o.get("localStorage")
                }
                if ls_map:
                    ls_script = f"""
                    (() => {{
                        try {{
                            const map = {json.dumps(ls_map, ensure_ascii=False)};
                            const items = map[window.location.origin];
                            if (Array.isArray(items)) {{
                                for (const item of items) {{
                                    if (item && item.name) {{
                                        window.localStorage.setItem(item.name, item.value || '');
                                    }}
                                }}
                            }}
                        }} catch (e) {{}}
                    }})();
                    """
                    context.add_init_script(ls_script)
            user_data_dir.mkdir(parents=True, exist_ok=True)
            stamp_file.write_text(sig, encoding="utf-8")
            logger.info(f"[{ch_folder}] Đã tự động đồng bộ phiên đăng nhập TikTok ({len(cookies)} cookies) sang máy hiện tại!")
            return True
        except Exception as exc:
            logger.debug(f"[{user_data_dir.name}] Không thể khôi phục phiên đồng bộ: {exc}")
            return False

    def _human_move_mouse(self, page: Any, target_x: float, target_y: float) -> None:
        """
        Di chuyển con trỏ trình duyệt tốc độ cao (4-6 bước mượt, tổng ~8ms) tới `(target_x, target_y)`
        để thao tác tức thì không gây trễ/lag.
        """
        start_x, start_y = getattr(self, "_last_vp_mouse", (520.0, 430.0))
        steps = random.randint(4, 6)
        for s in range(1, steps + 1):
            frac = s / steps
            jitter_x = random.uniform(-1.5, 1.5) * (1.0 - frac)
            jitter_y = random.uniform(-1.5, 1.5) * (1.0 - frac)
            cur_x = start_x + (target_x - start_x) * frac + jitter_x
            cur_y = start_y + (target_y - start_y) * frac + jitter_y
            page.mouse.move(cur_x, cur_y)
            time.sleep(0.0015)
        self._last_vp_mouse = (float(target_x), float(target_y))

    def _os_click_viewport(self, page: Any, viewport_x: float, viewport_y: float) -> None:
        """
        Di chuyển chuột phần cứng macOS (`CoreGraphics CGEvent`) theo đường cong Bezier tới tọa độ màn hình
        tương ứng với `(viewport_x, viewport_y)` và click thật ở tầng Hệ điều hành (`screenY != clientY`).
        """
        try:
            sx, sy = self.desktop.convert_viewport_to_screen_coords(page, viewport_x, viewport_y)
            self.desktop.fast_bezier_click_os(sx, sy, clicks=1)
            self._last_vp_mouse = (float(viewport_x), float(viewport_y))
        except Exception:
            self._human_move_mouse(page, viewport_x, viewport_y)
            time.sleep(random.uniform(0.012, 0.025))
            page.mouse.click(viewport_x, viewport_y, delay=random.randint(10, 22))

    def _dismiss_all_studio_popovers(self, page: Any, ch_folder: str) -> int:
        """
        Tự động phát hiện và bấm đóng tất cả hộp thoại hướng dẫn / giới thiệu tính năng mới của TikTok Studio
        (ví dụ: 'Bổ sung tính năng chỉnh sửa mới' -> nút đỏ 'Đã hiểu', 'Got it', 'OK', 'Đóng', 'Bỏ qua', 'Tiếp theo').
        """
        dismissed = 0
        for _ in range(4):
            try:
                btn_pos = page.evaluate(
                    """() => {
                        const targetLabels = [
                            'Đã hiểu', 'Got it', 'Tôi đã hiểu', 'Bỏ qua', 'Skip',
                            'Để sau', 'Not now', 'Đóng', 'Close', 'OK', 'Tiếp theo', 'Hoàn tất'
                        ];
                        const hintKeywords = [
                            'Bổ sung tính năng', 'tính năng chỉnh sửa mới', 'chuyên nghiệp và cuốn hút',
                            'Hướng dẫn', 'Mẹo', 'Tính năng mới'
                        ];
                        const allEls = Array.from(document.querySelectorAll('button, div[role="button"], span, div'));

                        // 1. Ưu tiên tìm nút 'Đã hiểu' / 'Got it' (click trực tiếp bằng JS + trả về tọa độ)
                        for (const el of allEls) {
                            if (el.children.length > 2) continue;
                            const txt = (el.innerText || el.textContent || '').trim();
                            if (!targetLabels.includes(txt)) continue;
                            const btn = el.closest('button') || el.closest('[role="button"]') || el;
                            const r = btn.getBoundingClientRect();
                            if (r.width > 24 && r.height > 16 && r.top > 50) {
                                try { btn.click(); } catch (e) {}
                                try { el.click(); } catch (e) {}
                                return {
                                    label: txt,
                                    x: r.left + r.width / 2,
                                    y: Math.min(window.innerHeight - 10, Math.max(10, r.top + Math.min(r.height / 2, 16)))
                                };
                            }
                        }

                        // 2. Nếu có thẻ chứa 'Bổ sung tính năng chỉnh sửa mới', tìm mọi nút bên trong nó
                        for (const container of Array.from(document.querySelectorAll('div, section, aside'))) {
                            const cTxt = (container.innerText || '').trim();
                            if (cTxt.length > 350 || !hintKeywords.some(k => cTxt.includes(k))) continue;
                            const btns = Array.from(container.querySelectorAll('button, [role="button"]'));
                            for (const b of btns) {
                                const r = b.getBoundingClientRect();
                                if (r.width > 20 && r.height > 14) {
                                    try { b.click(); } catch (e) {}
                                    return {
                                        label: (b.innerText || 'PopoverButton').trim(),
                                        x: r.left + r.width / 2,
                                        y: Math.min(window.innerHeight - 10, Math.max(10, r.top + 14))
                                    };
                                }
                            }
                        }
                        return null;
                    }"""
                )
                if btn_pos and "x" in btn_pos and "y" in btn_pos:
                    dismissed += 1
                    logger.info(
                        f"[{ch_folder}] [Studio Popover] Đã tự động bấm tắt hộp thoại hướng dẫn: '{btn_pos.get('label')}'"
                    )
                    try:
                        self._os_click_viewport(page, float(btn_pos["x"]), float(btn_pos["y"]))
                    except Exception:
                        pass
                    time.sleep(0.8)
                else:
                    break
            except Exception:
                break
        return dismissed

    def _simulate_human_warmup(self, page: Any, ch_folder: str) -> None:
        """
        Mô phỏng người dùng thật mở trang chủ TikTok, di chuột và lướt xem 2-3 video (12-22 giây)
        kết hợp thực thi 1 lượt "Bộ lọc 24 giờ" (Xem hết video + Thả tim + Sao chép liên kết +
        Thêm yêu thích + Follow + Bình luận) trước khi bấm vào trang Upload để vừa tăng follow chéo,
        vừa tạo điểm tin cậy hành vi (Behavioral Trust Score) cao nhất cho `mssdk`.
        """
        try:
            logger.info(f"[{ch_folder}] [Anti-Detect Warmup] Đang lướt dạo trang chủ TikTok 15s như người dùng thật...")
            page.goto("https://www.tiktok.com/?lang=vi-VN", wait_until="domcontentloaded", timeout=45000)
            time.sleep(random.uniform(3.0, 5.0))
            self._human_move_mouse(page, random.uniform(420, 780), random.uniform(320, 560))

            for _ in range(random.randint(2, 3)):
                page.mouse.wheel(0, random.randint(450, 750))
                time.sleep(random.uniform(3.2, 6.0))
                self._human_move_mouse(page, random.uniform(380, 820), random.uniform(280, 600))

            cfg = self.ensure_schedule_config()
            if cfg.get("filter_24h_enabled", True):
                logger.info(
                    f"[{ch_folder}] [Bộ lọc 24h Warmup] Kết hợp chạy 1 video tương tác chéo 'Bộ lọc 24 giờ' trước khi đăng bài..."
                )
                self.filter_24h.run_session_on_page(page, ch_folder, video_count=1)
        except Exception as exc:
            logger.debug(f"[{ch_folder}] Bỏ qua bước warmup nhẹ: {exc}")

    def _scroll_upload_form(self, page: Any, delta_y: int) -> None:
        """
        Cuộn khung nội dung chính bên phải của trang TikTok Studio Upload (`rect.left > 240`),
        đảm bảo không cuộn nhầm vào thanh menu bên trái (`Bài đăng`).
        """
        try:
            page.mouse.move(680, 500)
            page.mouse.wheel(0, delta_y)
        except Exception:
            pass
        try:
            page.evaluate(
                """(dy) => {
                    window.scrollBy({ top: dy, behavior: 'smooth' });
                    const containers = Array.from(document.querySelectorAll('div, main, section'));
                    for (const el of containers) {
                        const st = window.getComputedStyle(el);
                        if ((st.overflowY === 'auto' || st.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 40) {
                            const r = el.getBoundingClientRect();
                            if (r.left >= 180 && r.width > 400) {
                                el.scrollTop += dy;
                            }
                        }
                    }
                }""",
                delta_y,
            )
        except Exception:
            pass

    def _move_caret_to_end_of_editor(self, page: Any, ensure_trailing_space: bool = False) -> None:
        """
        Đưa con trỏ soạn thảo (caret) về chính xác vị trí cuối cùng của ô `Mô tả` DraftJS trên macOS.
        Tuyệt đối KHÔNG dùng `caption_box.click()` (vì click vào tâm 50%/50% sẽ chèn chữ vào giữa câu đầu)
        và KHÔNG dùng phím `End` (trên macOS phím `End` cuộn trang làm mất focus thay vì đưa trỏ về cuối câu).
        """
        try:
            needs_space = page.evaluate(
                """() => {
                    const editor = document.querySelector(
                        'div.public-DraftEditor-content[contenteditable="true"], ' +
                        'div[contenteditable="true"][role="textbox"], ' +
                        'div[contenteditable="true"]'
                    );
                    if (!editor) return false;
                    editor.scrollIntoView({ block: 'center', inline: 'nearest' });
                    editor.focus();
                    const sel = window.getSelection();
                    if (sel) {
                        const range = document.createRange();
                        range.selectNodeContents(editor);
                        range.collapse(false);
                        sel.removeAllRanges();
                        sel.addRange(range);
                    }
                    const raw = editor.innerText || editor.textContent || '';
                    return raw.length > 0 && !/\\s$/.test(raw.replace(/\\n+$/, ''));
                }"""
            )
            time.sleep(0.08)
            page.keyboard.press("Meta+ArrowDown")
            time.sleep(0.05)
            page.keyboard.press("Meta+ArrowRight")
            if ensure_trailing_space and needs_space:
                time.sleep(0.05)
                page.keyboard.type(" ", delay=40)
        except Exception:
            pass

    def _select_hashtag_suggestion_if_open(self, page: Any, tag_word: str) -> bool:
        """
        Kiểm tra xem menu gợi ý Hashtag thực sự của TikTok Studio có đang mở bên dưới ô `Mô tả` hay không.
        Loại trừ tuyệt đối nút thanh công cụ `# Hashtag` / `@ Nhắc đến`.
        Nếu có mục gợi ý khớp với `#tag_word`, click chọn trực tiếp để tạo thẻ Hashtag Pill chuẩn có thể bấm vào được.
        """
        try:
            pos = page.evaluate(
                """(kw) => {
                    const kwLower = (kw || '').toLowerCase().replace(/^#+/, '');
                    if (!kwLower) return null;
                    const editor = document.querySelector(
                        'div.public-DraftEditor-content[contenteditable="true"], ' +
                        'div[contenteditable="true"][role="textbox"], ' +
                        'div[contenteditable="true"]'
                    );
                    const edRect = editor ? editor.getBoundingClientRect() : { top: 100, bottom: 260, left: 260 };

                    const candidates = Array.from(
                        document.querySelectorAll(
                            '[role="option"], [role="listbox"] li, [role="listbox"] div, ' +
                            '[class*="hashtag" i] li, [class*="hashtag" i] div, ' +
                            '[class*="suggestion" i] li, [class*="suggestion" i] div, ' +
                            '[class*="mention" i] li, [class*="mention" i] div'
                        )
                    );
                    const matches = [];
                    for (const el of candidates) {
                        if (editor && editor.contains(el)) continue;
                        if (el.children.length > 5) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 45 || r.height < 18 || r.height > 72) continue;
                        if (r.top < edRect.top - 10 || r.top > edRect.bottom + 340) continue;

                        const txt = (el.innerText || el.textContent || '').trim();
                        if (!txt) continue;
                        const tLower = txt.toLowerCase();

                        // Loại trừ tuyệt đối nút công cụ '# Hashtag' và '@ Nhắc đến' nằm ngay dưới ô Mô tả
                        if (
                            /^#\\s*hashtag$/i.test(txt) ||
                            /^hashtag$/i.test(txt) ||
                            /^#\\s+/.test(txt) ||
                            tLower.includes('nhắc đến') ||
                            tLower.includes('mention')
                        ) {
                            continue;
                        }

                        const firstToken = tLower.split(/\\s+/)[0].replace(/^#+/, '');
                        if (firstToken === kwLower || firstToken.startsWith(kwLower)) {
                            matches.push({
                                x: r.left + Math.min(90, r.width / 2),
                                y: r.top + r.height / 2,
                                exact: firstToken === kwLower ? 2 : 1,
                                top: r.top,
                                label: txt.slice(0, 50)
                            });
                        }
                    }
                    if (matches.length === 0) return null;
                    matches.sort((a, b) => (b.exact - a.exact) || (a.top - b.top));
                    return matches[0];
                }""",
                tag_word,
            )
            if pos and "x" in pos and "y" in pos:
                page.mouse.click(float(pos["x"]), float(pos["y"]), delay=45)
                time.sleep(0.35)
                return True
        except Exception:
            pass
        return False

    def _clear_and_type_caption_hashtags(
        self,
        page: Any,
        ch_folder: str,
        caption: str,
        hashtags_str: str,
        video_stem: str,
    ) -> None:
        """
        Xóa sạch tên file mặc định (`20261025_152239_tiktok_story_...`) mà TikTok Studio tự điền vào ô `Mô tả`,
        đảm bảo 100% `caption` là mô tả tiếng Việt theo ngữ cảnh, sau đó đưa con trỏ về ĐÚNG CUỐI CÂU
        (`_move_caret_to_end_of_editor`) và gắn từng thẻ `#hashtag` Hot Search tách biệt bằng khoảng trắng
        để tất cả hashtag đều xanh/bấm vào ra danh sách xu hướng trên TikTok.
        """
        if self.crawler.is_raw_filename_caption(caption, video_stem) or not (hashtags_str or "").strip():
            rec_cap, rec_tags, _ = self.crawler.recover_contextual_caption_and_hashtags(
                ch_folder, video_stem
            )
            if self.crawler.is_raw_filename_caption(caption, video_stem):
                caption = rec_cap
            if not (hashtags_str or "").strip():
                hashtags_str = rec_tags

        self._dismiss_all_studio_popovers(page, ch_folder)
        self._scroll_upload_form(page, -1500)
        time.sleep(0.8)

        caption_box = page.locator(
            'div.public-DraftEditor-content[contenteditable="true"], div[contenteditable="true"][role="textbox"], div[contenteditable="true"]'
        ).first
        caption_box.wait_for(state="visible", timeout=25000)
        caption_box.scroll_into_view_if_needed()

        # Đợi TikTok tự điền tên file mặc định vào ô Mô tả rồi tắt mọi popup hướng dẫn
        time.sleep(random.uniform(1.2, 2.0))
        self._dismiss_all_studio_popovers(page, ch_folder)

        box_bounds = caption_box.bounding_box()
        if box_bounds:
            self._human_move_mouse(
                page,
                box_bounds["x"] + box_bounds["width"] * random.uniform(0.3, 0.6),
                box_bounds["y"] + box_bounds["height"] * random.uniform(0.3, 0.7),
            )

        # Xóa sạch tên file mặc định bằng phím tắt macOS chuẩn (Meta+A -> Backspace)
        for attempt in range(6):
            self._dismiss_all_studio_popovers(page, ch_folder)
            try:
                caption_box.click(click_count=3)
                time.sleep(0.15)
                page.keyboard.press("Backspace")
            except Exception:
                caption_box.click()
            time.sleep(0.15)
            page.keyboard.press("Meta+A")
            time.sleep(0.1)
            page.keyboard.press("Backspace")
            time.sleep(0.1)
            page.keyboard.press("Control+A")
            time.sleep(0.1)
            page.keyboard.press("Backspace")

            remaining = ""
            try:
                remaining = (caption_box.inner_text() or "").strip()
            except Exception:
                pass

            if remaining:
                self._move_caret_to_end_of_editor(page, ensure_trailing_space=False)
                for _ in range(min(120, len(remaining) + 18)):
                    page.keyboard.press("Backspace")
                page.keyboard.press("Meta+ArrowUp")
                page.keyboard.press("Meta+ArrowLeft")
                for _ in range(min(60, len(remaining) + 10)):
                    page.keyboard.press("Delete")
                time.sleep(0.2)
                try:
                    remaining = (caption_box.inner_text() or "").strip()
                except Exception:
                    remaining = ""

            if not remaining or (
                video_stem not in remaining
                and "tiktok_story" not in remaining
                and "douyin_story" not in remaining
                and not re.search(r"\b20\d{6}_\d{6}\b", remaining)
            ):
                break
            logger.debug(f"[{ch_folder}] Xóa lại tên file mặc định lần {attempt + 1}: '{remaining}'")

        time.sleep(random.uniform(0.35, 0.6))

        # Gõ Kịch bản mô tả tiếng Việt theo ngữ cảnh vào ô Mô tả đang trống
        clean_caption = caption.strip()
        caption_box.click()
        time.sleep(0.2)
        page.keyboard.insert_text(clean_caption)
        time.sleep(0.25)
        self._move_caret_to_end_of_editor(page, ensure_trailing_space=True)
        time.sleep(random.uniform(0.35, 0.6))

        # Chuẩn hóa danh sách #hashtag
        raw_tokens = [t.strip() for t in hashtags_str.split() if t.strip()]
        tags: List[str] = []
        for t in raw_tokens:
            norm_t = t if t.startswith("#") else f"#{t}"
            if len(norm_t) > 1 and norm_t.lower() not in [x.lower() for x in tags]:
                tags.append(norm_t)
        if not tags:
            tags = ["#xuhuong", "#xuhuongtiktok", "#fyp", "#tiktokvn"]

        # Gõ từng #hashtag ở ĐÚNG CUỐI VĂN BẢN (có dấu cách phía trước '#'):
        # 1. Đưa con trỏ về cuối câu bằng JS Selection + Meta+ArrowDown (TUYỆT ĐỐI không click vào tâm ô làm nhảy trỏ vào giữa chữ!)
        # 2. Gõ '#' + từ khóa để mở gợi ý Hashtag thực của TikTok Studio.
        # 3. Nếu có mục gợi ý khớp từ khóa trong dropdown -> click chọn để gắn thẻ Hashtag Pill.
        # 4. Đưa con trỏ về cuối và gõ dấu cách ' ' để khóa thẻ hashtag và chuẩn bị cho thẻ tiếp theo.
        for tag in tags:
            tag_word = tag.lstrip("#").strip()
            if not tag_word:
                continue
            self._move_caret_to_end_of_editor(page, ensure_trailing_space=True)
            time.sleep(0.1)
            page.keyboard.type("#", delay=55)
            time.sleep(0.22)
            page.keyboard.type(tag_word, delay=random.randint(35, 58))
            time.sleep(random.uniform(1.0, 1.35))

            picked = self._select_hashtag_suggestion_if_open(page, tag_word)
            if not picked:
                self._move_caret_to_end_of_editor(page, ensure_trailing_space=False)
                page.keyboard.type(" ", delay=55)
            else:
                time.sleep(0.25)
                self._move_caret_to_end_of_editor(page, ensure_trailing_space=True)
            time.sleep(random.uniform(0.2, 0.35))

        # Kiểm tra xác nhận nội dung trong ô Mô tả đã có đủ Kịch bản tiếng Việt + tất cả Hashtag tách rời ở cuối câu
        try:
            final_text = (caption_box.inner_text() or "").strip()
            has_broken_midword_tag = bool(re.search(r"[^\s#]#[a-zA-Z0-9_]", final_text))
            tag_count_in_text = len(re.findall(r"(?:^|\s)#[a-zA-Z0-9_]+", final_text))
            if (
                not final_text
                or self.crawler.is_raw_filename_caption(final_text, video_stem)
                or has_broken_midword_tag
                or tag_count_in_text < min(3, len(tags))
            ):
                logger.info(
                    f"[{ch_folder}] Chuẩn hóa lại khối Mô tả + Hashtag ở cuối câu (tag_count={tag_count_in_text}, midword={has_broken_midword_tag})..."
                )
                self._move_caret_to_end_of_editor(page, ensure_trailing_space=False)
                page.keyboard.press("Meta+A")
                time.sleep(0.1)
                page.keyboard.press("Backspace")
                time.sleep(0.15)
                page.keyboard.insert_text(clean_caption + " ")
                time.sleep(0.2)
                for tag in tags:
                    tw = tag.lstrip("#").strip()
                    if not tw:
                        continue
                    self._move_caret_to_end_of_editor(page, ensure_trailing_space=True)
                    page.keyboard.type(f"#{tw}", delay=35)
                    time.sleep(0.85)
                    if not self._select_hashtag_suggestion_if_open(page, tw):
                        self._move_caret_to_end_of_editor(page, ensure_trailing_space=False)
                        page.keyboard.type(" ", delay=45)
                final_text = (caption_box.inner_text() or "").strip()
            logger.info(
                f"[{ch_folder}] Đã nhập xong Mô tả & {len(tags)} Hashtag chuẩn vào TikTok Studio: \"{final_text[:160]}...\""
            )
        except Exception as exc:
            logger.debug(f"[{ch_folder}] Kiểm tra lại văn bản ô Mô tả: {exc}")

    def _click_public_option_in_open_dropdown(self, page: Any) -> bool:
        """
        Tìm và click chính xác tùy chọn `Mọi người` (`Everyone` / `Công khai`) nằm bên trong
        menu dropdown Quyền riêng tư đang mở (có chứa đồng thời `Bạn bè` / `Chỉ mình tôi`).
        Đảm bảo không bao giờ click nhầm vào nút của hàng khác.
        """
        pos = page.evaluate(
            """() => {
                const targetLabels = ['Mọi người', 'Công khai', 'Everyone', 'Public'];
                const siblingLabels = ['Bạn bè', 'Chỉ mình tôi', 'Friends', 'Only me', 'Private'];
                const allElements = Array.from(document.querySelectorAll('*'));
                const candidates = [];
                for (const el of allElements) {
                    if (el.children.length > 3) continue;
                    const txt = (el.innerText || el.textContent || '').trim();
                    if (!targetLabels.includes(txt)) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 5 || rect.height <= 5) continue;

                    let parent = el.parentElement;
                    let isMenuOption = false;
                    for (let depth = 0; depth < 6 && parent; depth++) {
                        const pTxt = (parent.innerText || '').trim();
                        if (siblingLabels.some(s => pTxt.includes(s)) && pTxt.length < 260) {
                            isMenuOption = true;
                            break;
                        }
                        parent = parent.parentElement;
                    }
                    if (isMenuOption) {
                        candidates.push({
                            x: rect.left + rect.width / 2,
                            y: rect.top + rect.height / 2,
                            area: rect.width * rect.height
                        });
                    }
                }
                if (candidates.length === 0) return null;
                candidates.sort((a, b) => a.area - b.area);
                return candidates[0];
            }"""
        )
        if pos and "x" in pos and "y" in pos:
            self._os_click_viewport(page, float(pos["x"]), float(pos["y"]))
            return True
        return False

    def _set_switch_near_label(
        self, page: Any, label_keywords: List[str], desired_on: bool
    ) -> bool:
        """
        Tìm nút gạt (switch/toggle) nằm cùng dòng với nhãn `label_keywords` (ví dụ:
        `Nội dung tải lên chất lượng cao`, `Khai báo nội dung bài đăng`, `Nội dung do AI tạo`,
        `Kiểm tra bản quyền nhạc`, `Kiểm tra nội dung nhanh`) và bật/tắt theo `desired_on`.
        """
        try:
            action = page.evaluate(
                """({ keywords, wantOn }) => {
                    const els = Array.from(document.querySelectorAll('span, div, label, p, h3, h4'));
                    let targetLabelRect = null;
                    for (const el of els) {
                        if (el.children.length > 4) continue;
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (!txt || txt.length > 90) continue;
                        if (keywords.some(k => txt.includes(k))) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 10 && r.height > 8) {
                                targetLabelRect = r;
                                break;
                            }
                        }
                    }
                    if (!targetLabelRect) return { found: false };

                    const switches = Array.from(
                        document.querySelectorAll('[role="switch"], input[type="checkbox"], [class*="Switch"], [class*="switch"]')
                    );
                    let bestSw = null;
                    let bestDist = 9999;
                    for (const sw of switches) {
                        const r = sw.getBoundingClientRect();
                        if (r.width < 12 || r.height < 10) continue;
                        const dy = Math.abs((r.top + r.height / 2) - (targetLabelRect.top + targetLabelRect.height / 2));
                        const dx = r.left - targetLabelRect.left;
                        if (dy <= 38 && dx > -20 && dx < 650) {
                            if (dy < bestDist) {
                                bestDist = dy;
                                bestSw = { el: sw, rect: r };
                            }
                        }
                    }
                    if (!bestSw) return { found: false };

                    const swEl = bestSw.el;
                    const ariaChecked = swEl.getAttribute('aria-checked');
                    const cls = (swEl.className || '') + ' ' + ((swEl.parentElement && swEl.parentElement.className) || '');
                    let isOn = false;
                    if (ariaChecked === 'true' || swEl.checked === true) {
                        isOn = true;
                    } else if (ariaChecked === 'false') {
                        isOn = false;
                    } else if (/checked|active|is-checked|on/i.test(cls)) {
                        isOn = true;
                    }

                    return {
                        found: true,
                        isOn: isOn,
                        needClick: isOn !== wantOn,
                        x: bestSw.rect.left + bestSw.rect.width / 2,
                        y: bestSw.rect.top + bestSw.rect.height / 2
                    };
                }""",
                {"keywords": label_keywords, "wantOn": desired_on},
            )
            if action and action.get("found") and action.get("needClick"):
                self._human_move_mouse(page, float(action["x"]), float(action["y"]))
                time.sleep(random.uniform(0.2, 0.4))
                page.mouse.click(float(action["x"]), float(action["y"]))
                time.sleep(random.uniform(0.4, 0.7))
                return True
        except Exception as exc:
            logger.debug(f"Bỏ qua chỉnh switch {label_keywords}: {exc}")
        return False

    @staticmethod
    def detect_video_aigc_disclosure_needed(
        video_path: Path, caption: str = "", hashtags: str = "", ch_folder: str = ""
    ) -> bool:
        """
        Tự động nhận diện từng video trước khi đăng (bất kể ở kênh nào) xem có cần BẬT nút
        'Nội dung do AI tạo' (AIGC Disclosure) hay không:
        - Nếu video thuộc diện mô phỏng nhân vật/bối cảnh AI rõ rệt (baby_AI, video_AI_hay, hoặc chứa
          từ khóa/hashtag #babyai, #hoathinhai, #phimnganai, #aianimation, #aitutien, 'AI ') -> BẬT (True).
        - Nếu là video mẹo vặt đời sống, sức khỏe, tâm lý, kinh doanh, truyện kể Audio không mô phỏng người thật AI -> TẮT (False).
        """
        combined = f"{ch_folder} {video_path.name} {caption} {hashtags}".lower()
        ai_markers = (
            "baby_ai",
            "video_ai_hay",
            "#babyai",
            "#hoathinhai",
            "#phimnganai",
            "#aianimation",
            "#aitutien",
            "#khamphaai",
            "câu chuyện ai",
            "hoạt hình ai",
            "em bé ai",
            "ai 생성",
            "aigc",
        )
        return any(m in combined for m in ai_markers)

    def _configure_upload_settings_public(
        self, page: Any, ch_folder: str, aigc_on: bool = False
    ) -> None:
        """
        Tự động cài đặt toàn bộ mục `Cài đặt` và `Kiểm tra` trên trang Upload của TikTok Studio:
        1. `Thời điểm đăng`: Chọn `Bây giờ`.
        2. `Người có thể nhìn thấy bài đăng này`: Bắt buộc chọn `Mọi người` (Công khai).
        3. `Nội dung tải lên chất lượng cao`: BẬT (`ON`).
        4. `Cho phép người dùng`: Tích chọn `Bình luận`.
        5. `Khai báo nội dung bài đăng`: TẮT (`OFF`).
        6. `Nội dung do AI tạo`: Tự động BẬT (`ON`) nếu `aigc_on=True` (video có AI), ngược lại TẮT (`OFF`).
        7. `Kiểm tra bản quyền nhạc` & `Kiểm tra nội dung nhanh`: BẬT (`ON`).
        """
        logger.info(
            f"[{ch_folder}] Đang tự động cấu hình mục Cài đặt (Công khai = 'Mọi người', Nhãn AIGC = {'BẬT' if aigc_on else 'TẮT'})..."
        )
        try:
            # Cuộn xuống phần Cài đặt trong khung form chính bên phải
            self._scroll_upload_form(page, 480)
            time.sleep(random.uniform(1.0, 1.6))

            # 1. Thời điểm đăng -> Bây giờ
            try:
                now_radio = page.locator('label:has-text("Bây giờ"), span:has-text("Bây giờ")').first
                if now_radio.count() > 0 and now_radio.is_visible():
                    now_radio.click()
                    time.sleep(0.3)
            except Exception:
                pass

            # 2. Người có thể nhìn thấy bài đăng này -> Mọi người (Công khai)
            privacy_trigger = page.evaluate(
                """() => {
                    const labels = ['Người có thể nhìn thấy bài đăng này', 'Ai có thể xem video này', 'Who can view this post'];
                    const allEls = Array.from(document.querySelectorAll('span, div, p, label, h3, h4'));
                    let labelRect = null;
                    for (const el of allEls) {
                        if (el.children.length > 3) continue;
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (labels.some(l => txt.includes(l))) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 20 && r.height > 8) {
                                labelRect = r;
                                break;
                            }
                        }
                    }
                    if (!labelRect) return null;

                    // Tìm combobox/dropdown nằm ngay bên dưới nhãn "Người có thể nhìn thấy bài đăng này"
                    const candidates = Array.from(document.querySelectorAll('button, [role="combobox"], [role="button"], div'));
                    let best = null;
                    let bestDist = 9999;
                    for (const c of candidates) {
                        const txt = (c.innerText || c.textContent || '').trim();
                        if (!['Mọi người', 'Bạn bè', 'Chỉ mình tôi', 'Everyone', 'Friends', 'Only me', 'Private'].includes(txt)) {
                            continue;
                        }
                        const r = c.getBoundingClientRect();
                        if (r.width < 80 || r.width > 520 || r.height < 22 || r.height > 70) continue;
                        const dy = r.top - labelRect.bottom;
                        const dx = Math.abs(r.left - labelRect.left);
                        if (dy >= -10 && dy <= 95 && dx <= 120) {
                            if (dy < bestDist) {
                                bestDist = dy;
                                best = {
                                    text: txt,
                                    x: r.left + r.width / 2,
                                    y: r.top + r.height / 2
                                };
                            }
                        }
                    }
                    return best;
                }"""
            )
            if privacy_trigger:
                cur_priv = privacy_trigger.get("text", "")
                logger.info(
                    f"[{ch_folder}] Trạng thái 'Người có thể nhìn thấy bài đăng này' hiện tại: '{cur_priv}' -> Đảm bảo chọn 'Mọi người'..."
                )
                self._human_move_mouse(page, float(privacy_trigger["x"]), float(privacy_trigger["y"]))
                time.sleep(random.uniform(0.25, 0.5))
                page.mouse.click(float(privacy_trigger["x"]), float(privacy_trigger["y"]))
                time.sleep(random.uniform(0.7, 1.2))
                clicked_pub = self._click_public_option_in_open_dropdown(page)
                if not clicked_pub:
                    page.keyboard.press("Escape")
                time.sleep(random.uniform(0.5, 0.9))

            # 3. Nội dung tải lên chất lượng cao -> BẬT (ON)
            self._set_switch_near_label(
                page, ["Nội dung tải lên chất lượng cao", "High-quality uploads"], desired_on=True
            )

            # 4. Cho phép người dùng -> Đảm bảo "Bình luận" được tích chọn
            try:
                page.evaluate(
                    """() => {
                        const labels = Array.from(document.querySelectorAll('label, span, div'));
                        for (const lb of labels) {
                            if (lb.children.length > 3) continue;
                            const txt = (lb.innerText || '').trim();
                            if (txt === 'Bình luận' || txt === 'Comment' || txt === 'Comments') {
                                const parent = lb.closest('label') || lb.parentElement;
                                if (parent) {
                                    const cb = parent.querySelector('input[type="checkbox"]');
                                    if (cb && !cb.checked) {
                                        parent.click();
                                    }
                                }
                                break;
                            }
                        }
                    }"""
                )
            except Exception:
                pass

            # 5. Khai báo nội dung bài đăng -> TẮT (OFF); Nội dung do AI tạo -> BẬT/TẮT tự động theo từng video (aigc_on)
            self._set_switch_near_label(
                page, ["Khai báo nội dung bài đăng", "Disclose post content"], desired_on=False
            )
            self._set_switch_near_label(
                page, ["Nội dung do AI tạo", "AI-generated content"], desired_on=bool(aigc_on)
            )

            # Cuộn xuống phần Kiểm tra & nút Đăng
            self._scroll_upload_form(page, 520)
            time.sleep(random.uniform(0.8, 1.3))

            # 6. Kiểm tra bản quyền nhạc & Kiểm tra nội dung nhanh -> BẬT (ON)
            self._set_switch_near_label(
                page, ["Kiểm tra bản quyền nhạc", "Music copyright check"], desired_on=True
            )
            self._set_switch_near_label(
                page, ["Kiểm tra nội dung nhanh", "Content check lite"], desired_on=True
            )

        except Exception as exc:
            logger.warning(f"[{ch_folder}] Lưu ý nhỏ khi chỉnh Cài đặt trang Upload: {exc}")

    @staticmethod
    def _is_video_upload_100_percent_done(page: Any) -> bool:
        """
        Kiểm tra xem tiến trình tải file video lên TikTok Studio đã đạt 100% chưa
        (không còn hiển thị phần trăm đang tải như `47.12%` hay `Còn ... giây`).
        """
        try:
            uploading_state = page.evaluate(
                """() => {
                    const bodyText = document.body ? (document.body.innerText || '') : '';
                    // Nếu đang hiện phần trăm dạng 47.12% kèm "Còn ... giây" hoặc "MB/"
                    const hasProgressPct = /\\b\\d{1,2}\\.\\d{1,2}%\\b/.test(bodyText);
                    const hasRemainingSec = /Còn\\s+\\d+\\s*(giây|phút)/i.test(bodyText);
                    const isUploadingLabel = /Đang tải lên|Uploading/i.test(bodyText);
                    return hasProgressPct || hasRemainingSec || isUploadingLabel;
                }"""
            )
            return not bool(uploading_state)
        except Exception:
            return True

    def _find_exact_post_button(
        self, page: Any, scroll_into_view: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Tìm CHÍNH XÁC nút đỏ `Đăng` (`button[data-e2e="post_video_button"]` hoặc nút có nhãn đúng bằng `'Đăng'` / `'Post'`)
        nằm ở cuối biểu mẫu Upload (bên phải thanh menu, `rect.left > 220`).
        TUYỆT ĐỐI KHÔNG BAO GIỜ chọn nhầm nút `'Bài đăng'` ở thanh menu bên trái hay nút `'Hủy bỏ'` / `'Thoát'`.
        """
        try:
            return page.evaluate(
                """(doScroll) => {
                    let targetBtn = document.querySelector('button[data-e2e="post_video_button"], [data-e2e="post_video_button"]');
                    if (!targetBtn) {
                        const allBtns = Array.from(document.querySelectorAll('button, [role="button"]'));
                        for (const b of allBtns) {
                            if (b.closest('nav, aside')) continue;
                            const e2e = (b.getAttribute('data-e2e') || '').toLowerCase();
                            if (e2e.includes('cancel') || e2e.includes('discard') || e2e.includes('draft')) continue;
                            const txt = (b.innerText || b.textContent || '').trim();
                            if (txt !== 'Đăng' && txt !== 'Post') continue;
                            const r = b.getBoundingClientRect();
                            if (r.left < 220 || r.width < 50 || r.height < 24) continue;
                            targetBtn = b;
                            break;
                        }
                    }
                    if (!targetBtn) return null;
                    if (doScroll) {
                        try {
                            targetBtn.scrollIntoView({ block: 'center', inline: 'nearest' });
                        } catch (e) {}
                    }
                    const rect = targetBtn.getBoundingClientRect();
                    const disabledAttr = targetBtn.getAttribute('disabled');
                    const ariaDisabled = targetBtn.getAttribute('aria-disabled');
                    const cls = (targetBtn.className || '').toString();
                    const isDisabled = (
                        targetBtn.disabled === true ||
                        disabledAttr !== null ||
                        ariaDisabled === 'true' ||
                        /disabled|loading/i.test(cls)
                    );
                    return {
                        found: rect.width > 20 && rect.height > 15,
                        enabled: !isDisabled,
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        width: rect.width,
                        height: rect.height,
                        text: (targetBtn.innerText || targetBtn.textContent || '').trim(),
                        dataE2e: targetBtn.getAttribute('data-e2e') || ''
                    };
                }""",
                scroll_into_view,
            )
        except Exception:
            return None

    def _click_confirm_modal_if_present(self, page: Any) -> bool:
        """
        Xử lý thông minh hộp thoại popup của TikTok Studio:
        1. Nếu vô tình hiện hộp thoại hỏi 'Bạn có muốn rời khỏi / Hủy bỏ bài đăng?' -> Bấm ngay 'Tiếp tục chỉnh sửa' / 'Ở lại'
           để TUYỆT ĐỐI KHÔNG BAO GIỜ thoát trang khi chưa đăng!
        2. Nếu hiện hộp thoại xác nhận đăng bài ('Đăng ngay', 'Tiếp tục đăng', 'Post now', 'Continue to post') -> Bấm xác nhận đăng!
        """
        try:
            pos = page.evaluate(
                """() => {
                    const stayLabels = [
                        'Tiếp tục chỉnh sửa', 'Ở lại', 'Quay lại', 'Keep editing', 'Stay'
                    ];
                    const confirmPostLabels = [
                        'Đăng ngay', 'Tiếp tục đăng', 'Post now', 'Continue to post'
                    ];
                    const forbiddenExitLabels = [
                        'Thoát', 'Rời khỏi', 'Hủy bỏ', 'Hủy', 'Leave', 'Discard', 'Exit'
                    ];
                    const modals = Array.from(
                        document.querySelectorAll('[role="dialog"], [class*="Modal"], [class*="modal"], [class*="Dialog"], [class*="Popup"]')
                    );
                    if (modals.length === 0) return null;
                    for (const m of modals) {
                        const mRect = m.getBoundingClientRect();
                        if (mRect.width < 100 || mRect.height < 60) continue;
                        const btns = Array.from(m.querySelectorAll('button, [role="button"]'));

                        // Ưu tiên 1: Nếu là hộp thoại cảnh báo thoát/hủy -> bấm 'Tiếp tục chỉnh sửa' / 'Ở lại'
                        for (const b of btns) {
                            const txt = (b.innerText || b.textContent || '').trim();
                            if (stayLabels.some(l => txt === l || txt.includes(l))) {
                                const r = b.getBoundingClientRect();
                                if (r.width > 20 && r.height > 15) {
                                    return { x: r.left + r.width / 2, y: r.top + r.height / 2, text: txt };
                                }
                            }
                        }

                        // Ưu tiên 2: Nếu là hộp thoại xác nhận Đăng ngay / Tiếp tục đăng
                        for (const b of btns) {
                            const txt = (b.innerText || b.textContent || '').trim();
                            if (forbiddenExitLabels.some(f => txt === f || txt.includes(f))) continue;
                            if (confirmPostLabels.some(l => txt === l || txt.includes(l))) {
                                const r = b.getBoundingClientRect();
                                if (r.width > 20 && r.height > 15) {
                                    return { x: r.left + r.width / 2, y: r.top + r.height / 2, text: txt };
                                }
                            }
                        }
                    }
                    return null;
                }"""
            )
            if pos and "x" in pos and "y" in pos:
                logger.info(f"[Modal Handler] Tự động bấm nút hộp thoại: '{pos.get('text')}'")
                self._human_move_mouse(page, float(pos["x"]), float(pos["y"]))
                time.sleep(random.uniform(0.25, 0.5))
                page.mouse.click(float(pos["x"]), float(pos["y"]))
                time.sleep(random.uniform(1.0, 1.8))
                return True
        except Exception:
            pass
        return False

    def _ensure_posts_public_in_content_page(self, page: Any, ch_folder: str) -> int:
        """
        BƯỚC SETTING QUAN TRỌNG SAU KHI BẤM ĐĂNG (Ảnh chụp màn hình 5 - `tiktok.com/tiktokstudio/content`):
        1. Chuyển tới trang quản lý `https://www.tiktok.com/tiktokstudio/content` (mục `Bài đăng`).
        2. Tìm tất cả các bài đăng có cột `Quyền riêng tư` đang bị để ở `Chỉ mình tôi` / `Chỉ mình ...` / `Bạn bè`
           (kể cả bài vừa mới đăng và các bài đăng trước đó vừa xét duyệt xong).
        3. Bấm vào nút dropdown `Chỉ mình ...` ở từng bài và chọn `Mọi người` (Công khai).
        4. Trả về tổng số bài đăng đã được chuyển sang `Mọi người` (Công khai).
        """
        switched_count = 0
        try:
            if "tiktokstudio/content" not in page.url:
                logger.info(
                    f"[{ch_folder}] Đang mở trang Quản lý Bài đăng (tiktok.com/tiktokstudio/content) để kiểm tra & bật Công khai ('Mọi người')..."
                )
                page.goto(
                    "https://www.tiktok.com/tiktokstudio/content",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )

            for pass_idx in range(3):
                time.sleep(random.uniform(4.5, 6.5))

                # Tìm tất cả nút Quyền riêng tư đang là "Chỉ mình...", "Chỉ mình tôi", "Bạn bè", "Only me", "Private"
                private_buttons = page.evaluate(
                    """() => {
                        const privatePrefixes = ['Chỉ mình', 'Bạn bè', 'Only me', 'Private', 'Friends'];
                        const allEls = Array.from(
                            document.querySelectorAll('button, [role="button"], [role="combobox"], div, span')
                        );
                        const raw = [];
                        for (const el of allEls) {
                            if (el.children.length > 4) continue;
                            const txt = (el.innerText || el.textContent || '').trim();
                            if (!txt || txt.length > 26) continue;
                            if (!privatePrefixes.some(p => txt.startsWith(p) || txt === p)) continue;
                            const rect = el.getBoundingClientRect();
                            // Các hàng bài đăng trong bảng nằm bên dưới thanh công cụ lọc (rect.top > 205)
                            if (rect.width < 45 || rect.width > 210 || rect.height < 18 || rect.height > 60 || rect.top < 205) {
                                continue;
                            }
                            raw.push({
                                text: txt,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                top: rect.top
                            });
                        }
                        raw.sort((a, b) => a.top - b.top);
                        const deduped = [];
                        for (const item of raw) {
                            if (!deduped.some(d => Math.abs(d.x - item.x) < 35 && Math.abs(d.y - item.y) < 22)) {
                                deduped.push(item);
                            }
                        }
                        return deduped;
                    }"""
                )

                if not private_buttons:
                    if pass_idx == 0:
                        logger.info(
                            f"[{ch_folder}] Tất cả bài đăng trên trang Quản lý đều đã ở trạng thái 'Mọi người' (Công khai)!"
                        )
                    break

                logger.info(
                    f"[{ch_folder}] [Lượt {pass_idx + 1}] Phát hiện {len(private_buttons)} bài đăng đang ở trạng thái riêng tư ({[b['text'] for b in private_buttons]}). Đang chuyển sang 'Mọi người' (Công khai)..."
                )

                for btn in private_buttons:
                    bx, by = float(btn["x"]), float(btn["y"])
                    self._os_click_viewport(page, bx, by)
                    time.sleep(random.uniform(0.85, 1.35))

                    if self._click_public_option_in_open_dropdown(page):
                        switched_count += 1
                        logger.info(
                            f"[{ch_folder}] -> Đã chọn 'Mọi người' (Công khai) cho bài đăng (trước đó: '{btn['text']}')."
                        )
                        time.sleep(random.uniform(1.0, 1.8))
                        self._click_confirm_modal_if_present(page)
                    else:
                        page.keyboard.press("Escape")
                        time.sleep(0.5)

                # Nếu vẫn còn bài vừa đăng đang xét duyệt, đợi vài giây rồi tải lại bảng Bài đăng để kiểm tra lần nữa
                if pass_idx < 2:
                    time.sleep(random.uniform(4.0, 6.0))
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=35000)
                    except Exception:
                        pass

        except Exception as exc:
            logger.warning(f"[{ch_folder}] Lưu ý khi chuyển quyền riêng tư tại tiktokstudio/content: {exc}")

        return switched_count

    def _delete_post_in_content_page(
        self,
        page: Any,
        ch_folder: str,
        match_keywords: Optional[List[str]] = None,
        delete_top_if_no_match: bool = False,
    ) -> List[str]:
        """
        Tại trang `https://www.tiktok.com/tiktokstudio/content`, tìm tất cả bài đăng khớp `match_keywords`
        (hoặc bài mới nhất trên cùng nếu `delete_top_if_no_match=True`), bấm nút `...` ở cột `Hành động`
        -> chọn `Xóa` / `Xoá video` (`Delete`) -> xác nhận `Xóa` trong hộp thoại modal.
        """
        deleted_captions: List[str] = []
        try:
            if "tiktokstudio/content" not in (page.url or ""):
                logger.info(
                    f"[{ch_folder}] Đang mở trang Quản lý Bài đăng (tiktok.com/tiktokstudio/content) để kiểm tra và xóa video lỗi..."
                )
                page.goto(
                    "https://www.tiktok.com/tiktokstudio/content",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
            time.sleep(random.uniform(5.2, 6.8))
            self._dismiss_all_studio_popovers(page, ch_folder)

            kw_lower = [k.strip().lower() for k in (match_keywords or []) if k.strip()]
            self._last_delete_debug: List[Any] = []

            for pass_idx in range(4):
                rows_info = page.evaluate(
                    """() => {
                        const norm = (s) => (s || '').normalize('NFC').trim();
                        const privacyPrefixes = ['Mọi người', 'Chỉ mình', 'Bạn bè', 'Everyone', 'Only me', 'Private', 'Friends'];
                        const allEls = Array.from(document.querySelectorAll('button, [role="button"], [role="combobox"], div, span, a, p'));
                        const privacyBtns = [];
                        for (const el of allEls) {
                            if (el.children.length > 4) continue;
                            const txt = norm(el.innerText || el.textContent || '');
                            if (!txt || txt.length > 26) continue;
                            if (!privacyPrefixes.some(p => txt.startsWith(p) || txt === p)) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width < 45 || r.width > 210 || r.height < 18 || r.height > 60 || r.top < 205) continue;
                            const cy = r.top + r.height / 2;
                            if (!privacyBtns.some(b => Math.abs(b.y - cy) < 28)) {
                                privacyBtns.push({ y: cy, top: r.top });
                            }
                        }
                        privacyBtns.sort((a, b) => a.top - b.top);

                        const rows = [];
                        const svgs = Array.from(document.querySelectorAll('svg'));
                        const textNodes = Array.from(document.querySelectorAll('*'));
                        for (const pb of privacyBtns) {
                            const rowY = pb.y;
                            // Gom toàn bộ text mô tả + thời lượng + giờ đăng ở nửa bên trái cùng hàng (r.left từ 80..750)
                            const capParts = [];
                            for (const el of textNodes) {
                                if (el.children.length > 3) continue;
                                const r = el.getBoundingClientRect();
                                if (r.left < 80 || r.left > 750 || r.width < 12 || r.height < 8 || r.height > 120) continue;
                                const cy = r.top + r.height / 2;
                                if (Math.abs(cy - rowY) > 54) continue;
                                const t = norm(el.innerText || el.textContent || '').replace(/\\s+/g, ' ');
                                if (t && t.length <= 240 && !capParts.includes(t)) {
                                    capParts.push(t);
                                }
                            }
                            const bestCaption = capParts.join(' | ').slice(0, 420);

                            // Tìm chính xác icon SVG của nút '...' (nút thứ 4 ngoài cùng bên phải ở cột Hành động, r.left > 1050)
                            const actionCandidates = [];
                            for (const svg of svgs) {
                                const r = svg.getBoundingClientRect();
                                if (r.left < 1050 || r.left > 1420 || r.width < 8 || r.width > 48 || r.height < 8 || r.height > 48) continue;
                                const cy = r.top + r.height / 2;
                                if (Math.abs(cy - rowY) > 36) continue;
                                const btn = svg.closest('button, [role="button"]') || svg.parentElement || svg;
                                const br = btn.getBoundingClientRect();
                                actionCandidates.push({
                                    x: br.left + br.width / 2,
                                    y: br.top + br.height / 2,
                                    left: br.left
                                });
                            }
                            actionCandidates.sort((a, b) => b.x - a.x);
                            if (actionCandidates.length > 0) {
                                rows.push({
                                    rowY: Math.round(rowY),
                                    caption: bestCaption,
                                    moreX: Math.round(actionCandidates[0].x * 10) / 10,
                                    moreY: Math.round(actionCandidates[0].y * 10) / 10,
                                    actionCount: actionCandidates.length
                                });
                            }
                        }
                        return rows;
                    }"""
                )

                logger.info(
                    f"[{ch_folder}] [Pass {pass_idx + 1}] Tìm thấy {len(rows_info or [])} hàng bài đăng trên tiktokstudio/content: {rows_info}"
                )
                if not rows_info:
                    break

                target_row = None
                for r in rows_info:
                    cap_low = (r.get("caption") or "").lower()
                    if kw_lower and any(k in cap_low for k in kw_lower):
                        target_row = r
                        break
                if target_row is None and delete_top_if_no_match and pass_idx == 0:
                    target_row = rows_info[0]

                if not target_row:
                    self._last_delete_debug.append({"pass": pass_idx, "rows": rows_info, "msg": "no_matching_row"})
                    logger.info(f"[{ch_folder}] Không còn bài đăng nào khớp từ khóa cần xóa ({match_keywords}).")
                    break

                mx, my = float(target_row["moreX"]), float(target_row["moreY"])
                logger.info(
                    f"[{ch_folder}] Đang mở menu '...' tại ({mx:.1f}, {my:.1f}) để xóa bài: '{target_row.get('caption')}'"
                )

                find_del_js = """() => {
                    const norm = (s) => (s || '').normalize('NFC').trim().toLowerCase();
                    const els = Array.from(document.querySelectorAll('*'));
                    const matches = [];
                    const popupDump = [];
                    for (const el of els) {
                        if (el.children.length > 4) continue;
                        const txt = norm(el.innerText || el.textContent || '');
                        if (!txt || txt.length > 35) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 12 || r.height < 10 || r.top < 80) continue;
                        if (r.left > 950 && popupDump.length < 25) {
                            popupDump.push({ t: txt, x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) });
                        }
                        if (
                            txt === 'xóa' || txt === 'xoá' ||
                            txt === 'xóa video' || txt === 'xoá video' ||
                            txt === 'xóa bài đăng' || txt === 'xoá bài đăng' ||
                            txt === 'delete' || txt === 'delete video' || txt === 'delete post' ||
                            ((txt.includes('xóa') || txt.includes('xoá') || txt.includes('delete')) && !txt.includes('tìm kiếm'))
                        ) {
                            if (r.width <= 320 && r.height <= 80) {
                                matches.push({
                                    text: txt,
                                    x: r.left + r.width / 2,
                                    y: r.top + r.height / 2,
                                    left: r.left,
                                    childrenCount: el.children.length
                                });
                            }
                        }
                    }
                    matches.sort((a, b) => (a.childrenCount - b.childrenCount) || (b.left - a.left));
                    return { match: matches[0] || null, popupDump: popupDump };
                }"""

                # Bước A: Click 1 lần chuẩn xác bằng OS click vào đúng tâm icon SVG '...'
                self._os_click_viewport(page, mx, my)
                time.sleep(1.4)
                res_del = page.evaluate(find_del_js)
                del_option = res_del.get("match") if isinstance(res_del, dict) else None

                # Bước B: Nếu chưa mở menu, thử hover + Playwright mouse click 1 lần
                if not del_option:
                    page.mouse.move(mx, my)
                    time.sleep(0.8)
                    res_del = page.evaluate(find_del_js)
                    del_option = res_del.get("match") if isinstance(res_del, dict) else None

                if not del_option:
                    page.mouse.click(mx, my)
                    time.sleep(1.3)
                    res_del = page.evaluate(find_del_js)
                    del_option = res_del.get("match") if isinstance(res_del, dict) else None

                self._last_delete_debug.append({
                    "pass": pass_idx,
                    "target_row": target_row,
                    "del_option": del_option,
                    "popup_dump": res_del.get("popupDump") if isinstance(res_del, dict) else [],
                })

                if not del_option:
                    logger.warning(f"[{ch_folder}] Không tìm thấy nút 'Xóa' trong menu tại ({mx:.1f}, {my:.1f}).")
                    break

                dx, dy = float(del_option["x"]), float(del_option["y"])
                logger.info(
                    f"[{ch_folder}] Đã thấy tùy chọn '{del_option['text']}' tại ({dx:.1f}, {dy:.1f}), đang bấm..."
                )
                find_confirm_js = """() => {
                    const norm = (s) => (s || '').normalize('NFC').trim().toLowerCase();
                    const labels = ['xóa', 'xoá', 'xóa video', 'xoá video', 'delete', 'xác nhận', 'confirm'];
                    const btns = Array.from(document.querySelectorAll('button, [role="button"], div, span'));
                    const candidates = [];
                    for (const b of btns) {
                        if (b.children.length > 3) continue;
                        const txt = norm(b.innerText || b.textContent || '');
                        if (!labels.includes(txt)) continue;
                        const r = b.getBoundingClientRect();
                        if (r.width < 36 || r.width > 280 || r.height < 22 || r.height > 75 || r.top < 160) continue;
                        // Ưu tiên nút trong hộp thoại Modal ở giữa màn hình (r.left < 1050), không trùng menu '...' bên phải
                        const isModalCenter = (r.left >= 250 && r.left <= 1050) ? 1 : 0;
                        const isButtonTag = (b.tagName === 'BUTTON' || b.getAttribute('role') === 'button') ? 1 : 0;
                        candidates.push({
                            text: txt,
                            x: r.left + r.width / 2,
                            y: r.top + r.height / 2,
                            left: r.left,
                            top: r.top,
                            isModalCenter: isModalCenter,
                            isButtonTag: isButtonTag
                        });
                    }
                    candidates.sort((a, b) => (b.isModalCenter - a.isModalCenter) || (b.isButtonTag - a.isButtonTag) || (b.top - a.top));
                    return candidates.find(c => c.isModalCenter === 1) || null;
                }"""

                self._os_click_viewport(page, dx, dy)
                time.sleep(1.8)
                confirm_btn = page.evaluate(find_confirm_js)
                if not confirm_btn:
                    page.mouse.click(dx, dy)
                    page.evaluate(
                        """({x, y}) => {
                            const el = document.elementFromPoint(x, y);
                            if (el) el.click();
                        }""",
                        {"x": dx, "y": dy},
                    )
                    time.sleep(1.6)
                    confirm_btn = page.evaluate(find_confirm_js)

                if confirm_btn:
                    cx, cy = float(confirm_btn["x"]), float(confirm_btn["y"])
                    logger.info(
                        f"[{ch_folder}] Đang bấm xác nhận XÓA ('{confirm_btn['text']}') trong modal tại ({cx:.1f}, {cy:.1f})..."
                    )
                    self._os_click_viewport(page, cx, cy)
                    page.mouse.click(cx, cy)
                    try:
                        page.evaluate(
                            """({x, y}) => {
                                const el = document.elementFromPoint(x, y);
                                if (el) {
                                    const btn = el.closest('button, [role="button"]') || el;
                                    btn.click();
                                }
                            }""",
                            {"x": cx, "y": cy},
                        )
                    except Exception:
                        pass
                    time.sleep(4.2)
                    deleted_captions.append(target_row.get("caption") or "deleted_post")
                    logger.info(f"[{ch_folder}] ĐÃ XÓA THÀNH CÔNG bài đăng lỗi: '{target_row.get('caption')}'!")
                else:
                    logger.warning(f"[{ch_folder}] Không thấy nút xác nhận Xóa trong modal.")
                    page.keyboard.press("Escape")
                    time.sleep(1.0)
                    break
        except Exception as exc:
            logger.warning(f"[{ch_folder}] Lỗi khi xóa bài đăng tại tiktokstudio/content: {exc}")

        return deleted_captions

    def delete_channel_posts_by_keywords(
        self,
        channel_arg: str,
        match_keywords: List[str],
        headless: bool = False,
    ) -> List[str]:
        """
        Mở trực tiếp trang `https://www.tiktok.com/tiktokstudio/content` của kênh `channel_arg`,
        xóa toàn bộ các bài đăng lỗi khớp `match_keywords` và đồng bộ ảnh chụp màn hình về `remote_telemetry/`.
        """
        from playwright.sync_api import sync_playwright

        profile = resolve_channel_profile(channel_arg)
        ch_folder = profile["folder_name"]
        user_data_dir = self.profiles_root / ch_folder
        if not user_data_dir.exists():
            logger.warning(f"Kênh [{ch_folder}] chưa có profile đăng nhập tại {user_data_dir}.")
            return []

        deleted: List[str] = []
        with sync_playwright() as p:
            context = self._launch_stealth_chrome_context(
                playwright_obj=p, user_data_dir=user_data_dir, headless=headless
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                deleted = self._delete_post_in_content_page(
                    page=page,
                    ch_folder=ch_folder,
                    match_keywords=match_keywords,
                    delete_top_if_no_match=False,
                )
                self._save_and_push_telemetry(
                    page=page,
                    event_type="upload",
                    ch_folder=ch_folder,
                    details={
                        "action": "delete_bad_posts",
                        "match_keywords": match_keywords,
                        "deleted_posts": deleted,
                        "delete_debug": getattr(self, "_last_delete_debug", []),
                        "final_url": page.url,
                    },
                )
                time.sleep(random.uniform(3.0, 5.0))
            finally:
                try:
                    context.close()
                except Exception:
                    pass
        return deleted

    def ensure_channel_posts_public(self, channel_arg: str, headless: bool = False) -> Dict[str, Any]:
        """
        Mở trang `https://www.tiktok.com/tiktokstudio/content` của một kênh (hoặc cả 4 kênh nếu `channel_arg='all'`)
        và tự động chuyển toàn bộ các video đang bị để `Chỉ mình tôi` / `Bạn bè` sang `Mọi người` (Công khai).
        """
        if str(channel_arg).strip().lower() == "all":
            summary: Dict[str, Any] = {}
            for profile in CHANNEL_PROFILES.values():
                ch_folder = profile["folder_name"]
                summary[ch_folder] = self.ensure_channel_posts_public(ch_folder, headless=headless)
            return summary

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài đặt `playwright`. Vui lòng chạy: `./venv/bin/pip install playwright && ./venv/bin/playwright install chromium`"
            ) from exc

        profile = resolve_channel_profile(channel_arg)
        ch_folder = profile["folder_name"]
        user_data_dir = self.profiles_root / ch_folder
        if not user_data_dir.exists():
            logger.warning(f"Kênh [{ch_folder}] chưa đăng nhập tại {user_data_dir}.")
            return {"channel": ch_folder, "switched_to_public": 0, "status": "not_logged_in"}

        with sync_playwright() as p:
            context = self._launch_stealth_chrome_context(
                playwright_obj=p, user_data_dir=user_data_dir, headless=headless
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                switched = self._ensure_posts_public_in_content_page(page, ch_folder)
                time.sleep(random.uniform(2.0, 3.5))
                return {"channel": ch_folder, "switched_to_public": switched, "status": "ok"}
            finally:
                try:
                    context.close()
                except Exception:
                    pass

    def login_channel_profile(self, channel_arg: str) -> Path:
        """
        Mở trực tiếp Google Chrome nguyên bản (100% Native Chrome, KHÔNG gắn Playwright CDP Debugger Pipe,
        KHÔNG có cờ AutomationControlled) với Profile riêng của kênh (`browser_profiles/<ten_kenh>`)
        để tạo tài khoản / đăng nhập TikTok không bao giờ bị chặn `tiktok.com/login/download-app`.
        """
        import subprocess

        profile = resolve_channel_profile(channel_arg)
        ch_folder = profile["folder_name"]
        user_data_dir = self.profiles_root / ch_folder
        user_data_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_chrome_profile_locks(user_data_dir)
        fp = self._get_or_create_channel_fingerprint(user_data_dir)

        logger.info(
            f"=== TẠO TÀI KHOẢN / ĐĂNG NHẬP TIKTOK [{ch_folder}] (100% NATIVE CHROME - KHÔNG GẮN CDP) ===\n"
            f"1. Cửa sổ Chrome thật 100% (không có dòng cảnh báo vàng, không bị TikTok báo 'Tiếp tục trên ứng dụng').\n"
            f"2. Hãy đăng nhập Gmail ở Tab 2 trước, sau đó ở Tab 1 bấm 'Tiếp tục với Google' (hoặc đăng ký bằng Email).\n"
            f"3. Sau khi tạo tài khoản & sửa hồ sơ xong, hãy ĐÓNG cửa sổ Chrome này (Cmd+Q hoặc bấm nút đỏ X) để lưu phiên."
        )

        chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        win_w = int(fp.get("window_width", 1366))
        win_h = int(fp.get("window_height", 860))
        win_x = int(fp.get("window_x", 30))
        win_y = int(fp.get("window_y", 30))

        cmd = [
            chrome_bin,
            f"--user-data-dir={user_data_dir}",
            "--use-mock-keychain",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={win_w},{win_h}",
            f"--window-position={win_x},{win_y}",
            "--lang=vi-VN",
            "https://www.tiktok.com/login/qrcode",
        ]
        proxy_raw = str(fp.get("proxy_server") or "").strip()
        if proxy_raw:
            proxy_cfg = self._parse_playwright_proxy(proxy_raw)
            if proxy_cfg and proxy_cfg.get("server"):
                cmd.insert(-2, f"--proxy-server={proxy_cfg['server']}")

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            saw_renderer = False
            empty_ticks = 0
            u_str = str(user_data_dir)
            while proc.poll() is None:
                time.sleep(1.5)
                try:
                    ps_out = subprocess.run(
                        ["ps", "-axo", "command"],
                        capture_output=True,
                        text=True,
                        timeout=4,
                        check=False,
                    ).stdout
                    has_tab_renderer = False
                    for line in ps_out.splitlines():
                        if u_str in line and "--type=renderer" in line and "--extension-process" not in line:
                            has_tab_renderer = True
                            break
                    if has_tab_renderer:
                        saw_renderer = True
                        empty_ticks = 0
                    elif saw_renderer:
                        empty_ticks += 1
                        if empty_ticks >= 2:
                            proc.terminate()
                            break
                except Exception:
                    pass
            proc.wait(timeout=10)
        except BaseException:
            try:
                proc.terminate()
            except Exception:
                pass

        # Sau khi đóng Native Chrome, trích xuất nhanh trạng thái phiên để đồng bộ sang máy Intel
        try:
            from playwright.sync_api import sync_playwright

            self._cleanup_stale_chrome_profile_locks(user_data_dir)
            with sync_playwright() as p:
                ctx = self._launch_stealth_chrome_context(
                    playwright_obj=p, user_data_dir=user_data_dir, headless=True
                )
                try:
                    st = ctx.storage_state()
                    if st and st.get("cookies"):
                        self._save_synced_channel_session(ch_folder, st)
                finally:
                    try:
                        ctx.close()
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug(f"[{ch_folder}] Lỗi phụ khi xuất phiên đồng bộ sau khi đóng Chrome: {exc}")

        logger.info(f"Đã lưu & mã hóa đồng bộ phiên đăng nhập TikTok cho kênh [{ch_folder}] tại: {user_data_dir}")
        return user_data_dir

    @staticmethod
    def _quick_video_fingerprint(file_path: Path) -> str:
        """Tính vân tay nhị phân nhanh (size + 64KB đầu/cuối) để phát hiện 2 file MP4 giống hệt nội dung dù khác tên."""
        import hashlib

        try:
            st = file_path.stat()
            size = st.st_size
            h = hashlib.sha256()
            h.update(str(size).encode("utf-8"))
            with open(file_path, "rb") as f:
                h.update(f.read(65536))
                if size > 131072:
                    f.seek(max(0, size - 65536))
                    h.update(f.read(65536))
            return f"{size}:{h.hexdigest()[:24]}"
        except Exception:
            return ""

    @staticmethod
    def _audio_story_fingerprint(file_path: Path) -> Tuple[float, str]:
        """
        Trích xuất thời lượng chính xác (giây) và vân tay năng lượng âm thanh (Audio Envelope Hash)
        ở đoạn giữa video (t=25s..55s) để phát hiện 2 video khác hình nền ASMR nhưng dùng chung 1 file ghi âm truyện!
        """
        import array
        import subprocess
        from .utils import get_media_duration

        dur = float(get_media_duration(file_path) or 0.0)
        try:
            start_sec = 25 if dur >= 60.0 else max(2, int(dur * 0.2))
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    str(start_sec),
                    "-t",
                    "25",
                    "-i",
                    str(file_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "1000",
                    "-f",
                    "s16le",
                    "-",
                ],
                capture_output=True,
                timeout=8,
                check=False,
            )
            raw = proc.stdout
            if not raw or len(raw) < 2000:
                return dur, ""
            samples = array.array("h", raw[: (len(raw) // 2) * 2])
            bucket_size = 500  # 0.5s mỗi bucket ở 1000Hz
            energies: List[int] = []
            for i in range(0, len(samples) - bucket_size, bucket_size):
                chunk = samples[i : i + bucket_size]
                avg_abs = sum(abs(x) for x in chunk) // bucket_size
                energies.append(avg_abs)
            if len(energies) < 8:
                return dur, ""
            mean_e = max(1, sum(energies) // len(energies))
            # Chuẩn hóa bit theo ngưỡng năng lượng trung bình (bất biến với thay đổi âm lượng hoặc pitch nhẹ)
            bits = "".join("1" if e >= mean_e else "0" for e in energies[:40])
            return dur, bits
        except Exception:
            return dur, ""

    def _record_uploaded_video_in_registry(
        self, ch_folder: str, video_path: Path, caption: str = ""
    ) -> None:
        """Ghi nhận ngay lập tức file/story_id đã bấm Đăng vào `uploaded_videos_registry.json` để chống đăng trùng tuyệt đối."""
        reg_path = self.project_root / "uploaded_videos_registry.json"
        data: Dict[str, Any] = {
            "posted_story_ids": [],
            "posted_filenames": [],
            "posted_fingerprints": [],
            "history": [],
        }
        if reg_path.exists():
            try:
                loaded = json.loads(reg_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data.update(loaded)
            except Exception:
                pass

        m_id = re.search(r"(?:tiktok|douyin)_story_(\d+)", video_path.stem, re.IGNORECASE)
        vid_id = m_id.group(1) if m_id else ""
        fp = self._quick_video_fingerprint(video_path) if video_path.exists() else ""

        sids = set(str(x) for x in (data.get("posted_story_ids") or []) if x)
        fns = set(str(x) for x in (data.get("posted_filenames") or []) if x)
        fps = set(str(x) for x in (data.get("posted_fingerprints") or []) if x)

        if vid_id:
            sids.add(vid_id)
        fns.add(video_path.name)
        if fp:
            fps.add(fp)

        data["posted_story_ids"] = sorted(sids)
        data["posted_filenames"] = sorted(fns)
        data["posted_fingerprints"] = sorted(fps)
        hist = list(data.get("history") or [])
        hist.insert(
            0,
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "channel": ch_folder,
                "filename": video_path.name,
                "story_id": vid_id,
                "fingerprint": fp,
                "caption": caption[:160],
            },
        )
        data["history"] = hist[:200]
        try:
            reg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _get_next_ready_video_for_channel(
        self, ch_folder: str
    ) -> Optional[Tuple[Path, str, str]]:
        """
        Lấy video chờ đăng tiếp theo trong `output/<ch_folder>/ready/` cùng `caption` (kịch bản ngắn tiếng Việt)
        và `hashtags` (Hot Search) từ `output/danh_sach_video.csv`.
        Tự động kiểm tra chống đăng trùng 3 lớp (story_id + filename + binary fingerprint) so với `posted/`,
        CSV, `uploaded_videos_registry.json` và lịch sử Telemetry trước khi chọn file.
        """
        try:
            self.crawler.auto_sync_posted_videos()
        except Exception:
            pass

        ready_dir = self.project_root / "output" / ch_folder / "ready"
        posted_dir = self.project_root / "output" / ch_folder / "posted"
        posted_dir.mkdir(parents=True, exist_ok=True)
        if not ready_dir.exists():
            return None

        posted_ids, posted_filenames = self.crawler._collect_all_known_posted_story_ids_and_files()
        posted_fingerprints: set[str] = set()
        posted_audio_bits: List[str] = []

        for p_mp4 in posted_dir.glob("*.mp4"):
            if p_mp4.is_file():
                fp = self._quick_video_fingerprint(p_mp4)
                if fp:
                    posted_fingerprints.add(fp)
                _, p_bits = self._audio_story_fingerprint(p_mp4)
                if p_bits:
                    posted_audio_bits.append(p_bits)

        reg_path = self.project_root / "uploaded_videos_registry.json"
        if reg_path.exists():
            try:
                reg_data = json.loads(reg_path.read_text(encoding="utf-8"))
                for fp in reg_data.get("posted_fingerprints") or []:
                    if fp:
                        posted_fingerprints.add(str(fp))
            except Exception:
                pass

        target_video: Optional[Path] = None
        for cand in sorted(f for f in ready_dir.glob("*.mp4") if f.is_file()):
            # Kênh audio_dem_khuyen chỉ đăng chuẩn motip Truyện Audio TikTok Việt Nam (nền ASMR/chữa lành + giọng kể chuyện), loại bỏ video Douyin lệch motip
            if ch_folder == "audio_dem_khuyen" and "douyin_story_" in cand.name.lower():
                cand.unlink(missing_ok=True)
                logger.info(
                    f"[{ch_folder}] [Motif Guard] Đã xóa video Douyin lệch motip ({cand.name}) khỏi ready/ để chỉ giữ chuẩn Truyện Audio TikTok Việt Nam."
                )
                continue

            m_cand = re.search(r"(?:tiktok|douyin)_story_(\d+)", cand.stem, re.IGNORECASE)
            cand_id = m_cand.group(1) if m_cand else ""
            cand_fp = self._quick_video_fingerprint(cand)
            cand_dur, cand_bits = self._audio_story_fingerprint(cand)

            is_dup_audio = False
            if cand_bits and posted_audio_bits:
                for p_b in posted_audio_bits:
                    if len(cand_bits) == len(p_b):
                        same_cnt = sum(1 for a, b in zip(cand_bits, p_b) if a == b)
                        if same_cnt / len(cand_bits) >= 0.88:
                            is_dup_audio = True
                            break

            if (
                cand.name in posted_filenames
                or (cand_id and cand_id in posted_ids)
                or (cand_fp and cand_fp in posted_fingerprints)
                or is_dup_audio
            ):
                dest_dup = posted_dir / cand.name
                if is_dup_audio:
                    cand.unlink(missing_ok=True)
                    logger.info(
                        f"[{ch_folder}] [Anti-Duplicate Audio Guard] Đã xóa video trùng nội dung âm thanh {cand.name} (thời lượng={cand_dur:.1f}s) khỏi ready/."
                    )
                    continue
                has_same_sid = bool(cand_id and any(cand_id in p.name for p in posted_dir.glob("*.mp4")))
                if has_same_sid and not dest_dup.exists():
                    cand.unlink(missing_ok=True)
                    logger.info(
                        f"[{ch_folder}] [Anti-Duplicate Guard] Đã xóa bản trùng {cand.name} (story_id={cand_id}) khỏi ready/."
                    )
                else:
                    if dest_dup.exists():
                        dest_dup.unlink()
                    shutil.move(str(cand), str(dest_dup))
                    logger.info(
                        f"[{ch_folder}] [Anti-Duplicate Guard] Bỏ qua {cand.name} (story_id={cand_id}) vì ĐÃ ĐĂNG TRƯỚC ĐÓ -> chuyển sang posted/."
                    )
                continue

            target_video = cand
            break

        if not target_video:
            return None

        caption = ""
        hashtags = ""
        m_id = re.search(r"(?:tiktok|douyin)_story_(\d+)", target_video.stem, re.IGNORECASE)
        vid_id = m_id.group(1) if m_id else ""

        csv_path = self.project_root / "output" / "danh_sach_video.csv"
        if csv_path.exists():
            try:
                with open(csv_path, mode="r", newline="", encoding="utf-8") as rf:
                    for row in csv.DictReader(rf):
                        fn = (row.get("filename") or "").strip()
                        s_url = (row.get("source_url") or "").strip()
                        if Path(fn).name == target_video.name or (
                            vid_id and (vid_id in fn or vid_id in s_url)
                        ):
                            caption = (row.get("caption") or "").strip()
                            hashtags = (row.get("hashtags") or "").strip()
                            break
            except Exception:
                pass

        if self.crawler.is_raw_filename_caption(caption, target_video.stem) or not hashtags.strip():
            rec_cap, rec_tags, _ = self.crawler.recover_contextual_caption_and_hashtags(
                ch_folder, target_video.name
            )
            if self.crawler.is_raw_filename_caption(caption, target_video.stem):
                caption = rec_cap
            if not hashtags.strip():
                hashtags = rec_tags
            try:
                self.crawler._upgrade_master_csv_schema()
            except Exception:
                pass

        return target_video, caption, hashtags

    def _was_channel_uploaded_recently(
        self, ch_folder: str, min_interval_minutes: float = 180.0
    ) -> Optional[float]:
        """
        Kiểm tra 3 nguồn độc lập (`posted_schedule_history.json`, `uploaded_videos_registry.json`,
        `remote_telemetry/intel_status_report.json`) xem kênh `ch_folder` có vừa đăng bài trong vòng
        `min_interval_minutes` phút (mặc định 180 phút = 3 tiếng) gần đây hay không.
        Trả về số phút đã trôi qua nếu vừa đăng gần đây, hoặc `None` nếu đã đủ giãn cách an toàn.
        """
        now_dt = datetime.now()
        timestamps_to_check: List[str] = []

        # 1. Kiểm tra posted_schedule_history.json
        try:
            hist = self._load_post_history()
            for k, ts_val in hist.items():
                if ch_folder in str(k) and "filter24h" not in str(k) and isinstance(ts_val, str):
                    timestamps_to_check.append(ts_val.strip())
        except Exception:
            pass

        # 2. Kiểm tra uploaded_videos_registry.json
        try:
            reg_path = self.project_root / "uploaded_videos_registry.json"
            if reg_path.exists():
                reg_data = json.loads(reg_path.read_text(encoding="utf-8"))
                for item in reg_data.get("history") or []:
                    if isinstance(item, dict) and item.get("channel") == ch_folder and item.get("timestamp"):
                        timestamps_to_check.append(str(item["timestamp"]).strip())
        except Exception:
            pass

        # 3. Kiểm tra remote_telemetry/intel_status_report.json
        try:
            rep_path = self.project_root / "remote_telemetry" / "intel_status_report.json"
            if rep_path.exists():
                rep_data = json.loads(rep_path.read_text(encoding="utf-8"))
                for ev in rep_data.get("recent_events") or []:
                    if (
                        isinstance(ev, dict)
                        and ev.get("event_type") == "upload"
                        and ev.get("channel") == ch_folder
                        and ev.get("timestamp")
                    ):
                        timestamps_to_check.append(str(ev["timestamp"]).strip())
        except Exception:
            pass

        min_elapsed: Optional[float] = None
        for ts_str in timestamps_to_check:
            try:
                dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
                diff_min = (now_dt - dt).total_seconds() / 60.0
                if 0.0 <= diff_min < min_interval_minutes:
                    if min_elapsed is None or diff_min < min_elapsed:
                        min_elapsed = diff_min
            except Exception:
                pass
        return min_elapsed

    def upload_one_channel_video(
        self,
        channel_arg: str,
        headless: bool = False,
        force: bool = False,
        delete_match_keywords: Optional[List[str]] = None,
        delete_top_before_upload: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Tự động đăng 1 video từ `output/<ten_kenh>/ready/` lên kênh TikTok tương ứng với đầy đủ 5 bước:
        1. Mở Chrome thật với profile riêng `browser_profiles/<ten_kenh>/` + Stealth JS + Human Warm-up.
        2. Vào trang `tiktokstudio/upload?from=webapp&tab=video`, nạp file video.
        3. Xóa sạch tên file mặc định trong ô `Mô tả`, gõ Kịch bản ngắn + chọn từng Hashtag Hot Search.
        4. Tự động cấu hình mục `Cài đặt` & `Kiểm tra`:
           - `Thời điểm đăng`: `Bây giờ`
           - `Người có thể nhìn thấy bài đăng này`: `Mọi người` (Công khai)
           - `Nội dung tải lên chất lượng cao`: `ON`
           - `Cho phép người dùng`: `Bình luận` (`ON`)
           - `Khai báo nội dung bài đăng` & `Nội dung do AI tạo`: `OFF`
           - `Kiểm tra bản quyền nhạc` & `Kiểm tra nội dung nhanh`: `ON`
        5. Đợi tải video đạt 100%, bấm chính xác nút đỏ `Đăng` (`post_video_button`), xác nhận bài đăng thành công
           rồi chuyển sang trang `tiktokstudio/content` (mục `Bài đăng`) để đảm bảo `Quyền riêng tư` là `Mọi người` (Công khai).
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài đặt `playwright`. Vui lòng chạy: `./venv/bin/pip install playwright && ./venv/bin/playwright install chromium`"
            ) from exc

        profile = resolve_channel_profile(channel_arg)
        ch_folder = profile["folder_name"]

        if not force:
            recent_min = self._was_channel_uploaded_recently(ch_folder, min_interval_minutes=180.0)
            if recent_min is not None:
                logger.info(
                    f"[{ch_folder}] [Anti-Double-Post Guard] Kênh vừa đăng bài cách đây {recent_min:.1f} phút (< 180 phút). "
                    "Chặn tuyệt đối đăng 2 video liên tiếp!"
                )
                return None

        user_data_dir = self.profiles_root / ch_folder
        synced_sess = self.project_root / "remote_telemetry" / "sessions" / f"{ch_folder}.bin"
        if not user_data_dir.exists() and synced_sess.exists():
            user_data_dir.mkdir(parents=True, exist_ok=True)
        if not user_data_dir.exists():
            logger.warning(
                f"Kênh [{ch_folder}] chưa có phiên đăng nhập tại {user_data_dir}. "
                f"Hãy chạy: `./venv/bin/python main.py --login-channel {ch_folder}` trước."
            )
            return None

        next_item = self._get_next_ready_video_for_channel(ch_folder)
        if not next_item and force:
            logger.info(
                f"[{ch_folder}] Kho ready/ đã lọc hết các bản trùng nội dung -> Đang tự động cào ngay 1 video truyện mới hoàn toàn cho [{ch_folder}]..."
            )
            try:
                from .main import run_crawl_story_mode

                run_crawl_story_mode(channel=ch_folder, limit=1)
                next_item = self._get_next_ready_video_for_channel(ch_folder)
            except Exception as cr_exc:
                logger.warning(f"[{ch_folder}] Lỗi phụ khi cào bổ sung khẩn cấp: {cr_exc}")
        if not next_item:
            logger.info(f"Kênh [{ch_folder}] hiện không có video nào trong ready/ để đăng.")
            return None

        video_path, caption, hashtags = next_item
        cfg = self.ensure_schedule_config()
        do_warmup = bool(cfg.get("warmup_before_upload", True))

        logger.info(
            f"=== BẮT ĐẦU TỰ ĐỘNG ĐĂNG TIKTOK (FULL SETTING + CÔNG KHAI) KÊNH [{ch_folder}] ===\n"
            f"  + File: {video_path.name}\n"
            f"  + Kịch bản ngắn: {caption}\n"
            f"  + Hashtags Hot Search: {hashtags}"
        )

        with sync_playwright() as p:
            context = self._launch_stealth_chrome_context(
                playwright_obj=p, user_data_dir=user_data_dir, headless=headless
            )

            try:
                page = context.pages[0] if context.pages else context.new_page()

                # Bước 1: Human Warm-up lướt dạo trang chủ TikTok trước khi đăng
                if do_warmup:
                    self._simulate_human_warmup(page, ch_folder)

                deleted_posts: List[str] = []
                if delete_match_keywords or delete_top_before_upload:
                    deleted_posts = self._delete_post_in_content_page(
                        page=page,
                        ch_folder=ch_folder,
                        match_keywords=delete_match_keywords,
                        delete_top_if_no_match=delete_top_before_upload,
                    )

                # Bước 2: Truy cập TikTok Studio Upload đúng URL giao diện WebApp (có retry chống lỗi ngắt mạng tạm thời)
                for _nav_attempt in range(3):
                    try:
                        page.goto(
                            "https://www.tiktok.com/tiktokstudio/upload?from=webapp&tab=video",
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                        if "chromewebdata" not in (page.url or "").lower():
                            break
                    except Exception as _nav_err:
                        if _nav_attempt == 2:
                            raise _nav_err
                    time.sleep(3.0)
                time.sleep(random.uniform(4.5, 7.0))

                if "login" in page.url.lower():
                    raise RuntimeError(
                        f"Phiên đăng nhập TikTok của kênh [{ch_folder}] đã hết hạn. "
                        f"Vui lòng chạy lại `./venv/bin/python main.py --login-channel {ch_folder}`."
                    )

                # Bước 3: Chọn file video đưa vào input[type="file"]
                file_input = page.locator('input[type="file"]').first
                file_input.wait_for(state="attached", timeout=30000)
                file_input.set_input_files(str(video_path))
                logger.info(
                    f"[{ch_folder}] Đã nạp file video vào TikTok Studio. Đang chờ tải lên 100% và tự động đóng các hộp thoại hướng dẫn ('Đã hiểu')..."
                )

                # Bước 4: Đợi video tải lên đạt 100% TRƯỚC, kiểm tra đúng nút đỏ `post_video_button` (không nhầm với 'Bài đăng' ở menu trái!)
                ready_to_post = False
                for _ in range(120):
                    time.sleep(2)
                    self._dismiss_all_studio_popovers(page, ch_folder)
                    try:
                        if not self._is_video_upload_100_percent_done(page):
                            continue
                        btn_info = self._find_exact_post_button(page, scroll_into_view=False)
                        if btn_info and btn_info.get("found") and btn_info.get("enabled"):
                            ready_to_post = True
                            break
                    except Exception:
                        pass

                if not ready_to_post:
                    raise RuntimeError(
                        f"[{ch_folder}] Quá thời gian chờ tải video lên TikTok Studio (240s)."
                    )

                # Đợi thêm 1.5s sau khi tải xong 100% để bắt trọn mọi popup 'Đã hiểu' xuất hiện muộn
                time.sleep(1.5)
                self._dismiss_all_studio_popovers(page, ch_folder)

                # Bước 5: Xóa sạch tên file mặc định (20261025_...) trong ô Mô tả & gõ Kịch bản ngắn + Hashtags Hot Search
                self._clear_and_type_caption_hashtags(
                    page=page,
                    ch_folder=ch_folder,
                    caption=caption,
                    hashtags_str=hashtags,
                    video_stem=video_path.stem,
                )

                # Bước 6: Tự động nhận diện AIGC cho từng video và cấu hình toàn bộ mục Cài đặt & Kiểm tra
                self._dismiss_all_studio_popovers(page, ch_folder)
                need_aigc_label = self.detect_video_aigc_disclosure_needed(
                    video_path=video_path,
                    caption=caption,
                    hashtags=hashtags,
                    ch_folder=ch_folder,
                )
                self._configure_upload_settings_public(page, ch_folder, aigc_on=need_aigc_label)
                self._dismiss_all_studio_popovers(page, ch_folder)

                # Cuộn khung form xuống cuối trang tới đúng nút đỏ `Đăng` (post_video_button) và click
                self._scroll_upload_form(page, 900)
                time.sleep(random.uniform(1.0, 1.5))
                self._dismiss_all_studio_popovers(page, ch_folder)

                # Chụp ảnh màn hình ngay trước khi bấm Đăng để có thể kiểm tra từ xa
                try:
                    telem_dir = self.project_root / "remote_telemetry"
                    telem_dir.mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(telem_dir / f"latest_upload_{ch_folder}_before_post.jpg"),
                        type="jpeg",
                        quality=60,
                    )
                except Exception:
                    pass

                btn_info = self._find_exact_post_button(page, scroll_into_view=True)
                time.sleep(0.8)
                btn_info = self._find_exact_post_button(page, scroll_into_view=False) or btn_info
                if btn_info and btn_info.get("found"):
                    click_x = float(btn_info["x"])
                    click_y = float(btn_info["y"])
                    logger.info(
                        f"[{ch_folder}] Đang bấm nút đỏ ĐĂNG ('{btn_info.get('text')}', data-e2e='{btn_info.get('dataE2e')}') tại ({click_x:.1f}, {click_y:.1f})..."
                    )
                    self._os_click_viewport(page, click_x, click_y)
                else:
                    exact_loc = page.locator('button[data-e2e="post_video_button"]').first
                    exact_loc.scroll_into_view_if_needed()
                    exact_loc.click()

                logger.info(
                    f"[{ch_folder}] Đã bấm nút ĐĂNG! Đang chờ TikTok Studio xác nhận đăng bài hoàn tất..."
                )

                # Đợi đến khi TikTok thực sự gửi bài đăng thành công (chuyển sang tiktokstudio/content hoặc hiện thông báo đã đăng)
                post_confirmed = False
                for wait_idx in range(30):
                    time.sleep(1.5)
                    self._dismiss_all_studio_popovers(page, ch_folder)
                    self._click_confirm_modal_if_present(page)

                    if "tiktokstudio/content" in page.url:
                        post_confirmed = True
                        break

                    # Kiểm tra thông báo đăng thành công trên màn hình
                    try:
                        is_done_toast = page.evaluate(
                            """() => {
                                const txt = document.body ? (document.body.innerText || '') : '';
                                return /Video đã được đăng|Đã đăng video|Quản lý bài đăng của bạn|Đăng thêm video khác|Your video is being uploaded|Video published/i.test(txt);
                            }"""
                        )
                        if is_done_toast:
                            post_confirmed = True
                            break
                    except Exception:
                        pass

                    # Nếu sau 4.5s nút đỏ Đăng vẫn còn nguyên và đang enabled (chưa ăn click), bấm trực tiếp lần nữa
                    if wait_idx in (3, 7, 12):
                        still_btn = self._find_exact_post_button(page, scroll_into_view=True)
                        if still_btn and still_btn.get("found") and still_btn.get("enabled"):
                            logger.info(
                                f"[{ch_folder}] Nút ĐĂNG vẫn đang chờ trên màn hình (lần thử {wait_idx}), thực hiện click trực tiếp vào post_video_button..."
                            )
                            try:
                                page.evaluate(
                                    """() => {
                                        const b = document.querySelector('button[data-e2e="post_video_button"], [data-e2e="post_video_button"]');
                                        if (b) b.click();
                                    }"""
                                )
                            except Exception:
                                pass
                            self._os_click_viewport(page, float(still_btn["x"]), float(still_btn["y"]))

                if post_confirmed:
                    logger.info(f"[{ch_folder}] TikTok Studio đã xác nhận ĐĂNG VIDEO THÀNH CÔNG!")
                else:
                    logger.warning(
                        f"[{ch_folder}] Đã hết thời gian chờ phản hồi sau khi bấm Đăng, tiến hành kiểm tra tại trang Quản lý Bài đăng..."
                    )

                # Ghi nhận vào sổ cái chống đăng trùng & chuyển ngay sang posted/ trước khi làm các bước phụ
                self._record_uploaded_video_in_registry(ch_folder, video_path, caption)
                marked = self.crawler.mark_video_posted(str(video_path))

                # Bước 7 (Ảnh chụp màn hình 5): Tại trang tiktok.com/tiktokstudio/content,
                # kiểm tra cột "Quyền riêng tư" và chuyển mọi bài đang ở "Chỉ mình tôi" sang "Mọi người" (Công khai)!
                switched_public = self._ensure_posts_public_in_content_page(page, ch_folder)
                logger.info(
                    f"[{ch_folder}] Hoàn tất kiểm tra trang Bài đăng (đã chuyển {switched_public} bài từ 'Chỉ mình tôi' -> 'Mọi người')."
                )

                # Chụp ảnh màn hình kết quả sau khi đăng & đồng bộ báo cáo từ xa lên GitHub
                self._save_and_push_telemetry(
                    page=page,
                    event_type="upload",
                    ch_folder=ch_folder,
                    details={
                        "video_file": video_path.name,
                        "caption": caption,
                        "hashtags": hashtags,
                        "deleted_posts": deleted_posts,
                        "post_confirmed": post_confirmed,
                        "switched_public_count": switched_public,
                        "final_url": page.url,
                    },
                )

                dwell_sec = random.randint(8, 15)
                time.sleep(dwell_sec)

            except Exception as up_exc:
                try:
                    self._save_and_push_telemetry(
                        page=page,
                        event_type="upload_error",
                        ch_folder=ch_folder,
                        details={"video_file": video_path.name, "error": str(up_exc)},
                    )
                except Exception:
                    pass
                raise
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        logger.info(f"[{ch_folder}] HOÀN TẤT ĐĂNG TỰ ĐỘNG CÔNG KHAI & ĐỒNG BỘ POSTED: {marked['filename']}")
        return marked

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

                    subprocess.run(
                        ["git", "add", "remote_telemetry"],
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

    def run_24h_filter_for_channel(
        self,
        channel_arg: str,
        video_count: int = 10,
        headless: bool = False,
    ) -> Dict[str, Any]:
        """
        Thực thi tự động "Bộ lọc 24 giờ" (Xem hết video -> Thả tim -> Sao chép liên kết ->
        Thêm vào yêu thích -> Follow chủ kênh -> Bình luận có ý nghĩa xác nhận đã follow)
        cho 1 kênh hoặc toàn bộ 4 kênh (`all`).
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài đặt `playwright`. Vui lòng chạy: `./venv/bin/pip install playwright && ./venv/bin/playwright install chromium`"
            ) from exc

        if channel_arg.strip().lower() == "all":
            target_channels = [p["folder_name"] for p in CHANNEL_PROFILES.values()]
        else:
            target_channels = [resolve_channel_profile(channel_arg)["folder_name"]]

        summary: Dict[str, Any] = {}
        for ch_folder in target_channels:
            try:
                import importlib
                from . import growth_24h_filter as _g24_mod

                importlib.reload(_g24_mod)
                self.filter_24h = _g24_mod.TikTok24hFilterEngine(
                    project_root=self.project_root,
                    desktop=self.desktop,
                    human_move_fn=self._human_move_mouse,
                    os_click_fn=self._os_click_viewport,
                )
                in_cd, cd_reason = self.filter_24h.is_in_cooldown(ch_folder)
                if in_cd:
                    logger.info(
                        f"[{ch_folder}] [Dòng 2 - Chế Độ Xả Phạt Tự Động] Bỏ qua mở trình duyệt kênh [{ch_folder}] ({cd_reason}) -> "
                        "Sẽ tự động mở lại ngay khi hết thời gian xả phạt!"
                    )
                    summary[ch_folder] = {
                        "status": "COOLDOWN",
                        "cooldown_reason": cd_reason,
                        "interacted_count": 0,
                        "records": [],
                    }
                    continue
            except Exception:
                pass

            user_data_dir = self.profiles_root / ch_folder
            if not user_data_dir.exists():
                logger.info(
                    f"Kênh [{ch_folder}] chưa có thư mục profile tại {user_data_dir}, đang khởi tạo profile mới..."
                )
                user_data_dir.mkdir(parents=True, exist_ok=True)

            logger.info(
                f"=== BẮT ĐẦU CHẠY TỰ ĐỘNG 'BỘ LỌC 24 GIỜ' CHO KÊNH [{ch_folder}] ({video_count} video mục tiêu) ==="
            )
            with sync_playwright() as p:
                context = self._launch_stealth_chrome_context(
                    playwright_obj=p, user_data_dir=user_data_dir, headless=headless
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    records = self.filter_24h.run_session_on_page(
                        page=page, ch_folder=ch_folder, video_count=video_count
                    )
                    summary[ch_folder] = {
                        "status": "OK",
                        "interacted_count": len(records),
                        "records": records,
                    }
                    self._save_and_push_telemetry(
                        page=page,
                        event_type="filter24h",
                        ch_folder=ch_folder,
                        details={
                            "interacted_count": len(records),
                            "own_profile_stats": getattr(self.filter_24h, "last_own_profile_stats", {}),
                            "records": records,
                        },
                    )
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass

        return summary

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
        """
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
                    for single_ch in target_channels:
                        ch_trig_key = f"remote_post_{p_trig_id}_{single_ch}"
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
                                )
                                if res:
                                    posted_results.append(res)
                        except Exception as exc:
                            logger.error(
                                f"[Dòng 1 - Đăng Video] Lỗi khi đăng ngay ({p_trig_id}) kênh [{single_ch}]: {exc}"
                            )
                        finally:
                            self._release_browser_process_lock(lock_fh)
                    history = self._load_post_history()
                    history[p_trig_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._save_post_history(history)

        active_ch_set = set(cfg.get("active_channels") or ["video_AI_hay", "baby_AI", "audio_dem_khuyen", "tu_chan_gioi"])
        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
            if ch_folder not in active_ch_set:
                continue
            slots = ch_times_map.get(ch_folder) or default_times
            for slot in slots:
                history = self._load_post_history()
                slot_key = f"{today_str}_{ch_folder}_{slot}"
                if slot_key in history:
                    continue
                try:
                    sh, sm = map(int, slot.split(":"))
                except Exception:
                    continue
                jitter = self._compute_daily_jitter_minutes(today_str, ch_folder, slot, max_jitter)
                effective_slot_min = max(0, min(23 * 60 + 59, sh * 60 + sm + jitter))

                # Kích hoạt nếu thời gian hiện tại nằm trong khoảng [effective_slot_min, effective_slot_min + 180 phút]
                if 0 <= (now_minutes - effective_slot_min) <= 180:
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
                            posted_results.append(res)
                        else:
                            # Nếu kho ready/ trống chưa có video để đăng, trả lại slot_key để thử lại khi có video mới
                            history = self._load_post_history()
                            history.pop(slot_key, None)
                            history.pop(f"last_upload_ts_{ch_folder}", None)
                            self._save_post_history(history)
                    except Exception as exc:
                        logger.error(f"[Dòng 1 - Lịch Đăng Video] Lỗi tự động đăng kênh [{ch_folder}] khung {slot}: {exc}")
                    finally:
                        self._release_browser_process_lock(lock_fh)

        return posted_results

    def _is_stream1_upload_process_running(self) -> bool:
        """Kiểm tra xem tiến trình Dòng 1 (`--stream-upload` hoặc `--continuous`) có đang sống trên máy hay không."""
        import os as _os
        import subprocess

        try:
            my_pid = _os.getpid()
            out = subprocess.run(
                ["ps", "-axo", "pid,command"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
            for line in out.splitlines():
                s = line.strip()
                if not s or "main.py" not in s:
                    continue
                if "--stream-upload" in s or "--continuous" in s:
                    parts = s.split(None, 1)
                    if parts and parts[0].isdigit() and int(parts[0]) != my_pid:
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
        if self._is_stream1_upload_process_running():
            return

        proj_str = str(self.project_root)
        logger.info(
            "[Auto-Revive Watchdog] Phát hiện Cửa sổ 1 (--stream-upload) đang dừng trên máy Intel -> "
            "Đang tự động khởi động lại Cửa sổ 1 từ xa..."
        )

        # 1. Thử gõ lệnh trực tiếp vào cửa sổ Terminal đang rảnh trên màn hình máy Intel qua AppleScript
        apple_script = f'''
        tell application "Terminal"
            repeat with w in windows
                repeat with t in tabs of w
                    if not busy of t then
                        do script "cd '{proj_str}' && ./venv/bin/python main.py --stream-upload" in t
                        return "REVIVED_IN_TAB"
                    end if
                end repeat
            end repeat
        end tell
        '''
        try:
            subprocess.run(
                ["osascript", "-e", apple_script],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
            time.sleep(2.5)
        except Exception:
            pass

        # 2. Nếu cửa sổ Terminal chưa nhận lệnh (hoặc chưa cấp quyền Automation cho Terminal), chạy ngầm Popen trực tiếp
        if not self._is_stream1_upload_process_running():
            try:
                py_bin = self.project_root / "venv" / "bin" / "python"
                exe_str = str(py_bin) if py_bin.exists() else _sys.executable
                log_file = open(self.project_root / "stream1_upload_revived.log", "a", encoding="utf-8")
                subprocess.Popen(
                    [exe_str, str(self.project_root / "main.py"), "--stream-upload"],
                    cwd=proj_str,
                    stdout=log_file,
                    stderr=log_file,
                    start_new_session=True,
                )
                time.sleep(1.5)
                logger.info("[Auto-Revive Watchdog] Đã tự động bật lại tiến trình Dòng 1 (--stream-upload) thành công!")
            except Exception as exc:
                logger.debug(f"[Auto-Revive Watchdog] Lỗi phụ khi Popen Dòng 1: {exc}")

        # 3. Nếu vì lý do gì đó vẫn chưa thấy tiến trình riêng, Cửa sổ 2 tự đảm nhận kiểm tra lịch đăng Dòng 1 luôn
        if not self._is_stream1_upload_process_running():
            try:
                self.check_and_run_stream1_upload_schedule()
            except Exception:
                pass

    def check_and_run_stream2_filter24h_schedule(self) -> Dict[str, Any]:
        """
        [DÒNG HOẠT ĐỘNG 2 — MỞ 4 KÊNH CHẠY BỘ LỌC 24 GIỜ (7 LẦN/NGÀY × 10 VIDEO/KÊNH)]:
        Tự động mở lần lượt 4 kênh theo 7 khung giờ mỗi ngày (`channel_filter_24h_times`),
        mỗi kênh tương tác `filter_24h_videos_per_session` (mặc định 10 video).
        Nếu Cửa sổ 1 (`--stream-upload`) bị dừng khi vắng nhà, tự động hồi sinh Cửa sổ 1 từ xa.
        """
        try:
            self._auto_revive_stream1_upload_if_dead()
        except Exception:
            pass

        cfg = self.ensure_schedule_config()
        filter_enabled = bool(cfg.get("filter_24h_enabled", True))
        filter_count = max(1, min(int(cfg.get("filter_24h_videos_per_session", 3)), 3))
        filter_times_map: Dict[str, List[str]] = cfg.get("channel_filter_24h_times") or {}
        max_jitter = int(cfg.get("random_jitter_minutes", 9))
        headless = bool(cfg.get("headless", False))

        history = self._load_post_history()
        filter_results: Dict[str, Any] = {}

        # 0a-bridge. Nếu có lệnh remote_trigger_post_now mới từ máy Silicon chưa xử lý, ưu tiên chạy ngay
        remote_post_chk = cfg.get("remote_trigger_post_now")
        if isinstance(remote_post_chk, dict) and remote_post_chk.get("trigger_id"):
            p_tid = str(remote_post_chk["trigger_id"]).strip()
            if p_tid and f"remote_post_{p_tid}" not in history:
                try:
                    self.check_and_run_stream1_upload_schedule()
                    history = self._load_post_history()
                except Exception:
                    pass

        # 0b. Kiểm tra lệnh kích hoạt chạy thử Bộ lọc 24h từ xa (Remote Trigger)
        remote_trig = cfg.get("remote_trigger_filter_24h")
        if isinstance(remote_trig, dict) and remote_trig.get("trigger_id"):
            import platform as _plat

            target_arch = str(remote_trig.get("target_arch") or "").strip()
            if not target_arch or _plat.machine() == target_arch:
                trig_id = str(remote_trig["trigger_id"]).strip()
                trig_key = f"remote_trig_{trig_id}"
                if trig_id and trig_key not in history:
                    lock_fh = self._acquire_browser_process_lock()
                    try:
                        history = self._load_post_history()
                        if trig_key not in history:
                            trig_ch = str(remote_trig.get("channel") or "all").strip()
                            trig_cnt = int(remote_trig.get("video_count") or filter_count)
                            history[trig_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self._save_post_history(history)
                            logger.info(
                                f"[Dòng 2 - Bộ lọc 24h] Nhận lệnh chạy ngay từ máy Silicon ({trig_id}) -> "
                                f"Kênh [{trig_ch}], số lượng {trig_cnt} video/kênh!"
                            )
                            res = self.run_24h_filter_for_channel(trig_ch, video_count=trig_cnt, headless=headless)
                            filter_results[trig_key] = res
                    except Exception as exc:
                        logger.error(f"[Dòng 2 - Bộ lọc 24h] Lỗi khi chạy Remote Trigger ({trig_id}): {exc}")
                    finally:
                        self._release_browser_process_lock(lock_fh)

        if not filter_enabled:
            return filter_results

        # 0c. Chế độ Xả Phạt Tự Động 72 giờ (Auto-Cooldown & Auto-Resume sau 3 ngày):
        cooldown_until_str = str(cfg.get("filter_24h_cooldown_until") or "").strip()
        if cooldown_until_str:
            try:
                cd_dt = datetime.strptime(cooldown_until_str, "%Y-%m-%d %H:%M:%S")
                now_dt = datetime.now()
                if now_dt < cd_dt:
                    last_log = getattr(self, "_last_cooldown_log_ts", 0.0)
                    if time.time() - last_log >= 1800.0:
                        rem_hours = (cd_dt - now_dt).total_seconds() / 3600.0
                        logger.info(
                            f"[Dòng 2 - Chế Độ Xả Phạt 72h] Đang tạm nghỉ Bộ lọc 24h đến {cooldown_until_str} "
                            f"(còn {rem_hours:.1f} giờ) -> Sẽ TỰ ĐỘNG MỞ LẠI ngay khi đủ 3 ngày!"
                        )
                        self._last_cooldown_log_ts = time.time()
                    return filter_results
                elif not getattr(self, "_cooldown_resume_logged", False):
                    logger.info(
                        f"[Dòng 2 - Tự Động Mở Lại Sau 3 Ngày] Đã vượt qua mốc xả phạt {cooldown_until_str} -> "
                        "Tự động kích hoạt lại Bộ lọc 24 giờ với nhịp độ an toàn (3 lần/ngày × 3 video)!"
                    )
                    self._cooldown_resume_logged = True
            except Exception:
                pass

        default_3_slots = ["09:30", "15:00", "21:00"]
        active_ch_set = set(
            cfg.get("active_filter_24h_channels")
            or cfg.get("active_channels")
            or ["video_AI_hay", "baby_AI", "audio_dem_khuyen", "tu_chan_gioi"]
        )
        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
            if ch_folder not in active_ch_set:
                continue
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            now_minutes = now.hour * 60 + now.minute
            history = self._load_post_history()

            f_slots = filter_times_map.get(ch_folder) or default_3_slots
            for round_idx, f_slot in enumerate(f_slots, start=1):
                f_key = f"{today_str}_filter24h_{ch_folder}_{f_slot}"
                if f_key in history:
                    continue
                try:
                    fh, fm = map(int, f_slot.split(":"))
                except Exception:
                    continue
                f_jitter = self._compute_daily_jitter_minutes(today_str, f"f24_{ch_folder}", f_slot, max_jitter)
                eff_f_min = max(0, min(23 * 60 + 59, fh * 60 + fm + f_jitter))

                # Cửa sổ kích hoạt 120 phút kèm giãn cách IP tối thiểu 15 phút giữa 2 kênh liên tiếp trên cùng máy
                if 0 <= (now_minutes - eff_f_min) <= 120:
                    last_any_ts = str(history.get("last_any_filter24h_ts") or "").strip()
                    if last_any_ts:
                        try:
                            last_dt = datetime.strptime(last_any_ts, "%Y-%m-%d %H:%M:%S")
                            gap_min = (now - last_dt).total_seconds() / 60.0
                            if 0 <= gap_min < 15.0:
                                continue
                        except Exception:
                            pass

                    lock_fh = self._acquire_browser_process_lock()
                    try:
                        history = self._load_post_history()
                        if f_key in history:
                            continue
                        now_claim_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        history[f_key] = now_claim_str
                        history["last_any_filter24h_ts"] = now_claim_str
                        self._save_post_history(history)
                        eff_hh, eff_mm = eff_f_min // 60, eff_f_min % 60
                        logger.info(
                            f"[Dòng 2 - Bộ lọc 24h (Lần {round_idx}/{len(f_slots)} trong ngày)] "
                            f"Mở kênh [{ch_folder}] lúc {eff_hh:02d}:{eff_mm:02d} (khung {f_slot}) -> Chạy tối đa {filter_count} video (max 1 Follow)!"
                        )
                        res = self.run_24h_filter_for_channel(
                            ch_folder, video_count=filter_count, headless=headless
                        )
                        filter_results[f_key] = res
                        history = self._load_post_history()
                        history["last_any_filter24h_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self._save_post_history(history)
                        logger.info(
                            f"[Dòng 2 - Bộ lọc 24h] Đã hoàn tất phiên [{ch_folder}] (khung {f_slot}) -> "
                            "Nghỉ giãn cách IP tối thiểu 15 phút trước khi mở kênh kế tiếp..."
                        )
                        return filter_results
                    except Exception as exc:
                        logger.error(
                            f"[Dòng 2 - Bộ lọc 24h] Lỗi chạy kênh [{ch_folder}] khung {f_slot}: {exc}"
                        )
                    finally:
                        self._release_browser_process_lock(lock_fh)

        return filter_results

    def warmup_browse_channel(self, ch_folder: str, browse_seconds: int = 150) -> None:
        """Lướt FYP TikTok tự nhiên bằng CoreGraphics CGEvent (chuột/bàn phím cấp OS) để TikTok nhận diện account thật."""
        user_data_dir = self.profiles_root / ch_folder
        if not user_data_dir.exists():
            user_data_dir.mkdir(parents=True, exist_ok=True)

        lock_fh = self._acquire_browser_process_lock()
        if lock_fh is None:
            logger.info(f"[Warmup] [{ch_folder}] Browser lock busy, skip")
            return

        try:
            from playwright.sync_api import sync_playwright

            logger.info(f"[Warmup] [{ch_folder}] Bắt đầu lướt FYP {browse_seconds}s (CoreGraphics CGEvent)...")
            with sync_playwright() as p:
                context = self._launch_stealth_chrome_context(
                    playwright_obj=p, user_data_dir=user_data_dir, headless=False
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    self._restore_synced_channel_session_if_needed(context, user_data_dir)

                    page.goto("https://www.tiktok.com/foryou", wait_until="domcontentloaded", timeout=30000)
                    time.sleep(random.uniform(3, 5))

                    # Di chuột vào giữa trang để focus
                    self._human_move_mouse(page, random.uniform(400, 800), random.uniform(300, 500))
                    time.sleep(random.uniform(0.5, 1.0))

                    start = time.time()
                    scroll_count = 0
                    while time.time() - start < browse_seconds:
                        watch_time = random.uniform(5, 15)
                        time.sleep(watch_time)

                        # Di chuột ngẫu nhiên trong viewport (giả lập người xem)
                        if random.random() < 0.3:
                            self._human_move_mouse(
                                page,
                                random.uniform(300, 900),
                                random.uniform(200, 600),
                            )
                            time.sleep(random.uniform(0.3, 0.8))

                        # 5% xác suất like bằng CoreGraphics click
                        if random.random() < 0.05:
                            try:
                                like_btn = page.locator('[data-e2e="like-icon"]').first
                                if like_btn.is_visible(timeout=1000):
                                    box = like_btn.bounding_box()
                                    if box:
                                        self._os_click_viewport(
                                            page,
                                            box["x"] + box["width"] / 2,
                                            box["y"] + box["height"] / 2,
                                        )
                                        logger.info(f"  [{ch_folder}] ❤️ Liked video #{scroll_count + 1}")
                                        time.sleep(random.uniform(0.5, 1.5))
                            except Exception:
                                pass

                        # Scroll xuống video tiếp = ArrowDown qua OS keyboard (CoreGraphics CGEvent)
                        self.desktop.key_press_os("arrow-down")
                        scroll_count += 1
                        time.sleep(random.uniform(0.8, 2.0))

                    logger.info(f"[Warmup] [{ch_folder}] ✅ Xong! {scroll_count} video trong {time.time() - start:.0f}s")
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
        except Exception as exc:
            logger.error(f"[Warmup] [{ch_folder}] Lỗi: {exc}")
        finally:
            self._release_browser_process_lock(lock_fh)

    def check_and_run_warmup_browse(self) -> None:
        """
        Tự động lướt FYP cho 1 kênh mỗi lần gọi, chọn kênh lâu nhất chưa lướt.
        - Mỗi kênh tối đa 4 lần/ngày, cách nhau tối thiểu 3 giờ
        - Cách kênh trước tối thiểu 10 phút (tránh fingerprint collision)
        - Chỉ chạy trên máy Intel (x86_64)
        """
        if platform.machine() != "x86_64":
            return

        cfg = self.ensure_schedule_config()
        all_filter_channels = cfg.get("active_filter_24h_channels", [])
        if not all_filter_channels:
            return

        browse_seconds = int(cfg.get("warmup_browse_seconds", 150))

        # Đọc lịch sử lướt từ file JSON
        history_file = self.profiles_root / ".warmup_browse_history.json"
        history: Dict[str, Any] = {}
        if history_file.exists():
            try:
                history = json.loads(history_file.read_text(encoding="utf-8"))
            except Exception:
                history = {}

        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now.strftime("%Y-%m-%d")

        # Kiểm tra gap 10 phút với kênh cuối cùng đã lướt
        last_any_ts_str = history.get("_last_any_warmup_ts", "")
        if last_any_ts_str:
            try:
                last_any_ts = datetime.strptime(last_any_ts_str, "%Y-%m-%d %H:%M:%S")
                if (now - last_any_ts).total_seconds() < 600:
                    return
            except Exception:
                pass

        # Tìm kênh phù hợp: chưa lướt hôm nay >= 4 lần, cách lần cuối >= 3 giờ
        best_ch = None
        best_last_ts = datetime.min
        for ch in all_filter_channels:
            ch_data = history.get(ch, {})
            today_count = ch_data.get(f"count_{today_str}", 0)
            if today_count >= 4:
                continue
            last_ts_str = ch_data.get("last_ts", "")
            if last_ts_str:
                try:
                    last_ts = datetime.strptime(last_ts_str, "%Y-%m-%d %H:%M:%S")
                    if (now - last_ts).total_seconds() < 10800:  # 3 giờ
                        continue
                    if best_ch is None or last_ts < best_last_ts:
                        best_ch = ch
                        best_last_ts = last_ts
                except Exception:
                    best_ch = ch
                    best_last_ts = datetime.min
            else:
                # Chưa lướt bao giờ → ưu tiên cao nhất
                if best_ch is None or best_last_ts > datetime.min:
                    best_ch = ch
                    best_last_ts = datetime.min

        if not best_ch:
            return

        logger.info(f"[Warmup Auto] Chọn kênh [{best_ch}] để lướt FYP ({browse_seconds}s)")
        self.warmup_browse_channel(best_ch, browse_seconds=browse_seconds)

        # Cập nhật lịch sử
        ch_data = history.get(best_ch, {})
        ch_data["last_ts"] = now_str
        ch_data[f"count_{today_str}"] = ch_data.get(f"count_{today_str}", 0) + 1
        history[best_ch] = ch_data
        history["_last_any_warmup_ts"] = now_str
        try:
            history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def check_and_run_scheduled_posts(self) -> List[Dict[str, Any]]:
        """
        Điều phối cả 2 dòng hoạt động:
        - Dòng 1: Giữ nguyên khung giờ đăng video cho 4 kênh (`check_and_run_stream1_upload_schedule`).
        - Dòng 2: Mở 4 kênh chạy Bộ lọc 24 giờ 7 lần/ngày × 10 video/kênh (`check_and_run_stream2_filter24h_schedule`).
        """
        posted_results = self.check_and_run_stream1_upload_schedule()
        self.check_and_run_stream2_filter24h_schedule()
        return posted_results
