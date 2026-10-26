import csv
import hashlib
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .crawler import CHANNEL_PROFILES, StoryAudioCrawler, resolve_channel_profile
from .growth_24h_filter import TikTok24hFilterEngine
from .macos_desktop_controller import MacOSDesktopController
from .utils import logger

DEFAULT_SCHEDULE_CONFIG: Dict[str, Any] = {
    "timezone": "Asia/Ho_Chi_Minh",
    "default_post_times": ["08:00", "12:30", "20:00"],
    "channel_post_times": {
        "video_AI_hay": ["08:00", "12:30", "20:00"],
        "baby_AI": ["08:15", "12:45", "20:15"],
        "audio_dem_khuyen": ["08:30", "13:00", "20:30"],
        "tu_chan_gioi": ["08:45", "13:15", "20:45"],
    },
    "filter_24h_enabled": True,
    "filter_24h_videos_per_session": 2,
    "channel_filter_24h_times": {
        "video_AI_hay": ["09:30", "15:00", "21:30"],
        "baby_AI": ["09:42", "15:12", "21:42"],
        "audio_dem_khuyen": ["09:54", "15:24", "21:54"],
        "tu_chan_gioi": ["10:06", "15:36", "22:06"],
    },
    "random_jitter_minutes": 9,
    "warmup_before_upload": True,
    "use_macos_native_input": True,
    "headless": False,
}

# Script chống phát hiện trình duyệt tự động (Stealth Anti-Detect cho TikTok mssdk / secsdk)
STEALTH_INIT_SCRIPT = """
(() => {
    // 1. Xóa dấu vết navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });

    // 2. Xóa các biến toàn cục của Playwright / Automation
    const automationProps = [
        '__playwright', '__pw_manual', '__PW_inspect',
        'cdc_adoQpoasnfa76pfcZLmcfl_Array',
        'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
        'cdc_adoQpoasnfa76pfcZLmcfl_Symbol'
    ];
    for (const prop of automationProps) {
        try { delete window[prop]; } catch (e) {}
    }

    // 3. Giả lập đối tượng window.chrome chuẩn của Google Chrome thật
    if (!window.chrome) {
        window.chrome = {};
    }
    if (!window.chrome.runtime) {
        window.chrome.runtime = {
            PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
            PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
            RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' }
        };
    }

    // 4. Chuẩn hóa ngôn ngữ trình duyệt sang Tiếng Việt
    Object.defineProperty(navigator, 'languages', {
        get: () => ['vi-VN', 'vi', 'en-US', 'en']
    });

    // 5. Chuẩn hóa Permissions API
    if (navigator.permissions && navigator.permissions.query) {
        const originalQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters)
        );
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
        self.desktop = MacOSDesktopController(project_root=self.project_root)
        self.filter_24h = TikTok24hFilterEngine(
            project_root=self.project_root,
            desktop=self.desktop,
            human_move_fn=self._human_move_mouse,
            os_click_fn=self._os_click_viewport,
        )
        self.ensure_schedule_config()

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

    @staticmethod
    def _launch_stealth_chrome_context(playwright_obj: Any, user_data_dir: Path, headless: bool = False) -> Any:
        """
        Khởi chạy Google Chrome thật với cấu hình Anti-Detect (xóa `--enable-automation`,
        bật WebGL/GPU tự nhiên, tiêm `STEALTH_INIT_SCRIPT`).
        """
        TikTokAutoPoster._cleanup_stale_chrome_profile_locks(user_data_dir)
        launch_args = [
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            "--lang=vi-VN",
        ]
        ignore_args = ["--enable-automation", "--no-sandbox"]

        try:
            context = playwright_obj.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                channel="chrome",
                headless=headless,
                viewport={"width": 1366, "height": 860},
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
                viewport={"width": 1366, "height": 860},
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                args=launch_args,
                ignore_default_args=ignore_args,
            )

        context.add_init_script(STEALTH_INIT_SCRIPT)
        return context

    def _human_move_mouse(self, page: Any, target_x: float, target_y: float) -> None:
        """
        Di chuyển chuột theo đường cong Bezier mềm mại nhiều bước:
        - Kết hợp điều khiển con trỏ phần cứng hệ điều hành macOS (`CoreGraphics CGEvent` từ `macos-desktop-control`)
          và sự kiện con trỏ trình duyệt để tạo điểm tin cậy hành vi `isTrusted = true` tuyệt đối.
        """
        try:
            sx, sy = self.desktop.convert_viewport_to_screen_coords(page, target_x, target_y)
            self.desktop.move_mouse_smooth_os(sx, sy, steps=14)
        except Exception:
            pass
        steps = random.randint(10, 18)
        for s in range(1, steps + 1):
            frac = s / steps
            jitter_x = random.uniform(-3.5, 3.5) * (1.0 - frac)
            jitter_y = random.uniform(-3.5, 3.5) * (1.0 - frac)
            page.mouse.move(target_x * frac + jitter_x, target_y * frac + jitter_y)
            time.sleep(random.uniform(0.005, 0.015))

    def _os_click_viewport(self, page: Any, viewport_x: float, viewport_y: float) -> None:
        """
        Di chuyển chuột phần cứng hệ điều hành macOS (`CoreGraphics CGEvent` từ `macos-desktop-control`)
        kết hợp đường cong Bezier tới `(viewport_x, viewport_y)` và thực hiện ĐÚNG 1 LẦN CLICK DUY NHẤT
        (tuyệt đối không click đúp 2 lần làm tự gỡ Follow / gỡ Thả tim).
        """
        self._human_move_mouse(page, viewport_x, viewport_y)
        time.sleep(random.uniform(0.06, 0.14))
        page.mouse.click(viewport_x, viewport_y, delay=random.randint(45, 90))

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

    def _select_hashtag_suggestion_if_open(self, page: Any, tag_word: str) -> bool:
        """
        Kiểm tra xem menu gợi ý Hashtag của TikTok Studio có đang mở bên dưới ô `Mô tả` hay không.
        Nếu có mục gợi ý khớp với `#tag_word` (hoặc mục đầu tiên trong danh sách gợi ý hashtag),
        click trực tiếp vào mục đó để gắn thẻ Hashtag Pill chuẩn của TikTok.
        """
        try:
            pos = page.evaluate(
                """(kw) => {
                    const kwLower = (kw || '').toLowerCase();
                    const editor = document.querySelector('div.public-DraftEditor-content[contenteditable="true"], div[contenteditable="true"]');
                    const edRect = editor ? editor.getBoundingClientRect() : { top: 100, bottom: 260, left: 260 };

                    // Tìm các phần tử gợi ý hashtag nằm trong popup/listbox bên dưới ô Mô tả
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
                        if (el.children.length > 4) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 40 || r.height < 16 || r.height > 65) continue;
                        if (r.top < edRect.top - 10 || r.top > edRect.bottom + 320) continue;
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (!txt) continue;
                        const tLower = txt.toLowerCase();
                        if (tLower.includes('#' + kwLower) || tLower.startsWith(kwLower) || tLower.startsWith('#')) {
                            matches.push({
                                x: r.left + Math.min(80, r.width / 2),
                                y: r.top + r.height / 2,
                                exact: tLower.includes(kwLower) ? 1 : 0,
                                top: r.top
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
        Xóa sạch tên file mặc định (`20261025_152239_tiktok_story_...`) mà TikTok Studio tự điền vào ô `Mô tả`
        (KHÔNG dùng `execCommand('delete')` trên root DraftJS để tránh làm hỏng cấu trúc DOM của Hashtag),
        đảm bảo 100% `caption` là mô tả tiếng Việt theo ngữ cảnh (không bao giờ gõ lại dãy số tên file),
        sau đó gõ Kịch bản ngắn (`caption`) và gắn đầy đủ các thẻ `#hashtag` Hot Search.
        """
        # Chốt chặn tuyệt đối: Nếu caption vô tình là tên file / dãy số, khôi phục ngay mô tả tiếng Việt theo ngữ cảnh
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

        # Xóa sạch tên file mặc định bằng bàn phím chuẩn (giữ nguyên cấu trúc DraftJS để gõ được #hashtag)
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
                caption_box.click()
                page.keyboard.press("End")
                for _ in range(min(120, len(remaining) + 18)):
                    page.keyboard.press("Backspace")
                page.keyboard.press("Home")
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

        # Gõ Kịch bản mô tả tiếng Việt theo ngữ cảnh vào ô Mô tả
        clean_caption = caption.strip()
        caption_box.click()
        time.sleep(0.2)
        page.keyboard.insert_text(clean_caption)
        time.sleep(0.25)
        page.keyboard.type(" ", delay=55)
        time.sleep(random.uniform(0.4, 0.7))

        # Chuẩn hóa danh sách #hashtag
        raw_tokens = [t.strip() for t in hashtags_str.split() if t.strip()]
        tags: List[str] = []
        for t in raw_tokens:
            norm_t = t if t.startswith("#") else f"#{t}"
            if len(norm_t) > 1 and norm_t.lower() not in [x.lower() for x in tags]:
                tags.append(norm_t)
        if not tags:
            tags = ["#xuhuong", "#xuhuongtiktok", "#fyp", "#tiktokvn"]

        # Gõ từng #hashtag: gõ '#' + từ khóa để kích hoạt gợi ý của TikTok, nếu có popup gợi ý thì click chọn,
        # nếu không có popup thì bấm phím Space (' ') (TUYỆT ĐỐI không bấm Enter mù làm xuống dòng hỏng hashtag!)
        for tag in tags:
            tag_word = tag.lstrip("#").strip()
            if not tag_word:
                continue
            caption_box.click()
            page.keyboard.press("End")
            time.sleep(0.1)
            page.keyboard.type("#", delay=55)
            time.sleep(0.2)
            page.keyboard.type(tag_word, delay=random.randint(35, 60))
            time.sleep(random.uniform(0.95, 1.3))

            picked = self._select_hashtag_suggestion_if_open(page, tag_word)
            if not picked:
                page.keyboard.type(" ", delay=55)
            else:
                time.sleep(0.2)
                page.keyboard.press("End")
                page.keyboard.type(" ", delay=50)
            time.sleep(random.uniform(0.2, 0.35))

        # Kiểm tra xác nhận nội dung trong ô Mô tả đã có đủ Kịch bản tiếng Việt + Hashtag và không còn tên file số
        try:
            final_text = (caption_box.inner_text() or "").strip()
            if (
                not final_text
                or self.crawler.is_raw_filename_caption(final_text, video_stem)
                or "#" not in final_text
            ):
                caption_box.click()
                page.keyboard.press("Meta+A")
                page.keyboard.press("Backspace")
                full_combined = f"{clean_caption} {' '.join(tags)} "
                page.keyboard.insert_text(full_combined)
                time.sleep(0.25)
                page.keyboard.type(" ", delay=50)
                final_text = (caption_box.inner_text() or "").strip()
            logger.info(
                f"[{ch_folder}] Đã nhập xong Mô tả & Hashtag vào TikTok Studio: \"{final_text[:140]}...\""
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

    def _get_next_ready_video_for_channel(
        self, ch_folder: str
    ) -> Optional[Tuple[Path, str, str]]:
        """
        Lấy video chờ đăng tiếp theo trong `output/<ch_folder>/ready/` cùng `caption` (kịch bản ngắn tiếng Việt)
        và `hashtags` (Hot Search) từ `output/danh_sach_video.csv`.
        Nếu video chưa có trong CSV hoặc `caption` đang bị ghi tên file/dãy số (`20261025_...`),
        tự động khôi phục Kịch bản mô tả tiếng Việt theo ngữ cảnh + Hashtag Hot Search và lưu lại vào CSV.
        """
        ready_dir = self.project_root / "output" / ch_folder / "ready"
        if not ready_dir.exists():
            return None
        ready_files = sorted(f for f in ready_dir.glob("*.mp4") if f.is_file())
        if not ready_files:
            return None

        target_video = ready_files[0]
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

    def upload_one_channel_video(
        self, channel_arg: str, headless: bool = False
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

                # Bước 2: Truy cập TikTok Studio Upload đúng URL giao diện WebApp
                page.goto(
                    "https://www.tiktok.com/tiktokstudio/upload?from=webapp&tab=video",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
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

        # Bước 8: Đánh dấu đã đăng: chuyển từ ready/ sang posted/ và cập nhật CSV
        marked = self.crawler.mark_video_posted(str(video_path))
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
        sau đó tự động `git commit` & `git push` lên GitHub để người dùng ở xa (trên máy Silicon)
        có thể kiểm tra hình ảnh & nhật ký của máy Intel bất kỳ lúc nào mà không cần có mặt ở nhà.
        """
        import platform as _plat
        import subprocess

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

            # Tự động đẩy ảnh chụp màn hình & báo cáo trạng thái lên GitHub
            subprocess.run(
                ["git", "add", "remote_telemetry"],
                cwd=str(self.project_root),
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
                cwd=str(self.project_root),
                capture_output=True,
                timeout=10,
                check=False,
            )
            subprocess.run(
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                cwd=str(self.project_root),
                capture_output=True,
                timeout=20,
                check=False,
            )
            subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=str(self.project_root),
                capture_output=True,
                timeout=25,
                check=False,
            )
            logger.info(
                f"[{ch_folder}] [Remote Telemetry] Đã chụp ảnh màn hình & đẩy báo cáo trạng thái ({img_name}) lên GitHub!"
            )
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Remote Telemetry] Bỏ qua đồng bộ telemetry phụ: {exc}")

    def run_24h_filter_for_channel(
        self,
        channel_arg: str,
        video_count: int = 2,
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
                        details={"interacted_count": len(records), "records": records},
                    )
                    time.sleep(random.uniform(3.0, 5.0))
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

    def check_and_run_scheduled_posts(self) -> List[Dict[str, Any]]:
        """
        Kiểm tra giờ hiện tại (`HH:MM`) so với lịch đăng bài và lịch chạy 'Bộ lọc 24 giờ'
        của từng kênh trong `schedule_config.json` (kèm độ lệch ngẫu nhiên ± phút mỗi ngày).
        """
        cfg = self.ensure_schedule_config()
        ch_times_map: Dict[str, List[str]] = cfg.get("channel_post_times") or {}
        default_times: List[str] = cfg.get("default_post_times") or ["08:00", "12:30", "20:00"]
        filter_enabled = bool(cfg.get("filter_24h_enabled", True))
        filter_count = int(cfg.get("filter_24h_videos_per_session", 2))
        filter_times_map: Dict[str, List[str]] = cfg.get("channel_filter_24h_times") or {}
        max_jitter = int(cfg.get("random_jitter_minutes", 9))
        headless = bool(cfg.get("headless", False))

        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        now_minutes = now.hour * 60 + now.minute
        history = self._load_post_history()
        posted_results: List[Dict[str, Any]] = []

        # 0a. Kiểm tra lệnh kích hoạt ĐĂNG NGAY TỪ XA (Remote Trigger Post-Now từ máy Silicon sang máy Intel qua GitHub)
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
                        f"[Remote Trigger Post-Now] Nhận lệnh ĐĂNG NGAY LẬP TỨC từ máy Silicon ({p_trig_id}) -> "
                        f"Kênh {target_channels}!"
                    )
                    any_ok = False
                    for single_ch in target_channels:
                        try:
                            res = self.upload_one_channel_video(single_ch, headless=headless)
                            if res:
                                any_ok = True
                                posted_results.append(res)
                        except Exception as exc:
                            logger.error(
                                f"[Remote Trigger Post-Now] Lỗi khi đăng ngay ({p_trig_id}) kênh [{single_ch}]: {exc}"
                            )
                    if any_ok:
                        history[p_trig_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self._save_post_history(history)

        # 0b. Kiểm tra lệnh kích hoạt chạy thử Bộ lọc 24h từ xa (Remote Trigger từ máy Silicon sang máy Intel qua GitHub)
        remote_trig = cfg.get("remote_trigger_filter_24h")
        if isinstance(remote_trig, dict) and remote_trig.get("trigger_id"):
            import platform as _plat

            target_arch = str(remote_trig.get("target_arch") or "").strip()
            if not target_arch or _plat.machine() == target_arch:
                trig_id = str(remote_trig["trigger_id"]).strip()
                trig_key = f"remote_trig_{trig_id}"
                if trig_id and trig_key not in history:
                    trig_ch = str(remote_trig.get("channel") or "video_AI_hay").strip()
                    trig_cnt = int(remote_trig.get("video_count") or 2)
                    logger.info(
                        f"[Remote Trigger] Nhận lệnh chạy thử 'Bộ lọc 24 giờ' từ máy Silicon ({trig_id}) -> "
                        f"Kênh [{trig_ch}], số lượng {trig_cnt} video!"
                    )
                    try:
                        self.run_24h_filter_for_channel(trig_ch, video_count=trig_cnt, headless=headless)
                        history[trig_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self._save_post_history(history)
                    except Exception as exc:
                        logger.error(f"[Remote Trigger] Lỗi khi chạy thử Bộ lọc 24h ({trig_id}): {exc}")

        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]

            # 1. Kiểm tra khung giờ ĐĂNG BÀI tự động
            slots = ch_times_map.get(ch_folder) or default_times
            for slot in slots:
                slot_key = f"{today_str}_{ch_folder}_{slot}"
                if slot_key in history:
                    continue
                try:
                    sh, sm = map(int, slot.split(":"))
                except Exception:
                    continue
                jitter = self._compute_daily_jitter_minutes(today_str, ch_folder, slot, max_jitter)
                effective_slot_min = max(0, min(23 * 60 + 59, sh * 60 + sm + jitter))

                # Kích hoạt nếu thời gian hiện tại nằm trong khoảng [effective_slot_min, effective_slot_min + 180 phút (bù giờ tự động 3 tiếng)]
                if 0 <= (now_minutes - effective_slot_min) <= 180:
                    eff_hh, eff_mm = effective_slot_min // 60, effective_slot_min % 60
                    logger.info(
                        f"[Anti-Detect Scheduler] Đã điểm giờ đăng ngẫu nhiên hôm nay của kênh [{ch_folder}]: "
                        f"{eff_hh:02d}:{eff_mm:02d} (khung gốc {slot}, lệch {jitter:+d} phút)"
                    )
                    try:
                        res = self.upload_one_channel_video(ch_folder, headless=headless)
                        if res:
                            history[slot_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self._save_post_history(history)
                            posted_results.append(res)
                    except Exception as exc:
                        logger.error(f"Lỗi tự động đăng theo lịch kênh [{ch_folder}] khung {slot}: {exc}")

            # 2. Kiểm tra khung giờ TƯƠNG TÁC CHÉO 'BỘ LỌC 24 GIỜ' tự động
            if filter_enabled:
                f_slots = filter_times_map.get(ch_folder) or ["09:30", "15:00", "21:30"]
                for f_slot in f_slots:
                    f_key = f"{today_str}_filter24h_{ch_folder}_{f_slot}"
                    if f_key in history:
                        continue
                    try:
                        fh, fm = map(int, f_slot.split(":"))
                    except Exception:
                        continue
                    f_jitter = self._compute_daily_jitter_minutes(today_str, f"f24_{ch_folder}", f_slot, max_jitter)
                    eff_f_min = max(0, min(23 * 60 + 59, fh * 60 + fm + f_jitter))

                    if 0 <= (now_minutes - eff_f_min) <= 20:
                        eff_hh, eff_mm = eff_f_min // 60, eff_f_min % 60
                        logger.info(
                            f"[Bộ lọc 24h Scheduler] Đã điểm giờ chăm kênh & tương tác chéo 'Bộ lọc 24 giờ' của kênh [{ch_folder}]: "
                            f"{eff_hh:02d}:{eff_mm:02d} (khung gốc {f_slot}, lệch {f_jitter:+d} phút)"
                        )
                        try:
                            self.run_24h_filter_for_channel(
                                ch_folder, video_count=filter_count, headless=headless
                            )
                            history[f_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self._save_post_history(history)
                        except Exception as exc:
                            logger.error(f"Lỗi chạy Bộ lọc 24h kênh [{ch_folder}] khung {f_slot}: {exc}")

        return posted_results
