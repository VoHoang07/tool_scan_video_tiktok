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

from .crawler import CHANNEL_PROFILES, StoryAudioCrawler, resolve_channel_profile
from .utils import logger

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


class PosterBrowserMixin:
    """Quản lý khởi tạo Chrome Anti-Detect, khóa phiên trình duyệt và đồng bộ đăng nhập."""

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


    def _refresh_browser_process_lock(self) -> None:
        """Làm mới dấu thời gian `ts=` trong `.tiktok_browser_session.lock` khi đang cào/render hoặc tải file nặng."""
        try:
            lock_path = self.project_root / "workspace_temp" / ".tiktok_browser_session.lock"
            if lock_path.exists():
                lock_path.write_text(f"pid={os.getpid()} ts={int(time.time())}\n", encoding="utf-8")
        except Exception:
            pass


    def _acquire_browser_process_lock(self, max_wait_seconds: int = 720) -> Any:
        """
        Khóa đồng bộ liên tiến trình (`fcntl.flock`) để khi chạy 2 cửa sổ Terminal độc lập
        (`--stream-upload` và `--stream-filter24h`), 2 dòng hoạt động tuần tự nhường Chrome & chuột OS cho nhau.
        Có tích hợp bộ giám sát (Watchdog Timeout): nếu khóa bị treo quá `max_wait_seconds` (mặc định 12 phút)
        do một tiến trình Chrome bị đơ mạng, tự động giải phóng Chrome treo để không làm kẹt Dòng 1 / Dòng 2.
        """
        import fcntl

        lock_path = self.project_root / "workspace_temp" / ".tiktok_browser_session.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+", encoding="utf-8")
        start_wait = time.time()
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    fh.seek(0)
                    fh.truncate()
                    fh.write(f"pid={os.getpid()} ts={int(time.time())}\n")
                    fh.flush()
                except Exception:
                    pass
                return fh
            except BlockingIOError:
                elapsed = time.time() - start_wait
                lock_ts_age = elapsed
                try:
                    txt = lock_path.read_text(encoding="utf-8").strip()
                    if "ts=" in txt:
                        ts_val = int(txt.split("ts=")[1].split()[0])
                        lock_ts_age = time.time() - ts_val
                except Exception:
                    pass
                if max_wait_seconds <= 15 and elapsed >= max_wait_seconds and lock_ts_age < 360:
                    try:
                        fh.close()
                    except Exception:
                        pass
                    return None
                if (elapsed >= max_wait_seconds and lock_ts_age >= 360) or lock_ts_age >= 420:
                    logger.warning(
                        f"[Browser Lock Watchdog] Khóa trình duyệt bị giữ quá {int(elapsed)}s (lock_ts_age={int(lock_ts_age)}s) — "
                        "tự động giải phóng tiến trình Chrome for Testing bị treo & tái tạo inode khóa mới!"
                    )
                    try:
                        subprocess.run(
                            ["pkill", "-9", "-f", "Google Chrome for Testing"],
                            capture_output=True,
                            timeout=5,
                            check=False,
                        )
                        try:
                            fh.close()
                        except Exception:
                            pass
                        lock_path.unlink(missing_ok=True)
                        time.sleep(1.0)
                        fh = open(lock_path, "a+", encoding="utf-8")
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fh.seek(0)
                        fh.truncate()
                        fh.write(f"pid={os.getpid()} ts={int(time.time())}\n")
                        fh.flush()
                        return fh
                    except Exception:
                        try:
                            fh.close()
                        except Exception:
                            pass
                        return None
                time.sleep(1.0)


    def _release_browser_process_lock(self, fh: Any = None) -> None:
        import fcntl

        if fh is not None:
            try:
                fh.seek(0)
                fh.truncate()
                fh.write("idle\n")
                fh.flush()
            except Exception:
                pass
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
        PosterBrowserMixin._cleanup_stale_chrome_profile_locks(user_data_dir)
        fp = PosterBrowserMixin._get_or_create_channel_fingerprint(user_data_dir)
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
        proxy_cfg = PosterBrowserMixin._parse_playwright_proxy(str(fp.get("proxy_server") or ""))
        if proxy_cfg:
            ctx_kwargs["proxy"] = proxy_cfg

        try:
            context = playwright_obj.chromium.launch_persistent_context(
                channel="chrome",
                **ctx_kwargs,
            )
        except Exception:
            PosterBrowserMixin._cleanup_stale_chrome_profile_locks(user_data_dir)
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
        PosterBrowserMixin._restore_synced_channel_session_if_needed(context, user_data_dir)
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
            local_prof = Path(__file__).resolve().parent.parent / "browser_profiles" / ch_folder
            fp_path = local_prof / "profile_fingerprint.json"
            if fp_path.exists() and "profile_fingerprint" not in state_dict:
                try:
                    state_dict = dict(state_dict)
                    state_dict["profile_fingerprint"] = json.loads(fp_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            raw = json.dumps(state_dict, ensure_ascii=False).encode("utf-8")
            compressed = zlib.compress(raw, level=9)
            enc = PosterBrowserMixin._session_cipher_bytes(compressed)
            out_path = sess_dir / f"{ch_folder}.bin"
            out_path.write_bytes(enc)
            # Đồng thời cập nhật dấu vết tại profile local để không tự ghi đè lại chính nó
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

            compressed = PosterBrowserMixin._session_cipher_bytes(enc)
            raw = zlib.decompress(compressed)
            state_dict = json.loads(raw.decode("utf-8"))
            user_data_dir.mkdir(parents=True, exist_ok=True)
            if isinstance(state_dict.get("profile_fingerprint"), dict):
                try:
                    (user_data_dir / "profile_fingerprint.json").write_text(
                        json.dumps(state_dict["profile_fingerprint"], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
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

