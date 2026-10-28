import csv
import hashlib
import json
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
    "default_post_times": ["08:00", "12:30", "20:00"],
    "channel_post_times": {
        "video_AI_hay": ["08:00", "12:30", "20:00"],
        "baby_AI": ["08:10", "12:40", "20:10"],
        "audio_dem_khuyen": ["08:20", "12:50", "20:20"],
        "tu_chan_gioi": ["08:30", "13:00", "20:30"],
    },
    "filter_24h_enabled": True,
    "filter_24h_runs_per_day": 7,
    "filter_24h_videos_per_session": 10,
    "channel_filter_24h_times": {
        "video_AI_hay": ["06:00", "09:30", "11:00", "14:00", "16:00", "18:00", "21:30"],
        "baby_AI": ["06:15", "09:45", "11:15", "14:15", "16:15", "18:15", "21:45"],
        "audio_dem_khuyen": ["06:30", "10:00", "11:30", "14:30", "16:30", "18:30", "22:00"],
        "tu_chan_gioi": ["06:45", "10:15", "11:45", "14:45", "16:45", "18:45", "22:15"],
    },
    "random_jitter_minutes": 9,
    "warmup_before_upload": True,
    "use_macos_native_input": True,
    "headless": False,
}

# Script dọn dẹp biến toàn cục Playwright (KHÔNG ghi đè Proxy lên console/prototype để giữ 100% [native code] cho webmssdk.js)
STEALTH_INIT_SCRIPT = """
(() => {
    const automationProps = [
        '__playwright', '__pw_manual', '__PW_inspect',
        'cdc_adoQpoasnfa76pfcZLmcfl_Array',
        'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
        'cdc_adoQpoasnfa76pfcZLmcfl_Symbol'
    ];
    for (const prop of automationProps) {
        try { delete window[prop]; } catch (e) {}
    }
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
        """Tạo hoặc đọc file cấu hình khung giờ đăng `schedule_config.json`."""
        if not self.schedule_config_path.exists():
            self.schedule_config_path.write_text(
                json.dumps(DEFAULT_SCHEDULE_CONFIG, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return dict(DEFAULT_SCHEDULE_CONFIG)
        try:
            cfg = json.loads(self.schedule_config_path.read_text(encoding="utf-8"))
            for k, v in DEFAULT_SCHEDULE_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
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
    def _launch_stealth_chrome_context(playwright_obj: Any, user_data_dir: Path, headless: bool = False) -> Any:
        """
        Khởi chạy Google Chrome thật với cấu hình Anti-Detect (xóa `--enable-automation`,
        bật WebGL/GPU tự nhiên, tiêm `STEALTH_INIT_SCRIPT`).
        """
        TikTokAutoPoster._cleanup_stale_chrome_profile_locks(user_data_dir)
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            "--enable-gpu-rasterization",
            "--enable-zero-copy",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            "--window-size=1366,860",
            "--window-position=20,20",
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

        try:
            context = playwright_obj.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                channel="chrome",
                headless=headless,
                no_viewport=True,
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                args=launch_args,
                ignore_default_args=ignore_args,
            )
        except Exception:
            TikTokAutoPoster._cleanup_stale_chrome_profile_locks(user_data_dir)
            context = playwright_obj.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=headless,
                no_viewport=True,
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                args=launch_args,
                ignore_default_args=ignore_args,
            )

        context.add_init_script(STEALTH_INIT_SCRIPT)
        return context

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

    def _configure_upload_settings_public(self, page: Any, ch_folder: str) -> None:
        """
        Tự động cài đặt toàn bộ mục `Cài đặt` và `Kiểm tra` trên trang Upload của TikTok Studio
        (đúng theo Ảnh chụp màn hình 3 & 4 của người dùng):
        1. `Thời điểm đăng`: Chọn `Bây giờ`.
        2. `Người có thể nhìn thấy bài đăng này`: Bắt buộc chọn `Mọi người` (Công khai).
        3. `Nội dung tải lên chất lượng cao`: BẬT (`ON`).
        4. `Cho phép người dùng`: Tích chọn `Bình luận`.
        5. `Khai báo nội dung bài đăng` & `Nội dung do AI tạo`: TẮT (`OFF`).
        6. `Kiểm tra bản quyền nhạc` & `Kiểm tra nội dung nhanh`: BẬT (`ON`).
        """
        logger.info(
            f"[{ch_folder}] Đang tự động cấu hình mục Cài đặt & Quyền riêng tư = 'Mọi người' (Công khai) trên trang Upload..."
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

            # 5. Khai báo nội dung bài đăng & Nội dung do AI tạo -> TẮT (OFF)
            self._set_switch_near_label(
                page, ["Khai báo nội dung bài đăng", "Disclose post content"], desired_on=False
            )
            self._set_switch_near_label(
                page, ["Nội dung do AI tạo", "AI-generated content"], desired_on=False
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
        Mở trình duyệt Chrome Anti-Detect với Profile riêng của kênh (`browser_profiles/<ten_kenh>`)
        để người dùng quét mã QR đăng nhập TikTok 1 lần duy nhất.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài đặt `playwright`. Vui lòng chạy: `./venv/bin/pip install playwright && ./venv/bin/playwright install chromium`"
            ) from exc

        profile = resolve_channel_profile(channel_arg)
        ch_folder = profile["folder_name"]
        user_data_dir = self.profiles_root / ch_folder
        user_data_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"=== ĐĂNG NHẬP KÊNH TIKTOK [{ch_folder}] (CHẾ ĐỘ ANTI-DETECT STEALTH) ===\n"
            f"1. Trình duyệt Chrome sẽ mở trang TikTok Studio.\n"
            f"2. Hãy đăng nhập tài khoản TikTok của kênh [{ch_folder}] (khuyên dùng Quét mã QR trên điện thoại).\n"
            f"3. Sau khi đăng nhập thành công và thấy giao diện Upload của TikTok, hãy ĐÓNG cửa sổ trình duyệt để lưu phiên."
        )

        with sync_playwright() as p:
            context = self._launch_stealth_chrome_context(
                playwright_obj=p, user_data_dir=user_data_dir, headless=False
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.tiktok.com/tiktokstudio/upload?from=webapp&tab=video")

            try:
                while len(context.pages) > 0:
                    time.sleep(1)
            except Exception:
                pass
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        logger.info(f"Đã lưu phiên đăng nhập TikTok cho kênh [{ch_folder}] tại: {user_data_dir}")
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
        for p_mp4 in posted_dir.glob("*.mp4"):
            if p_mp4.is_file():
                fp = self._quick_video_fingerprint(p_mp4)
                if fp:
                    posted_fingerprints.add(fp)

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
            m_cand = re.search(r"(?:tiktok|douyin)_story_(\d+)", cand.stem, re.IGNORECASE)
            cand_id = m_cand.group(1) if m_cand else ""
            cand_fp = self._quick_video_fingerprint(cand)

            if (
                cand.name in posted_filenames
                or (cand_id and cand_id in posted_ids)
                or (cand_fp and cand_fp in posted_fingerprints)
            ):
                dest_dup = posted_dir / cand.name
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
        self, channel_arg: str, headless: bool = False, force: bool = False
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
        if not user_data_dir.exists():
            logger.warning(
                f"Kênh [{ch_folder}] chưa có phiên đăng nhập tại {user_data_dir}. "
                f"Hãy chạy: `./venv/bin/python main.py --login-channel {ch_folder}` trước."
            )
            return None

        next_item = self._get_next_ready_video_for_channel(ch_folder)
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

                # Bước 6: Tự động cấu hình toàn bộ mục Cài đặt & Kiểm tra (Công khai = Mọi người, HD = ON, Bình luận = ON)
                self._dismiss_all_studio_popovers(page, ch_folder)
                self._configure_upload_settings_public(page, ch_folder)
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
        import platform as _plat
        import subprocess
        import threading

        try:
            telem_dir = self.project_root / "remote_telemetry"
            telem_dir.mkdir(parents=True, exist_ok=True)
            img_name = f"latest_{event_type}_{ch_folder}.jpg"
            img_path = telem_dir / img_name
            if page:
                try:
                    page.screenshot(path=str(img_path), type="jpeg", quality=60)
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

            def _async_git_push() -> None:
                try:
                    subprocess.run(
                        ["git", "config", "pull.rebase", "true"],
                        cwd=proj_str,
                        capture_output=True,
                        timeout=5,
                        check=False,
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
                        ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                        cwd=proj_str,
                        capture_output=True,
                        timeout=20,
                        check=False,
                    )
                    subprocess.run(
                        ["git", "push", "origin", "main"],
                        cwd=proj_str,
                        capture_output=True,
                        timeout=25,
                        check=False,
                    )
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
                    except Exception:
                        pass
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
                    any_ok = False
                    for single_ch in target_channels:
                        lock_fh = self._acquire_browser_process_lock()
                        try:
                            res = self.upload_one_channel_video(single_ch, headless=headless)
                            if res:
                                any_ok = True
                                posted_results.append(res)
                        except Exception as exc:
                            logger.error(
                                f"[Dòng 1 - Đăng Video] Lỗi khi đăng ngay ({p_trig_id}) kênh [{single_ch}]: {exc}"
                            )
                        finally:
                            self._release_browser_process_lock(lock_fh)
                    if any_ok:
                        history = self._load_post_history()
                        history[p_trig_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self._save_post_history(history)

        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
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
        filter_count = int(cfg.get("filter_24h_videos_per_session", 10))
        filter_times_map: Dict[str, List[str]] = cfg.get("channel_filter_24h_times") or {}
        max_jitter = int(cfg.get("random_jitter_minutes", 9))
        headless = bool(cfg.get("headless", False))

        history = self._load_post_history()
        filter_results: Dict[str, Any] = {}

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

        default_7_slots = ["06:00", "09:30", "11:00", "14:00", "16:00", "18:00", "21:30"]
        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            now_minutes = now.hour * 60 + now.minute
            history = self._load_post_history()

            f_slots = filter_times_map.get(ch_folder) or default_7_slots
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

                # Cửa sổ kích hoạt 90 phút để cả 4 kênh nối tiếp nhau chạy đủ 10 video/kênh trong mỗi lượt
                if 0 <= (now_minutes - eff_f_min) <= 90:
                    lock_fh = self._acquire_browser_process_lock()
                    try:
                        history = self._load_post_history()
                        if f_key in history:
                            continue
                        history[f_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self._save_post_history(history)
                        eff_hh, eff_mm = eff_f_min // 60, eff_f_min % 60
                        logger.info(
                            f"[Dòng 2 - Bộ lọc 24h (Lần {round_idx}/{len(f_slots)} trong ngày)] "
                            f"Mở kênh [{ch_folder}] lúc {eff_hh:02d}:{eff_mm:02d} (khung {f_slot}) -> Chạy {filter_count} video!"
                        )
                        res = self.run_24h_filter_for_channel(
                            ch_folder, video_count=filter_count, headless=headless
                        )
                        filter_results[f_key] = res
                        logger.info(
                            f"[Dòng 2 - Bộ lọc 24h] Đã hoàn tất phiên [{ch_folder}] (khung {f_slot}) -> "
                            "Đang chờ tới khung giờ kế tiếp theo lịch 7 lần/ngày..."
                        )
                    except Exception as exc:
                        logger.error(
                            f"[Dòng 2 - Bộ lọc 24h] Lỗi chạy kênh [{ch_folder}] khung {f_slot}: {exc}"
                        )
                    finally:
                        self._release_browser_process_lock(lock_fh)

        return filter_results

    def check_and_run_scheduled_posts(self) -> List[Dict[str, Any]]:
        """
        Điều phối cả 2 dòng hoạt động:
        - Dòng 1: Giữ nguyên khung giờ đăng video cho 4 kênh (`check_and_run_stream1_upload_schedule`).
        - Dòng 2: Mở 4 kênh chạy Bộ lọc 24 giờ 7 lần/ngày × 10 video/kênh (`check_and_run_stream2_filter24h_schedule`).
        """
        posted_results = self.check_and_run_stream1_upload_schedule()
        self.check_and_run_stream2_filter24h_schedule()
        return posted_results
