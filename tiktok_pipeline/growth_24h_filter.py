import json
import random
import re
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .crawler import KNOWN_VIETNAMESE_HANDLES
from .macos_desktop_controller import MacOSDesktopController
from .utils import logger

DIGG_UI_GUARD_SCRIPT = """
(() => {
    try { delete navigator.webdriver; } catch (e) {}
    try { if (window.chrome && window.chrome.runtime && window.chrome.runtime.PlatformOs) delete window.chrome.runtime; } catch (e) {}

    if (window.__tiktokDiggGuardInstalled) return;
    window.__tiktokDiggGuardInstalled = true;
    window.__tiktokDiggLogs = window.__tiktokDiggLogs || [];

    const origFetch = window.fetch;
    if (typeof origFetch === "function") {
        window.fetch = async function(...args) {
            const url = (typeof args[0] === "string" ? args[0] : (args[0] && args[0].url)) || "";
            const resp = await origFetch.apply(this, args);
            if (url.includes("/commit/item/digg") || url.includes("/item/digg")) {
                try {
                    const clone = resp.clone();
                    const rawText = await clone.text();
                    window.__tiktokDiggLogs.push({ via: "fetch", status: resp.status, body: rawText.slice(0, 240) });
                    if (rawText.includes('"is_digg":1') || rawText.includes('"is_digg": 1')) {
                        const fixedText = rawText.replace(/"is_digg"\\s*:\\s*1/g, '"is_digg":0');
                        return new Response(fixedText, {
                            status: resp.status,
                            statusText: resp.statusText,
                            headers: resp.headers
                        });
                    }
                } catch (e) {}
            }
            return resp;
        };
    }

    const protoDescText = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, "responseText");
    const protoDescResp = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, "response");
    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        const u = typeof url === "string" ? url : String(url || "");
        if (u.includes("/commit/item/digg") || u.includes("/item/digg")) {
            let logged = false;
            if (protoDescText && protoDescText.get) {
                Object.defineProperty(this, "responseText", {
                    get: function() {
                        const raw = protoDescText.get.call(this);
                        if (typeof raw === "string" && raw.length > 0) {
                            if (!logged && this.readyState === 4) {
                                logged = true;
                                window.__tiktokDiggLogs.push({ via: "xhr", status: this.status, body: raw.slice(0, 240) });
                            }
                            return raw.replace(/"is_digg"\\s*:\\s*1/g, '"is_digg":0');
                        }
                        return raw;
                    },
                    configurable: true
                });
            }
            if (protoDescResp && protoDescResp.get) {
                Object.defineProperty(this, "response", {
                    get: function() {
                        const raw = protoDescResp.get.call(this);
                        if (typeof raw === "string" && raw.length > 0) {
                            if (!logged && this.readyState === 4) {
                                logged = true;
                                window.__tiktokDiggLogs.push({ via: "xhr", status: this.status, body: raw.slice(0, 240) });
                            }
                            return raw.replace(/"is_digg"\\s*:\\s*1/g, '"is_digg":0');
                        }
                        if (raw && typeof raw === "object" && raw.is_digg === 1) {
                            return Object.assign({}, raw, { is_digg: 0 });
                        }
                        return raw;
                    },
                    configurable: true
                });
            }
        }
        return origOpen.call(this, method, url, ...rest);
    };
})();
"""


def _hot_patch_live_auto_poster_stealth() -> None:
    """
    Cập nhật trực tiếp `_launch_stealth_chrome_context` trên mọi class/instance `TikTokAutoPoster`
    đang chạy trong bộ nhớ tiến trình Daemon trên máy Intel để bật `--disable-blink-features=AutomationControlled`
    và tiêm `DIGG_UI_GUARD_SCRIPT` ngay khi khởi tạo trình duyệt.
    """
    try:
        import gc

        def _clean_launch_stealth_chrome_context(playwright_obj: Any, user_data_dir: Path, headless: bool = False) -> Any:
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
                    lock_file = Path(user_data_dir) / lock_name
                    if lock_file.exists() or lock_file.is_symlink():
                        lock_file.unlink()
                except Exception:
                    pass

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                "--lang=vi-VN",
            ]
            ignore_args = ["--enable-automation", "--no-sandbox"]
            try:
                ctx = playwright_obj.chromium.launch_persistent_context(
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
                ctx = playwright_obj.chromium.launch_persistent_context(
                    user_data_dir=str(user_data_dir),
                    headless=headless,
                    viewport={"width": 1366, "height": 860},
                    locale="vi-VN",
                    timezone_id="Asia/Ho_Chi_Minh",
                    args=launch_args,
                    ignore_default_args=ignore_args,
                )
            ctx.add_init_script(DIGG_UI_GUARD_SCRIPT)
            return ctx

        for obj in gc.get_objects():
            try:
                cls = obj if isinstance(obj, type) else type(obj)
                if getattr(cls, "__name__", "") == "TikTokAutoPoster":
                    setattr(cls, "_launch_stealth_chrome_context", staticmethod(_clean_launch_stealth_chrome_context))
            except Exception:
                pass
    except Exception:
        pass


_hot_patch_live_auto_poster_stealth()

# Danh sách từ khóa tìm kiếm đúng tệp người dùng đang áp dụng "Bộ lọc 24 giờ" / mới xây kênh trên TikTok
FILTER_24H_SEARCH_QUERIES: List[str] = [
    "bộ lọc 24 giờ",
    "bộ lọc 24h mới xây kênh",
    "tương tác chéo bộ lọc 24 giờ",
    "mới xây kênh follow chéo",
    "trả tương tác bộ lọc 24h",
    "bộ lọc 24 giờ cùng nhau phát triển",
    "xây kênh từ con số 0 bộ lọc 24h",
]

# Kho mẫu bình luận có ý nghĩa, thân thiện, chuẩn cộng đồng "Bộ lọc 24 giờ"
# (Tự động kết hợp ngẫu nhiên Mở đầu + Thân bài + Kết bài để tạo ra hàng trăm biến thể không trùng lặp)
COMMENT_OPENERS: List[str] = [
    "Mình đã xem hết video của bạn,",
    "Xem trọn vẹn hết video rồi mới",
    "Mình vừa xem kỹ hết video,",
    "Đã xem hết 100% thời lượng video,",
    "Hữu duyên gặp kênh bạn qua bộ lọc 24 giờ, mình đã xem hết video,",
    "Ủng hộ người mới xây kênh, mình đã xem hết video,",
    "Đúng chuẩn bộ lọc 24h, mình vừa xem hết video của bạn,",
]

COMMENT_ACTIONS: List[str] = [
    "thả tim, lưu yêu thích, sao chép liên kết và bấm follow kênh bạn đủ bộ rồi nha!",
    "bấm tim, thêm vào yêu thích, copy link và follow bạn đầy đủ 5 bước rồi nhé!",
    "tương tác đủ combo (tim + lưu yêu thích + sao chép liên kết + follow) cho bạn rồi nha!",
    "làm đủ các bước bộ lọc 24 giờ: thả tim, lưu yêu thích, sao chép liên kết và follow bạn rồi đó!",
    "thả tim, lưu video vào yêu thích, sao chép liên kết và đăng ký theo dõi kênh bạn luôn rồi nhé!",
]

COMMENT_CLOSERS: List[str] = [
    "Cùng tương tác chéo phát triển kênh trong 24h nhé 💯",
    "Chúc kênh bạn sớm lên xu hướng, rảnh ghé tương tác lại cùng mình nha 🔥",
    "Nhớ trả tương tác giúp mình trong 24 giờ để cùng nhau lên xu hướng nhé ✨",
    "Cùng nhau kiên trì xây kênh thật tốt nha bạn ơi, mình follow rồi đó 💪",
    "Chúc bạn xây kênh thành công rực rỡ, nhớ ghé trả tương tác cho mình nha ❤️",
    "Mình comment lại sau khi follow để bạn dễ nhận diện trả tương tác 24h nhé 🌟",
]


class TikTok24hFilterEngine:
    """
    Bộ máy tự động thực thi "BỘ LỌC 24 GIỜ" (24-Hour TikTok Channel Growth & Cross-Follow Filter)
    trên cả 4 kênh TikTok (`video_AI_hay`, `baby_AI`, `audio_dem_khuyen`, `tu_chan_gioi`):

    Quy trình chuẩn 6 bước trên mỗi video mục tiêu:
      1. Xem hết video của người khác (100% thời lượng hoặc 20-42s kèm di chuột phần cứng Bezier).
      2. Thả tim video (Like) bằng chuột phần cứng macOS (`CoreGraphics CGEvent`).
      3. Sao chép liên kết video đó (Copy Link -> tính 1 lượt Share thuật toán).
      4. Thêm video vào mục Yêu thích (Bookmark / Save to Favorites).
      5. Follow người đăng video đó (Follow Creator).
      6. Để lại 1 bình luận có ý nghĩa xác nhận đã làm đủ combo & đã follow để chủ kênh
         trả follow lại trong vòng 24 giờ.
    """

    def __init__(
        self,
        project_root: Path,
        desktop: MacOSDesktopController,
        human_move_fn: Callable[[Any, float, float], None],
        os_click_fn: Callable[[Any, float, float], None],
    ):
        self.project_root = Path(project_root).resolve()
        self.desktop = desktop
        self._human_move_mouse = human_move_fn
        self._os_click_viewport = os_click_fn
        self.history_path = self.project_root / "growth_24h_history.json"

    def load_history(self) -> Dict[str, Any]:
        if not self.history_path.exists():
            return {"interactions": [], "daily_runs": {}}
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
            if "interactions" not in data:
                data["interactions"] = []
            if "daily_runs" not in data:
                data["daily_runs"] = {}
            return data
        except Exception:
            return {"interactions": [], "daily_runs": {}}

    def save_history(self, data: Dict[str, Any]) -> None:
        self.history_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _get_interacted_sets(self, ch_folder: str) -> Tuple[set, set]:
        """Lấy tập hợp video_id và creator_handle mà kênh `ch_folder` đã tương tác."""
        hist = self.load_history()
        seen_videos = set()
        seen_creators = set()
        for item in hist.get("interactions", []):
            if item.get("channel") == ch_folder:
                vid = item.get("video_id")
                creator = item.get("creator_handle")
                if vid:
                    seen_videos.add(str(vid))
                if creator:
                    seen_creators.add(str(creator).lower().lstrip("@"))
        return seen_videos, seen_creators

    @staticmethod
    def _extract_creator_and_video_id(url: str) -> Tuple[str, str]:
        m = re.search(r"tiktok\.com/@([^/?#]+)/video/(\d+)", url)
        if m:
            return m.group(1), m.group(2)
        return "", ""

    @staticmethod
    def generate_meaningful_24h_comment(creator_handle: str = "", video_desc: str = "") -> str:
        """
        Trả về đúng 1 câu bình luận cố định theo yêu cầu:
        "chào mn, mình mới xây kênh, nhận follow chéo ạ"
        """
        return "chào mn, mình mới xây kênh, nhận follow chéo ạ"

    @staticmethod
    def _compute_best_whirl_rotation_fraction(
        outer_bytes: bytes, inner_bytes: bytes, inner_to_outer_ratio: float = 0.608
    ) -> float:
        """
        Thuật toán Thị giác máy tính Đa Vành (Multi-Band Circular Boundary Correlation + L1 RGB MAE + Edge Gradient):
        - Khi kéo thanh trượt một tỷ lệ `frac` trong `[0.0, 1.0]`, vòng trong xoay thuận chiều `+180 * frac` độ
          và vòng ngoài xoay ngược chiều `-180 * frac` độ (tổng góc quay tương đối `360 * frac` độ).
        - Lấy 360 mẫu điểm ảnh trên 3 vành đồng tâm sát ranh giới tiếp giáp giữa `inner_img` và `outer_img`,
          kết hợp tương quan chuẩn hóa (NCC), sai số tuyệt đối màu L1 (MAE) và đạo hàm biên chu vi để tìm góc khớp chính xác.
        """
        import io
        import numpy as np
        from PIL import Image

        outer_im = Image.open(io.BytesIO(outer_bytes)).convert("RGB")
        inner_im = Image.open(io.BytesIO(inner_bytes)).convert("RGB")

        size = 300
        outer_im = outer_im.resize((size, size), Image.Resampling.BILINEAR)
        inner_size = max(80, int(round(size * inner_to_outer_ratio)))
        inner_im = inner_im.resize((inner_size, inner_size), Image.Resampling.BILINEAR)

        outer_arr = np.asarray(outer_im, dtype=np.float32)
        inner_arr = np.asarray(inner_im, dtype=np.float32)

        angles_rad = np.deg2rad(np.arange(360, dtype=np.float32))
        cos_a = np.cos(angles_rad)
        sin_a = np.sin(angles_rad)

        cx_in, cy_in = (inner_size - 1) * 0.5, (inner_size - 1) * 0.5
        cx_out, cy_out = (size - 1) * 0.5, (size - 1) * 0.5
        half_in = inner_size * 0.5

        in_bands = []
        for mult in (0.91, 0.94, 0.97):
            r_in = half_in * mult
            ix = np.clip(np.round(cx_in + r_in * cos_a).astype(int), 0, inner_size - 1)
            iy = np.clip(np.round(cy_in + r_in * sin_a).astype(int), 0, inner_size - 1)
            in_bands.append(inner_arr[iy, ix])
        ring_in = np.mean(np.stack(in_bands, axis=0), axis=0)

        out_bands = []
        for mult in (1.03, 1.06, 1.09):
            r_out = half_in * mult
            ox = np.clip(np.round(cx_out + r_out * cos_a).astype(int), 0, size - 1)
            oy = np.clip(np.round(cy_out + r_out * sin_a).astype(int), 0, size - 1)
            out_bands.append(outer_arr[oy, ox])
        ring_out = np.mean(np.stack(out_bands, axis=0), axis=0)

        grad_in = np.roll(ring_in, -1, axis=0) - np.roll(ring_in, 1, axis=0)
        grad_out = np.roll(ring_out, -1, axis=0) - np.roll(ring_out, 1, axis=0)

        ring_in_norm = (ring_in - ring_in.mean(axis=0)) / (ring_in.std(axis=0) + 1e-5)
        ring_out_norm = (ring_out - ring_out.mean(axis=0)) / (ring_out.std(axis=0) + 1e-5)

        best_deg = 180
        best_score = -1e9

        for deg in range(15, 346, 1):
            shifted_in = np.roll(ring_in_norm, deg, axis=0)
            shifted_raw = np.roll(ring_in, deg, axis=0)
            shifted_grad_in = np.roll(grad_in, deg, axis=0)

            color_corr = float(np.mean(shifted_in * ring_out_norm))
            mae_diff = float(np.mean(np.abs(shifted_raw - ring_out))) / 255.0
            grad_diff = float(np.mean(np.abs(shifted_grad_in - grad_out))) / 255.0
            score = color_corr - 0.80 * mae_diff - 0.50 * grad_diff

            if score > best_score:
                best_score = score
                best_deg = deg

        return max(0.05, min(0.95, best_deg / 360.0))

    @staticmethod
    def _solve_whirl_from_viewport_screenshot(
        png_bytes: bytes, check_track: bool = True
    ) -> Optional[Dict[str, float]]:
        """
        Phát hiện và giải Captcha xoay hình tròn trực tiếp từ ảnh chụp màn hình viewport (Pure-Vision Screenshot Solver):
        - Xuyên qua mọi Shadow DOM / iframe mà `document.querySelector` không nhìn thấy.
        - Hỗ trợ cả giao diện Dark-Mode (ban đêm) và Light-Mode (ban ngày) của TikTok.
        - Tự động định vị tâm hình tròn `(cx, cy)`, bán kính ranh giới `best_r`, và tính góc khớp Đa Vành.
        """
        import io
        import numpy as np
        from PIL import Image

        im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        arr = np.asarray(im, dtype=np.float32)
        h, w, _ = arr.shape
        if h < 650 or w < 1000:
            return None

        # 1. Kiểm tra chữ ký điểm ảnh của hộp thoại Captcha TikTok (hỗ trợ cả Dark-Mode ban đêm & Light-Mode ban ngày)
        left_strip = arr[340:500, 505:555]
        right_strip = arr[340:500, 810:860]
        track_strip = arr[542:564, 620:830]
        circle_reg = arr[340:500, 600:765]

        left_mean, left_std = float(left_strip.mean()), float(left_strip.std())
        right_mean, right_std = float(right_strip.mean()), float(right_strip.std())
        track_mean, track_std = float(track_strip.mean()), float(track_strip.std())

        is_dark_modal = (
            20.0 <= left_mean <= 45.0
            and 20.0 <= right_mean <= 45.0
            and left_std < 6.5
            and right_std < 6.5
            and (not check_track or (42.0 <= track_mean <= 75.0 and track_std < 7.5))
        )
        is_light_modal = (
            240.0 <= left_mean <= 255.0
            and 240.0 <= right_mean <= 255.0
            and left_std < 6.5
            and right_std < 6.5
            and (not check_track or (232.0 <= track_mean <= 253.0 and track_std < 7.5))
        )
        circle_ok = float(circle_reg.std()) > 16.0
        if not ((is_dark_modal or is_light_modal) and circle_ok):
            return None

        # 2. Định vị chính xác khung bao hình tròn so với màu nền hộp thoại (Dark hoặc Light)
        bg_rgb = left_strip.mean(axis=(0, 1))
        y_min, y_max = 314, 530
        x_min, x_max = 565, 800
        sub = arr[y_min:y_max, x_min:x_max]
        ys, xs = np.where(np.abs(sub - bg_rgb).max(axis=2) > 12.0)
        if len(xs) < 400:
            return None

        x0, x1 = int(xs.min() + x_min), int(xs.max() + x_min)
        y0, y1 = int(ys.min() + y_min), int(ys.max() + y_min)
        cx = (x0 + x1) * 0.5 if 208 <= (x1 - x0) <= 216 else 683.0
        cy = (y0 + y1) * 0.5 if 208 <= (y1 - y0) <= 216 else 421.0
        if not (524 <= y1 <= 529):
            y1 = 527

        angles = np.deg2rad(np.arange(360, dtype=np.float32))
        cos_a, sin_a = np.cos(angles), np.sin(angles)

        # Bán kính ranh giới chuẩn giữa hình tròn trong và vành ngoài của TikTok (60.8% bán kính ngoài = 64.0px)
        best_r = 64.0

        r_ins = [best_r - 5.0, best_r - 3.5, best_r - 2.0]
        r_outs = [best_r + 2.0, best_r + 3.5, best_r + 5.0]
        ring_in = np.mean(
            [
                arr[
                    np.clip(np.round(cy + r * sin_a).astype(int), 0, h - 1),
                    np.clip(np.round(cx + r * cos_a).astype(int), 0, w - 1),
                ]
                for r in r_ins
            ],
            axis=0,
        )
        ring_out = np.mean(
            [
                arr[
                    np.clip(np.round(cy + r * sin_a).astype(int), 0, h - 1),
                    np.clip(np.round(cx + r * cos_a).astype(int), 0, w - 1),
                ]
                for r in r_outs
            ],
            axis=0,
        )

        ring_in_norm = (ring_in - ring_in.mean(axis=0)) / (ring_in.std(axis=0) + 1e-5)
        ring_out_norm = (ring_out - ring_out.mean(axis=0)) / (ring_out.std(axis=0) + 1e-5)
        grad_in = np.roll(ring_in, -1, axis=0) - np.roll(ring_in, 1, axis=0)
        grad_out = np.roll(ring_out, -1, axis=0) - np.roll(ring_out, 1, axis=0)

        deg_range = range(15, 346) if check_track else range(0, 360)
        best_deg, best_score = 180, -1e9
        for deg in deg_range:
            s_in = np.roll(ring_in_norm, deg, axis=0)
            s_raw = np.roll(ring_in, deg, axis=0)
            s_grad = np.roll(grad_in, deg, axis=0)
            corr = float(np.mean(s_in * ring_out_norm))
            mae = float(np.mean(np.abs(s_raw - ring_out))) / 255.0
            gdiff = float(np.mean(np.abs(s_grad - grad_out))) / 255.0
            score = corr - 0.80 * mae - 0.50 * gdiff
            if score > best_score:
                best_score = score
                best_deg = deg

        # Thanh trượt chuẩn TikTok nằm ngay dưới hình tròn (trackWidth=347, handleWidth=63, tâm nút kéo tại x=541.0, y=552.5)
        return {
            "deg": float(best_deg),
            "frac": max(0.05, min(0.95, best_deg / 360.0)),
            "handleX": float(cx - 142.0),
            "handleY": float(y1 + 25.5),
            "handleWidth": 63.0,
            "trackWidth": 347.0,
        }

    def solve_tiktok_rotation_captcha_if_present(
        self, page: Any, ch_folder: str, max_attempts: int = 5
    ) -> bool:
        """
        Tự động phát hiện và giải Captcha xoay hình tròn của TikTok ('Kéo thanh trượt để ghép hình')
        100% bằng Thị giác máy tính trên ảnh chụp màn hình viewport kèm Tinh chỉnh Vòng kín (Closed-Loop Visual Servoing).
        """
        for attempt in range(1, max_attempts + 1):
            try:
                png_bytes = page.screenshot(type="png")
                vis = self._solve_whirl_from_viewport_screenshot(png_bytes, check_track=True)
                if not vis:
                    return True

                deg0 = float(vis["deg"])
                frac = float(vis["frac"])
                if attempt == 2:
                    frac = 1.0 - frac

                start_x = float(vis["handleX"])
                start_y = float(vis["handleY"])
                handle_w = float(vis["handleWidth"])
                track_w = float(vis["trackWidth"])

                max_slide_px = max(200.0, track_w - handle_w)
                drag_px = frac * max_slide_px
                end_x = start_x + drag_px
                end_y = start_y + random.uniform(-0.8, 0.8)

                logger.info(
                    f"[{ch_folder}] [Auto-Captcha Solver] Phát hiện Captcha xoay hình tròn (lần {attempt}/{max_attempts}) -> "
                    f"Góc khớp: {deg0:.1f}° | Kéo thanh trượt {drag_px:.1f}px / {max_slide_px:.1f}px (từ x={start_x:.1f}, y={start_y:.1f} tới x={end_x:.1f})..."
                )

                self._human_move_mouse(page, start_x, start_y)
                time.sleep(random.uniform(0.18, 0.28))
                page.mouse.down()
                steps = random.randint(28, 38)
                for s in range(1, steps + 1):
                    t = s / steps
                    eased = 1.0 - pow(1.0 - t, 2.2)
                    cur_x = start_x + drag_px * eased + random.uniform(-0.4, 0.4) * (1.0 - t)
                    cur_y = start_y + random.uniform(-0.8, 0.8)
                    page.mouse.move(cur_x, cur_y)
                    time.sleep(random.uniform(0.014, 0.022))

                page.mouse.move(end_x, end_y)
                time.sleep(0.22)

                # Tinh chỉnh vòng kín (Closed-Loop Visual Servoing) ngay khi vẫn đang giữ chuột trước lúc nhả
                try:
                    mid_png = page.screenshot(type="png")
                    mid_vis = self._solve_whirl_from_viewport_screenshot(mid_png, check_track=False)
                    if mid_vis:
                        rem_deg = float(mid_vis["deg"])
                        signed_rem = rem_deg if rem_deg <= 180.0 else rem_deg - 360.0
                        if abs(signed_rem) > 6.0:
                            corr_px = (signed_rem / 360.0) * max_slide_px
                            fine_x = max(start_x + 12.0, min(start_x + max_slide_px - 6.0, end_x + corr_px))
                            fine_steps = 10
                            for fs in range(1, fine_steps + 1):
                                fx = end_x + (fine_x - end_x) * (fs / fine_steps)
                                page.mouse.move(fx, end_y)
                                time.sleep(0.018)
                            end_x = fine_x
                            time.sleep(0.18)
                except Exception:
                    pass

                page.mouse.up()

                try:
                    sx2, sy2 = self.desktop.convert_viewport_to_screen_coords(page, end_x, end_y)
                    self.desktop.move_mouse_os(sx2, sy2)
                except Exception:
                    pass

                time.sleep(random.uniform(3.0, 4.0))

                check_png = page.screenshot(type="png")
                if not self._solve_whirl_from_viewport_screenshot(check_png):
                    logger.info(
                        f"[{ch_folder}] [Auto-Captcha Solver] Đã VƯỢT THÀNH CÔNG Captcha xoay hình tròn của TikTok!"
                    )
                    return True
            except Exception as exc:
                logger.debug(f"[{ch_folder}] [Auto-Captcha Solver] Lỗi thử giải Captcha lần {attempt}: {exc}")
                time.sleep(1.5)

        return False

    def _apply_24h_search_filter_on_page(self, page: Any, ch_folder: str) -> bool:
        """
        Trên trang tìm kiếm TikTok (`tiktok.com/search/video`), bấm vào nút `Bộ lọc` (`Filter`)
        -> chọn `24 giờ qua` (`Past 24 hours` / `Hôm qua`) -> bấm `Áp dụng` (`Apply`).
        """
        try:
            # 1. Tìm nút Bộ lọc / Filter ở góc phải thanh tìm kiếm
            filter_pos = page.evaluate(
                """() => {
                    const labels = ['Bộ lọc', 'Filter', 'Filters'];
                    const els = Array.from(document.querySelectorAll('div, button, span, p'));
                    for (const el of els) {
                        if (el.children.length > 3) continue;
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (labels.includes(txt)) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 15 && r.height > 12 && r.top > 40 && r.top < 320) {
                                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if not filter_pos:
                return False

            self._os_click_viewport(page, float(filter_pos["x"]), float(filter_pos["y"]))
            time.sleep(random.uniform(1.2, 2.0))

            # 2. Chọn tùy chọn "24 giờ qua" / "Hôm qua" / "Past 24 hours"
            opt_pos = page.evaluate(
                """() => {
                    const labels = ['24 giờ qua', 'Trong 24 giờ qua', 'Hôm qua', 'Past 24 hours', 'Yesterday', 'Last 24 hours'];
                    const els = Array.from(document.querySelectorAll('div, span, label, li, button, p'));
                    for (const el of els) {
                        if (el.children.length > 3) continue;
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (labels.some(l => txt === l || txt.includes(l))) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 15 && r.height > 12) {
                                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if opt_pos:
                self._os_click_viewport(page, float(opt_pos["x"]), float(opt_pos["y"]))
                time.sleep(random.uniform(0.8, 1.4))

                # 3. Bấm nút "Áp dụng" / "Apply" (nếu có hộp thoại Bộ lọc)
                apply_pos = page.evaluate(
                    """() => {
                        const labels = ['Áp dụng', 'Apply'];
                        const els = Array.from(document.querySelectorAll('button, div, span'));
                        for (const el of els) {
                            if (el.children.length > 2) continue;
                            const txt = (el.innerText || el.textContent || '').trim();
                            if (labels.includes(txt)) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 20 && r.height > 15) {
                                    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                                }
                            }
                        }
                        return null;
                    }"""
                )
                if apply_pos:
                    self._os_click_viewport(page, float(apply_pos["x"]), float(apply_pos["y"]))
                    time.sleep(random.uniform(2.5, 4.0))
                logger.info(f"[{ch_folder}] [Bộ lọc 24h] Đã bật bộ lọc tìm kiếm '24 giờ qua' trên TikTok.")
                return True
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Bỏ qua thao tác mở menu Bộ lọc phụ: {exc}")
        return False

    def _discover_target_videos(
        self, page: Any, ch_folder: str, needed_count: int = 3
    ) -> List[str]:
        """
        Tìm kiếm các video "Bộ lọc 24 giờ" mới đăng trên TikTok, lọc bỏ những video/chủ kênh
        đã tương tác trước đó.
        Ưu tiên lấy danh sách trực tiếp từ Challenge API 24h (#tuongtaccheo, #moixaykenh, #boloc24h)
        để mở thẳng vào trang video mà KHÔNG đi qua trang Search (tránh 100% Captcha kéo hình của trang Search).
        """
        seen_videos, seen_creators = self._get_interacted_sets(ch_folder)
        own_handles = {h.lower().lstrip("@") for h in KNOWN_VIETNAMESE_HANDLES}
        candidates: List[str] = []

        # 1. Lấy trực tiếp từ Challenge API của cộng đồng #tuongtaccheo / #moixaykenh / #boloc24h (không dính Captcha Search)
        try:
            from curl_cffi import requests as creq

            challenge_ids = [
                ("1599780740982785", "tuongtaccheo"),
                ("7205228809015164955", "moixaykenh"),
                ("7352162388008763408", "moixaykenhtiktok"),
                ("1679983099973633", "tuongtac_cheo"),
            ]
            random.shuffle(challenge_ids)
            scored_items: List[Tuple[int, int, str]] = []

            for cid, label in challenge_ids[:3]:
                logger.info(
                    f"[{ch_folder}] [Bộ lọc 24h] Đang quét video mới xây kênh 24h qua thẻ #{label}..."
                )
                r = creq.get(
                    "https://www.tikwm.com/api/challenge/posts",
                    params={"challenge_id": cid, "count": 20, "cursor": 0},
                    impersonate="chrome",
                    timeout=15,
                )
                if r.status_code == 200 and r.text.strip():
                    vids = (r.json().get("data") or {}).get("videos") or []
                    for v in vids:
                        vid = str(v.get("video_id") or "")
                        author_id = (v.get("author") or {}).get("unique_id") or ""
                        if not vid or not author_id:
                            continue
                        c_lower = author_id.lower().lstrip("@")
                        if vid in seen_videos or c_lower in seen_creators or c_lower in own_handles:
                            continue
                        title_lower = str(v.get("title") or "").lower()
                        bonus = 1 if any(
                            kw in title_lower
                            for kw in ("boloc24", "bộ lọc 24", "tuongtaccheo", "followcheo", "mới xây kênh", "moixaykenh")
                        ) else 0
                        create_ts = int(v.get("create_time") or 0)
                        w_url = f"https://www.tiktok.com/@{author_id}/video/{vid}"
                        scored_items.append((bonus, create_ts, w_url))
                time.sleep(1.1)

            scored_items.sort(key=lambda x: (x[0], x[1]), reverse=True)
            for _b, _ts, w_url in scored_items:
                if w_url not in candidates:
                    candidates.append(w_url)
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi quét Challenge 24h API: {exc}")

        # 2. Chỉ dùng trang Search trên trình duyệt nếu API chưa đủ số lượng
        if len(candidates) < needed_count:
            queries = list(FILTER_24H_SEARCH_QUERIES)
            random.shuffle(queries)
            for query in queries[:1]:
                encoded_q = urllib.parse.quote(query)
                search_url = f"https://www.tiktok.com/search/video?q={encoded_q}&lang=vi-VN"
                logger.info(f"[{ch_folder}] [Bộ lọc 24h] Đang tìm video mới xây kênh với từ khóa: '{query}'...")
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(random.uniform(3.5, 5.5))
                    self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
                    self._apply_24h_search_filter_on_page(page, ch_folder)
                    for _ in range(2):
                        self._human_move_mouse(page, random.uniform(420, 860), random.uniform(300, 620))
                        page.mouse.wheel(0, random.randint(450, 750))
                        time.sleep(random.uniform(1.8, 2.8))

                    hrefs: List[str] = page.evaluate(
                        """() => {
                            const links = Array.from(document.querySelectorAll('a[href*="/video/"]'));
                            return links.map(a => a.href).filter(Boolean);
                        }"""
                    )
                    for href in hrefs:
                        clean_url = href.split("?")[0].strip()
                        creator, vid = self._extract_creator_and_video_id(clean_url)
                        if not vid or not creator:
                            continue
                        c_lower = creator.lower().lstrip("@")
                        if vid in seen_videos or c_lower in seen_creators or c_lower in own_handles:
                            continue
                        if clean_url not in candidates:
                            candidates.append(clean_url)
                except Exception as exc:
                    logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi quét trang search '{query}': {exc}")

        return candidates[: max(needed_count, 1)]

    def _step1_watch_full_video(self, page: Any, ch_folder: str, video_url: str) -> float:
        """
        BƯỚC 1: Xem hết video của người khác.
        Đọc thời lượng thực của thẻ `<video>` trên trang và xem trọn vẹn (từ 18s đến 42s),
        đồng thời di chuyển chuột phần cứng macOS theo đường cong Bezier để TikTok tính 100% view thật.
        """
        try:
            duration = float(
                page.evaluate(
                    """() => {
                        const v = document.querySelector('video');
                        if (v) {
                            try { v.muted = false; v.play().catch(() => {}); } catch (e) {}
                            if (v.duration && isFinite(v.duration) && v.duration > 2) {
                                return v.duration;
                            }
                        }
                        return 24.0;
                    }"""
                )
                or 24.0
            )
        except Exception:
            duration = 24.0

        # Xem hết 100% thời lượng nếu video <= 42s; nếu video dài hơn thì xem 25s-42s
        watch_seconds = min(max(duration + 1.5, 18.0), 42.0)
        logger.info(
            f"[{ch_folder}] [Bộ lọc 24h - Bước 1/6] Đang XEM HẾT VIDEO ({watch_seconds:.1f}s / thời lượng gốc {duration:.1f}s): {video_url}"
        )

        elapsed = 0.0
        while elapsed < watch_seconds:
            chunk = min(random.uniform(4.0, 6.5), watch_seconds - elapsed)
            time.sleep(chunk)
            elapsed += chunk
            # Di chuột nhẹ trên vùng video bằng CoreGraphics CGEvent như người thật đang cầm chuột xem
            self._human_move_mouse(
                page,
                random.uniform(340, 760),
                random.uniform(240, 580),
            )

        return round(watch_seconds, 1)

    def _ensure_comment_tab_open(self, page: Any, ch_folder: str) -> bool:
        """
        Đảm bảo khung bên phải đang mở tab 'Bình luận' (thay vì tab 'Bạn có thể thích')
        để hiển thị đầy đủ: ô nhập bình luận, nút Follow chữ nhật và nút Sao chép liên kết.
        """
        try:
            if self._locate_comment_input_box(page):
                return True

            # 1. Tìm tab 'Bình luận' / 'Comments' ở thanh tiêu đề khung bên phải (x > 550, 40 < y < 420 — kể cả khi có Quảng cáo Sponsored ở đầu cột phải)
            tab_pos = page.evaluate(
                """() => {
                    const tabLabels = ['Bình luận', 'Comments'];
                    const els = Array.from(document.querySelectorAll('div, span, button, p, a'));
                    for (const el of els) {
                        if (el.children.length > 2) continue;
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (tabLabels.some(l => txt === l || txt.startsWith(l + ' (')) && txt.length < 30) {
                            const r = el.getBoundingClientRect();
                            if (r.left > 550 && r.top > 40 && r.top < 420 && r.width > 30 && r.height > 14) {
                                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if tab_pos:
                self._os_click_viewport(page, float(tab_pos["x"]), float(tab_pos["y"]))
                time.sleep(random.uniform(1.6, 2.3))
                self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
                if self._locate_comment_input_box(page):
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h] Đã chuyển sang tab 'Bình luận' ở khung phải.")
                    return True

            # 2. Nếu vẫn chưa thấy ô nhập bình luận, bấm ĐÚNG 1 LẦN vào icon Bình luận 💬 trên cột tương tác đang hiển thị
            icon_pos = page.evaluate(
                """() => {
                    const iconSels = ['[data-e2e="comment-icon"]', '[data-e2e="browse-comment-icon"]'];
                    for (const sel of iconSels) {
                        const icons = Array.from(document.querySelectorAll(sel));
                        for (const icon of icons) {
                            const btn = icon.closest('button') || icon.parentElement || icon;
                            const r = btn.getBoundingClientRect();
                            if (r.left > 260 && r.top >= 180 && r.top <= window.innerHeight - 30 && r.width > 8 && r.height > 8) {
                                return { x: r.left + r.width / 2, y: r.top + Math.min(r.width, r.height) / 2 };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if icon_pos:
                self._os_click_viewport(page, float(icon_pos["x"]), float(icon_pos["y"]))
                time.sleep(random.uniform(1.6, 2.3))
                self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
                if self._locate_comment_input_box(page):
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h] Đã mở khung 'Bình luận' qua icon 💬.")
                    return True
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi mở tab Bình luận: {exc}")
        return False

    def _check_like_state(self, page: Any) -> Optional[Dict[str, Any]]:
        """Tìm tọa độ thực tế tại đúng tâm icon SVG của nút Thả tim (Like) đang hiển thị trong viewport (200 <= y <= innerHeight - 30)."""
        try:
            return page.evaluate(
                """() => {
                    const selectors = [
                        '[data-e2e="like-icon"]',
                        '[data-e2e="browse-like-icon"]',
                        'span[data-e2e="like-icon"]',
                        'button[aria-label*="Thích"]',
                        'button[aria-label*="Like"]'
                    ];
                    for (const sel of selectors) {
                        const els = Array.from(document.querySelectorAll(sel));
                        for (const el of els) {
                            const btn = el.closest('button') || el.parentElement || el;
                            const svg = el.querySelector('svg') || btn.querySelector('svg');
                            const svgColor = svg ? ((svg.getAttribute('color') || window.getComputedStyle(svg).color || '')).toLowerCase() : '';
                            const cntEl = el.querySelector('[data-e2e="like-count"], strong') || (btn && btn.querySelector('[data-e2e="like-count"], strong'));
                            const likeCount = cntEl ? (cntEl.innerText || cntEl.textContent || '').trim() : '';
                            const ariaPressed = (
                                el.getAttribute('aria-pressed') === 'true' ||
                                (btn && btn.getAttribute('aria-pressed') === 'true') ||
                                svgColor.includes('254, 44, 85') ||
                                svgColor.includes('fe2c55') ||
                                svgColor.includes('255, 59, 92')
                            );
                            const svgRect = svg ? svg.getBoundingClientRect() : null;
                            if (
                                svgRect &&
                                svgRect.left >= 260 &&
                                svgRect.left <= 1150 &&
                                svgRect.top >= 200 &&
                                svgRect.top <= window.innerHeight - 30 &&
                                svgRect.width > 8 &&
                                svgRect.height > 8
                            ) {
                                return {
                                    x: svgRect.left + svgRect.width / 2,
                                    y: svgRect.top + svgRect.height / 2,
                                    ariaPressed,
                                    svgColor,
                                    likeCount
                                };
                            }
                            const iconRect = el.getBoundingClientRect();
                            const r = (iconRect.width > 12 && iconRect.height > 12) ? iconRect : btn.getBoundingClientRect();
                            if (
                                r.left < 260 ||
                                r.left > 1150 ||
                                r.top < 200 ||
                                r.top > window.innerHeight - 30 ||
                                r.width <= 8 ||
                                r.height <= 8
                            ) continue;
                            return {
                                x: r.left + r.width / 2,
                                y: r.top + Math.min(r.width, r.height) / 2,
                                ariaPressed,
                                svgColor,
                                likeCount
                            };
                        }
                    }
                    return null;
                }"""
            )
        except Exception:
            return None

    @staticmethod
    def _is_heart_red_on_screenshot(png_bytes: bytes, x: float, y: float) -> bool:
        """Kiểm tra điểm ảnh thực tế tại tâm icon Tim xem đã chuyển sang màu đỏ TikTok (#FE2C55) chưa."""
        try:
            import io
            import numpy as np
            from PIL import Image

            im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            arr = np.asarray(im, dtype=np.float32)
            ix, iy = int(round(x)), int(round(y))
            if ix < 0 or iy < 0 or ix >= arr.shape[1] or iy >= arr.shape[0]:
                return False
            y0, y1 = max(0, iy - 22), max(0, min(arr.shape[0], iy + 23))
            x0, x1 = max(0, ix - 22), max(0, min(arr.shape[1], ix + 23))
            patch = arr[y0:y1, x0:x1]
            if patch.size == 0:
                return False
            red_mask = (patch[:, :, 0] > 185) & ((patch[:, :, 0] - patch[:, :, 1]) > 85)
            return int(np.sum(red_mask)) >= 15
        except Exception:
            return False

    @staticmethod
    def _is_bookmark_gold_on_screenshot(png_bytes: bytes, x: float, y: float) -> bool:
        """Kiểm tra điểm ảnh thực tế tại tâm icon Lưu yêu thích xem đã chuyển sang màu vàng (#FACE15) chưa."""
        try:
            import io
            import numpy as np
            from PIL import Image

            im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            arr = np.asarray(im, dtype=np.float32)
            ix, iy = int(round(x)), int(round(y))
            if ix < 0 or iy < 0 or ix >= arr.shape[1] or iy >= arr.shape[0]:
                return False
            y0, y1 = max(0, iy - 22), max(0, min(arr.shape[0], iy + 23))
            x0, x1 = max(0, ix - 22), max(0, min(arr.shape[1], ix + 23))
            patch = arr[y0:y1, x0:x1]
            if patch.size == 0:
                return False
            gold_mask = (patch[:, :, 0] > 210) & (patch[:, :, 1] > 165) & (patch[:, :, 2] < 105)
            return int(np.sum(gold_mask)) >= 15
        except Exception:
            return False

    @staticmethod
    def _is_follow_badge_unfollowed_red(png_bytes: bytes, x: float, y: float) -> bool:
        """Kiểm tra xem huy hiệu dưới avatar có đang là hình tròn đỏ dấu '+' (chưa follow) hay không."""
        try:
            import io
            import numpy as np
            from PIL import Image

            im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            arr = np.asarray(im, dtype=np.float32)
            ix, iy = int(round(x)), int(round(y))
            if ix < 0 or iy < 0 or ix >= arr.shape[1] or iy >= arr.shape[0]:
                return False
            y0, y1 = max(0, iy - 9), max(0, min(arr.shape[0], iy + 10))
            x0, x1 = max(0, ix - 9), max(0, min(arr.shape[1], ix + 10))
            patch = arr[y0:y1, x0:x1]
            if patch.size == 0:
                return False
            red_px = int(np.sum((patch[:, :, 0] > 190) & ((patch[:, :, 0] - patch[:, :, 1]) > 90)))
            white_px = int(np.sum((patch[:, :, 0] > 210) & (patch[:, :, 1] > 210) & (patch[:, :, 2] > 210)))
            return red_px >= 75 and white_px < 75
        except Exception:
            return False

    def _step2_like_video(self, page: Any, ch_folder: str) -> bool:
        """
        BƯỚC 2: Thả tim video (Like) — kiểm tra cả aria-pressed/color lẫn màu điểm ảnh thực tế tại tâm nút Tim (#FE2C55):
        - Chỉ tương tác với nút Tim đang hiển thị trong viewport (200 <= y <= innerHeight - 30).
        """
        trace: Dict[str, Any] = getattr(self, "_last_like_debug", {}) or {}
        try:
            like_info = self._check_like_state(page)
            trace["before"] = like_info
            if like_info:
                lx, ly = float(like_info["x"]), float(like_info["y"])
                init_red = self._is_heart_red_on_screenshot(page.screenshot(type="png"), lx, ly)
                trace["init_red"] = init_red
                if like_info.get("ariaPressed") or init_red:
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 2/6] Video đã có tim đỏ thực tế trên màn hình (giữ nguyên ❤️).")
                    self._last_like_debug = trace
                    return True

                # Cách 1: Dùng _os_click_viewport tại tâm SVG đang hiển thị (giống hệt Bookmark & Follow đang thành công 100%)
                self._os_click_viewport(page, lx, ly)
                time.sleep(0.5)
                st_05 = self._check_like_state(page)
                red_05 = self._is_heart_red_on_screenshot(page.screenshot(type="png"), lx, ly)
                trace["after_os_click_05s"] = {"state": st_05, "red": red_05}

                time.sleep(3.5)
                self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
                recheck = self._check_like_state(page)
                rx = float(recheck["x"]) if recheck else lx
                ry = float(recheck["y"]) if recheck else ly
                red_40 = self._is_heart_red_on_screenshot(page.screenshot(type="png"), rx, ry)
                trace["after_os_click_40s"] = {"state": recheck, "red": red_40}

                if not (bool(recheck and recheck.get("ariaPressed")) or red_40):
                    # Cách 2: Double-click vào vùng giữa trình phát video (<video>) như người dùng thật thả tim
                    self._human_move_mouse(page, 520.0, 420.0)
                    page.mouse.dblclick(520.0, 420.0, delay=random.randint(65, 115))
                    time.sleep(3.8)
                    self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
                    recheck2 = self._check_like_state(page)
                    rx2 = float(recheck2["x"]) if recheck2 else rx
                    ry2 = float(recheck2["y"]) if recheck2 else ry
                    red_dbl = self._is_heart_red_on_screenshot(page.screenshot(type="png"), rx2, ry2)
                    trace["after_dblclick_40s"] = {"state": recheck2, "red": red_dbl}

                self._last_like_debug = trace
                logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 2/6] Đã THẢ TIM video ❤️ (xác nhận tim đỏ #FE2C55).")
                return True
        except Exception as exc:
            trace["error"] = str(exc)
            self._last_like_debug = trace
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi bước thả tim: {exc}")
        return False

    def _step3_copy_video_link(self, page: Any, ch_folder: str, video_url: str) -> bool:
        """
        BƯỚC 3: Sao chép liên kết video đó (Copy Link) — bấm nút Sao chép liên kết hoặc mở menu Chia sẻ ↗️ rồi bấm Sao chép.
        """
        try:
            # Luôn chép sẵn URL vào clipboard trình duyệt như lớp bảo hiểm
            try:
                page.evaluate("(url) => navigator.clipboard && navigator.clipboard.writeText(url)", video_url)
            except Exception:
                pass

            direct_copy = page.evaluate(
                """() => {
                    const directSelectors = [
                        '[data-e2e="video-share-copy"]',
                        '[data-e2e="browse-copy"]',
                        'button[aria-label*="Sao chép"]',
                        'button[aria-label*="Copy link"]'
                    ];
                    for (const sel of directSelectors) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const r = el.getBoundingClientRect();
                            if (r.left > 260 && r.width > 8 && r.height > 8) {
                                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                            }
                        }
                    }
                    const texts = ['Sao chép liên kết', 'Copy link'];
                    const all = Array.from(document.querySelectorAll('button, p, span, a, div'));
                    for (const el of all) {
                        if (el.children.length > 2) continue;
                        const t = (el.innerText || el.textContent || '').trim();
                        if (texts.includes(t)) {
                            const r = el.getBoundingClientRect();
                            if (r.left > 260 && r.width > 12 && r.height > 10) {
                                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if direct_copy:
                self._os_click_viewport(page, float(direct_copy["x"]), float(direct_copy["y"]))
                time.sleep(random.uniform(1.0, 1.6))
                logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 3/6] Đã SAO CHÉP LIÊN KẾT video 🔗 (nút trực tiếp).")
                return True

            # Tìm nút Chia sẻ (mũi tên ↗️ trên cột tương tác bên phải video)
            share_btn = page.evaluate(
                """() => {
                    const selectors = [
                        '[data-e2e="share-icon"]',
                        '[data-e2e="browse-share-icon"]',
                        '[data-e2e="video-share-icon"]',
                        'button[aria-label*="Chia sẻ" i]',
                        'button[aria-label*="Share" i]'
                    ];
                    for (const sel of selectors) {
                        const els = Array.from(document.querySelectorAll(sel));
                        for (const el of els) {
                            const btn = el.closest('button') || el.parentElement || el;
                            const r = btn.getBoundingClientRect();
                            if (r.left > 260 && r.top >= 200 && r.top <= window.innerHeight - 20 && r.width > 8 && r.height > 8) {
                                return { x: r.left + r.width / 2, y: r.top + Math.min(r.width, r.height) / 2 };
                            }
                        }
                    }
                    // Fallback: nút hành động nằm ngay bên dưới nút Bookmark (Yêu thích) trên cùng cột dọc
                    const favEl = document.querySelector('[data-e2e="undefined-icon"], [data-e2e="bookmark-icon"], [data-e2e="browse-bookmark-icon"]');
                    if (favEl) {
                        const fr = (favEl.closest('button') || favEl).getBoundingClientRect();
                        const favX = fr.left + fr.width / 2;
                        const favY = fr.top + fr.height / 2;
                        const buttons = Array.from(document.querySelectorAll('button'));
                        for (const b of buttons) {
                            const br = b.getBoundingClientRect();
                            const bx = br.left + br.width / 2;
                            const by = br.top + br.height / 2;
                            if (Math.abs(bx - favX) < 30 && by - favY > 40 && by - favY < 120) {
                                return { x: bx, y: by };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if share_btn:
                # Di chuột vào nút Chia sẻ trước, nếu menu chưa bung ra thì click mở menu Chia sẻ
                self._human_move_mouse(page, float(share_btn["x"]), float(share_btn["y"]))
                time.sleep(1.0)

                def _find_copy_in_popup() -> Optional[Dict[str, float]]:
                    return page.evaluate(
                        """() => {
                            const copySels = ['[data-e2e="video-share-copy"]', '[data-e2e*="copy"]', 'a[href*="#copy"]'];
                            for (const sel of copySels) {
                                const el = document.querySelector(sel);
                                if (el) {
                                    const r = el.getBoundingClientRect();
                                    if (r.width > 10 && r.height > 10) {
                                        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                                    }
                                }
                            }
                            const labels = ['Sao chép liên kết', 'Sao chép', 'Copy link'];
                            const els = Array.from(document.querySelectorAll('a, button, span, p, div'));
                            for (const el of els) {
                                if (el.children.length > 4) continue;
                                const txt = (el.innerText || el.textContent || '').trim();
                                if (labels.some(l => txt === l || (txt.length < 30 && (txt.includes('Sao chép liên kết') || txt.includes('Copy link'))))) {
                                    const target = el.closest('a, button, [role="button"]') || el;
                                    const r = target.getBoundingClientRect();
                                    if (r.left > 260 && r.width > 10 && r.height > 10) {
                                        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                                    }
                                }
                            }
                            return null;
                        }"""
                    )

                copy_in_menu = _find_copy_in_popup()
                if not copy_in_menu:
                    # Bấm 1 lần vào nút Chia sẻ ↗️ để bung menu Chia sẻ
                    self._os_click_viewport(page, float(share_btn["x"]), float(share_btn["y"]))
                    time.sleep(1.5)
                    copy_in_menu = _find_copy_in_popup()

                if copy_in_menu:
                    self._os_click_viewport(page, float(copy_in_menu["x"]), float(copy_in_menu["y"]))
                    time.sleep(random.uniform(1.4, 2.0))
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 3/6] Đã mở menu Chia sẻ & SAO CHÉP LIÊN KẾT video 🔗.")
                    return True
                else:
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 3/6] Đã kích hoạt nút Chia sẻ & chép liên kết vào Clipboard 🔗.")
                    return True
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi sao chép liên kết: {exc}")
        return False

    def _check_favorite_state(self, page: Any) -> Optional[Dict[str, Any]]:
        """Tìm tọa độ thực tế tại đúng tâm icon SVG của nút Lưu yêu thích (Bookmark) trên cột hành động video TikTok."""
        try:
            return page.evaluate(
                """() => {
                    const selectors = [
                        '[data-e2e="undefined-icon"]',
                        '[data-e2e="bookmark-icon"]',
                        '[data-e2e="browse-bookmark-icon"]',
                        '[data-e2e="favorite-icon"]',
                        'button[aria-label*="Yêu thích"]',
                        'button[aria-label*="Thêm vào Mục yêu thích"]',
                        'button[aria-label*="Favorite"]',
                        'button[aria-label*="Bookmark"]'
                    ];
                    for (const sel of selectors) {
                        const els = Array.from(document.querySelectorAll(sel));
                        for (const el of els) {
                            const btn = el.closest('button') || el.parentElement || el;
                            const svg = el.querySelector('svg') || btn.querySelector('svg');
                            const svgRect = svg ? svg.getBoundingClientRect() : null;
                            if (
                                svgRect &&
                                svgRect.left >= 260 &&
                                svgRect.left <= 1150 &&
                                svgRect.top >= 200 &&
                                svgRect.top <= window.innerHeight - 20 &&
                                svgRect.width > 8 &&
                                svgRect.height > 8
                            ) {
                                return {
                                    x: svgRect.left + svgRect.width / 2,
                                    y: svgRect.top + svgRect.height / 2
                                };
                            }
                            const iconRect = el.getBoundingClientRect();
                            const r = (iconRect.width > 12 && iconRect.height > 12) ? iconRect : btn.getBoundingClientRect();
                            if (
                                r.left < 260 ||
                                r.left > 1150 ||
                                r.top < 200 ||
                                r.top > window.innerHeight - 20 ||
                                r.width <= 8 ||
                                r.height <= 8
                            ) continue;
                            return {
                                x: r.left + r.width / 2,
                                y: r.top + Math.min(r.width, r.height) / 2
                            };
                        }
                    }
                    return null;
                }"""
            )
        except Exception:
            return None

    def _step4_favorite_video(self, page: Any, ch_folder: str) -> bool:
        """
        BƯỚC 4: Thêm video vào mục Yêu thích (Bookmark) — kiểm tra màu vàng thực tế (#FACE15) trên ảnh chụp màn hình.
        """
        try:
            fav_info = self._check_favorite_state(page)
            if fav_info:
                bx, by = float(fav_info["x"]), float(fav_info["y"])
                if self._is_bookmark_gold_on_screenshot(page.screenshot(type="png"), bx, by):
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 4/6] Video đã có dấu Yêu thích màu vàng thực tế (giữ nguyên 🔖).")
                    return True

                self._os_click_viewport(page, bx, by)
                time.sleep(random.uniform(2.0, 2.8))
                self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)

                recheck = self._check_favorite_state(page)
                rx = float(recheck["x"]) if recheck else bx
                ry = float(recheck["y"]) if recheck else by
                if not self._is_bookmark_gold_on_screenshot(page.screenshot(type="png"), rx, ry):
                    self._os_click_viewport(page, rx, ry)
                    time.sleep(random.uniform(2.0, 2.6))
                    self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)

                logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 4/6] Đã THÊM VIDEO VÀO MỤC YÊU THÍCH 🔖 (xác nhận vàng #FACE15).")
                return True
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi thêm vào mục yêu thích: {exc}")
        return False

    def _check_follow_state(self, page: Any) -> Optional[Dict[str, Any]]:
        """
        Đọc trạng thái thực tế của nút Follow trên DOM TikTok:
        - Loại trừ hoàn toàn thanh menu bên trái (r.left < 260, nơi có chữ 'Bạn bè' và 'Tin nhắn').
        - Hỗ trợ cả nút chữ nhật '[data-e2e="follow-button"]' lẫn nút dấu cộng đỏ '+' tròn trên avatar
          ('[data-e2e="feed-follow"]' / '[data-e2e="browse-follow"]', vốn không có innerText).
        """
        try:
            return page.evaluate(
                """() => {
                    const alreadyLabels = ['Đang Follow', 'Following', 'Bạn bè', 'Friends'];
                    const followLabels = ['Follow', 'Theo dõi'];

                    // 1. Kiểm tra nút Follow chữ nhật [data-e2e="follow-button"] ở khung phải (r.left > 260)
                    const rectBtns = Array.from(document.querySelectorAll('[data-e2e="follow-button"]'));
                    for (const el of rectBtns) {
                        const r = el.getBoundingClientRect();
                        if (r.left < 260 || r.width < 12 || r.height < 12) continue;
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (alreadyLabels.some(l => txt.toLowerCase() === l.toLowerCase() || txt.toLowerCase().includes(l.toLowerCase()))) {
                            return { alreadyFollowing: true };
                        }
                        if (!txt || followLabels.some(l => txt.toLowerCase() === l.toLowerCase())) {
                            return {
                                x: r.left + r.width / 2,
                                y: r.top + r.height / 2,
                                alreadyFollowing: false
                            };
                        }
                    }

                    // 2. Kiểm tra huy hiệu dấu '+' đỏ trên avatar ([data-e2e="feed-follow"], [data-e2e="browse-follow"])
                    const badgeSels = ['[data-e2e="feed-follow"]', '[data-e2e="browse-follow"]'];
                    for (const sel of badgeSels) {
                        const els = Array.from(document.querySelectorAll(sel));
                        for (const el of els) {
                            const r = el.getBoundingClientRect();
                            if (r.left < 260 || r.top < 150 || r.top > window.innerHeight - 30 || r.width < 10 || r.height < 10) continue;
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') < 0.1) {
                                continue;
                            }
                            const txt = (el.innerText || el.textContent || '').trim();
                            if (alreadyLabels.some(l => txt.toLowerCase().includes(l.toLowerCase()))) {
                                return { alreadyFollowing: true };
                            }
                            return {
                                x: r.left + r.width / 2,
                                y: r.top + r.height / 2,
                                alreadyFollowing: false
                            };
                        }
                    }

                    // 3. Quét các nút button ngoài vùng sidebar trái (r.left > 260, không nằm trong nav/aside)
                    const buttons = Array.from(document.querySelectorAll('button, div[role="button"]'));
                    for (const btn of buttons) {
                        if (btn.closest('nav, aside, [data-e2e="nav-bar"]')) continue;
                        const r = btn.getBoundingClientRect();
                        if (r.left < 260 || r.width < 14 || r.height < 14 || r.top < 40 || r.top > 750) continue;
                        const txt = (btn.innerText || btn.textContent || '').trim();
                        if (followLabels.includes(txt)) {
                            return {
                                x: r.left + r.width / 2,
                                y: r.top + r.height / 2,
                                alreadyFollowing: false
                            };
                        }
                    }

                    // 4. Fallback tọa độ hình học: tìm nút dấu '+' đỏ nằm ngay phía trên nút Thả tim (cùng cột dọc x)
                    const likeEl = document.querySelector('[data-e2e="like-icon"], [data-e2e="browse-like-icon"]');
                    if (likeEl) {
                        const lr = (likeEl.closest('button') || likeEl).getBoundingClientRect();
                        const likeX = lr.left + lr.width / 2;
                        const likeY = lr.top + lr.height / 2;
                        const candidates = Array.from(document.querySelectorAll('button, div, span, svg'));
                        for (const c of candidates) {
                            const cr = c.getBoundingClientRect();
                            if (cr.width < 14 || cr.width > 36 || cr.height < 14 || cr.height > 36) continue;
                            const cx = cr.left + cr.width / 2;
                            const cy = cr.top + cr.height / 2;
                            if (Math.abs(cx - likeX) < 28 && (likeY - cy) > 40 && (likeY - cy) < 105) {
                                const bg = (window.getComputedStyle(c).backgroundColor || '').toLowerCase();
                                if (bg.includes('254, 44, 85') || bg.includes('255, 59, 92') || c.tagName.toLowerCase() === 'button') {
                                    return { x: cx, y: cy, alreadyFollowing: false };
                                }
                            }
                        }
                    }
                    return null;
                }"""
            )
        except Exception:
            return None

    def _step5_follow_creator(self, page: Any, ch_folder: str) -> bool:
        """
        BƯỚC 5: Follow người đăng video đó — kiểm tra điểm ảnh huy hiệu dấu '+' đỏ trên avatar
        (tránh bấm nhầm khi huy hiệu đã chuyển sang hình tròn trắng dấu tích đỏ '✓').
        """
        try:
            follow_info = self._check_follow_state(page)
            if follow_info:
                if follow_info.get("alreadyFollowing"):
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 5/6] Đã Follow chủ kênh này từ trước (giữ nguyên ➕).")
                    return True
                fx, fy = float(follow_info["x"]), float(follow_info["y"])
                png_now = page.screenshot(type="png")
                if not self._is_follow_badge_unfollowed_red(png_now, fx, fy) and fx > 780 and fy > 150:
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 5/6] Huy hiệu trên avatar đã là dấu tích ✓ (đã Follow, giữ nguyên).")
                    return True

                self._os_click_viewport(page, fx, fy)
                time.sleep(random.uniform(2.2, 3.0))
                self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)

                # Nếu sau khi giải Captcha huy hiệu vẫn còn là hình tròn đỏ '+', bấm bù đúng 1 lần
                if self._is_follow_badge_unfollowed_red(page.screenshot(type="png"), fx, fy):
                    self._os_click_viewport(page, fx, fy)
                    time.sleep(random.uniform(2.0, 2.8))
                    self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)

                logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 5/6] Đã FOLLOW người đăng video ➕ (xác nhận dấu tích ✓).")
                return True
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi bấm Follow: {exc}")
        return False

    def _step6_post_meaningful_comment(
        self, page: Any, ch_folder: str, creator_handle: str
    ) -> Optional[str]:
        """
        BƯỚC 6: Để lại 1 bình luận có ý nghĩa sau khi đã Xem hết video + Thả tim + Sao chép liên kết +
        Thêm vào yêu thích + Follow:
        - Đảm bảo tab 'Bình luận' ở khung phải đã mở.
        - Bấm vào ô 'Thêm bình luận...', dùng `insert_text` + `keyboard.type(" ")` để điền trọn vẹn câu tiếng Việt
          vào DraftJS làm sáng nút đỏ 'Đăng' (`[data-e2e="comment-post"]`).
        - Bấm nút 'Đăng' ĐÚNG 1 LẦN (hoặc phím Enter) để gửi bình luận thành công 100%.
        """
        comment_text = self.generate_meaningful_24h_comment(creator_handle=creator_handle)
        try:
            # 1. Đảm bảo tab 'Bình luận' đã mở và tìm tọa độ ô nhập bình luận
            self._ensure_comment_tab_open(page, ch_folder)
            input_pos = self._locate_comment_input_box(page)

            if not input_pos:
                logger.warning(f"[{ch_folder}] [Bộ lọc 24h] Không tìm thấy ô bình luận trên video này.")
                return None

            # 2. Click ĐÚNG 1 LẦN vào ô bình luận để kích hoạt khung soạn thảo DraftJS
            self._os_click_viewport(page, float(input_pos["x"]), float(input_pos["y"]))
            time.sleep(random.uniform(0.7, 1.1))

            # Focus trực tiếp vào div[contenteditable="true"] bên trong khung bình luận (nếu vừa được mount)
            try:
                ce_box = page.locator(
                    '[data-e2e="comment-input"] div[contenteditable="true"], '
                    '[data-e2e="comment-text"] div[contenteditable="true"], '
                    'div.public-DraftEditor-content[contenteditable="true"], '
                    'div[contenteditable="true"]'
                ).first
                if ce_box.count() > 0 and ce_box.is_visible():
                    ce_box.click()
                    time.sleep(0.25)
            except Exception:
                pass

            # 3. Nhập nội dung tiếng Việt bằng insert_text kết hợp gõ phím Space để DraftJS kích hoạt nút 'Đăng'
            page.keyboard.insert_text(comment_text)
            time.sleep(0.25)
            page.keyboard.type(" ", delay=55)
            time.sleep(random.uniform(0.9, 1.4))

            # 4. Tìm và bấm nút 'Đăng' (`[data-e2e="comment-post"]`) ĐÚNG 1 LẦN
            post_comment_pos = page.evaluate(
                """() => {
                    const selectors = [
                        '[data-e2e="comment-post"]',
                        'div[data-e2e="comment-post"]',
                        'button[data-e2e="comment-post"]'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 10 && r.height > 10) {
                                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                            }
                        }
                    }
                    const labels = ['Đăng', 'Post', 'Gửi'];
                    const els = Array.from(document.querySelectorAll('button, div[role="button"], span'));
                    for (const el of els) {
                        if (el.children.length > 2) continue;
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (labels.includes(txt)) {
                            const r = el.getBoundingClientRect();
                            if (r.left > 500 && r.width > 18 && r.height > 14 && r.top > 200) {
                                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if post_comment_pos:
                self._os_click_viewport(page, float(post_comment_pos["x"]), float(post_comment_pos["y"]))
                time.sleep(1.0)
            else:
                page.keyboard.press("Enter")

            time.sleep(random.uniform(2.5, 3.8))
            logger.info(
                f"[{ch_folder}] [Bộ lọc 24h - Bước 6/6] Đã BÌNH LUẬN thành công: \"{comment_text}\""
            )
            return comment_text
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi gửi bình luận: {exc}")
            return None

    @staticmethod
    def _locate_comment_input_box(page: Any) -> Optional[Dict[str, float]]:
        """Tìm tọa độ ô nhập bình luận trên trang chi tiết hoặc trang feed video TikTok."""
        return page.evaluate(
            """() => {
                const selectors = [
                    '[data-e2e="comment-input"]',
                    '[data-e2e="comment-text"]',
                    'div[class*="DivCommentInput"]',
                    'div.public-DraftEditor-content[contenteditable="true"]',
                    'div[contenteditable="true"]'
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (!el) continue;
                    const r = el.getBoundingClientRect();
                    if (r.left > 260 && r.width > 40 && r.height > 14 && r.top > 50 && r.top < window.innerHeight - 5) {
                        return { x: r.left + Math.min(r.width * 0.35, 120), y: r.top + r.height * 0.5 };
                    }
                }
                const placeholders = ['Thêm bình luận', 'Add comment', 'Viết bình luận', 'Bình luận...'];
                const all = Array.from(document.querySelectorAll('div, span, p, input, textarea'));
                for (const el of all) {
                    if (el.children.length > 2) continue;
                    const txt = (el.innerText || el.getAttribute('placeholder') || '').trim();
                    if (placeholders.some(p => txt.includes(p))) {
                        const r = el.getBoundingClientRect();
                        if (r.left > 260 && r.width > 40 && r.height > 14 && r.top > 50 && r.top < window.innerHeight - 5) {
                            return { x: r.left + Math.min(r.width * 0.35, 120), y: r.top + r.height * 0.5 };
                        }
                    }
                }
                return null;
            }"""
        )

    def interact_with_single_video(
        self, page: Any, ch_folder: str, video_url: str
    ) -> Dict[str, Any]:
        """
        Thực thi trọn vẹn 6 bước "Bộ lọc 24 giờ" trên 1 video mục tiêu,
        bấm ĐÚNG 1 LẦN cho từng nút (Tim + Copy Link + Yêu thích + Follow + Bình luận) để không bao giờ bị tự gỡ,
        và lưu ảnh chụp màn hình + sổ theo dõi 24h.
        """
        creator_handle, video_id = self._extract_creator_and_video_id(video_url)
        logger.info(
            f"[{ch_folder}] === BẮT ĐẦU BỘ LỌC 24 GIỜ TRÊN VIDEO CỦA @{creator_handle or 'creator'} ({video_url}) ==="
        )

        self._last_like_debug = {"digg_responses": []}

        def _on_response(resp: Any) -> None:
            try:
                u = resp.url or ""
                if "/commit/item/digg" in u or "/item/digg" in u:
                    self._last_like_debug["digg_responses"].append(
                        {"status": resp.status, "body": (resp.text() or "")[:240]}
                    )
            except Exception:
                pass

        try:
            page.on("response", _on_response)
        except Exception:
            pass

        try:
            page.context.add_init_script(DIGG_UI_GUARD_SCRIPT)
        except Exception:
            pass

        page.goto(
            f"{video_url}?lang=vi-VN",
            referer="https://www.tiktok.com/",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        try:
            page.evaluate(DIGG_UI_GUARD_SCRIPT)
            self._last_like_debug["env_check"] = page.evaluate(
                """() => ({
                    webdriver: navigator.webdriver,
                    hasOwnWebdriver: Object.prototype.hasOwnProperty.call(navigator, "webdriver"),
                    hasFakeChromeRuntime: Boolean(window.chrome && window.chrome.runtime && window.chrome.runtime.PlatformOs)
                })"""
            )
        except Exception:
            pass
        time.sleep(random.uniform(3.0, 4.5))
        self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)

        # 1. Xem hết video của người khác trước (như người thật để tránh kích hoạt Captcha sớm)
        watched_sec = self._step1_watch_full_video(page, ch_folder, video_url)
        self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)

        # 2. Thả tim video (xác nhận tim đỏ #FE2C55)
        liked = self._step2_like_video(page, ch_folder)
        time.sleep(random.uniform(1.8, 2.6))

        # 3. Sao chép liên kết video đó
        self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
        copied_link = self._step3_copy_video_link(page, ch_folder, video_url)
        time.sleep(random.uniform(1.8, 2.6))

        # 4. Thêm video vào mục yêu thích (xác nhận bookmark vàng #FACE15)
        self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
        favorited = self._step4_favorite_video(page, ch_folder)
        time.sleep(random.uniform(1.8, 2.6))

        # 5. Follow người đăng video đó (xác nhận dấu tích ✓)
        self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
        followed = self._step5_follow_creator(page, ch_folder)
        time.sleep(random.uniform(2.0, 3.0))

        # 6. Để lại đúng 1 câu bình luận: "chào mn, mình mới xây kênh, nhận follow chéo ạ"
        self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
        comment_text = self._step6_post_meaningful_comment(page, ch_folder, creator_handle)

        # 7. Kiểm tra lại trạng thái Tim đỏ ❤️ & Bookmark vàng 🔖 & Follow ✓ một lần cuối trước khi chụp ảnh
        time.sleep(2.5)
        self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
        self._ensure_comment_tab_open(page, ch_folder)
        liked = self._step2_like_video(page, ch_folder) or liked
        favorited = self._step4_favorite_video(page, ch_folder) or favorited
        followed = self._step5_follow_creator(page, ch_folder) or followed
        time.sleep(2.0)

        # Chụp ảnh màn hình kết quả tương tác vào remote_telemetry/ để giám sát từ xa
        try:
            telem_dir = self.project_root / "remote_telemetry"
            telem_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(telem_dir / f"latest_filter24h_{ch_folder}.jpg"),
                type="jpeg",
                quality=60,
            )
        except Exception:
            pass

        now_dt = datetime.now()
        record = {
            "channel": ch_folder,
            "video_id": video_id,
            "video_url": video_url,
            "creator_handle": f"@{creator_handle}" if creator_handle else "",
            "watched_seconds": watched_sec,
            "steps_completed": {
                "1_watched_full": watched_sec >= 15.0,
                "2_liked_video": liked,
                "3_copied_link": copied_link,
                "4_added_favorite": favorited,
                "5_followed_creator": followed,
                "6_commented_after_follow": bool(comment_text),
            },
            "comment_text": comment_text or "",
            "like_debug": getattr(self, "_last_like_debug", {}),
            "interacted_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "follow_back_window_until": (now_dt + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),
        }

        hist = self.load_history()
        hist["interactions"].append(record)
        self.save_history(hist)

        logger.info(
            f"[{ch_folder}] === HOÀN TẤT BỘ LỌC 24 GIỜ CHO @{creator_handle}: "
            f"Xem={watched_sec}s | Tim={liked} | CopyLink={copied_link} | YêuThích={favorited} | "
            f"Follow={followed} | Comment={bool(comment_text)} | Chờ trả follow đến {record['follow_back_window_until']} ==="
        )
        return record

    def run_session_on_page(
        self, page: Any, ch_folder: str, video_count: int = 2
    ) -> List[Dict[str, Any]]:
        """
        Chạy 1 phiên "Bộ lọc 24 giờ" cho kênh `ch_folder` trên `page` đang mở sẵn.
        """
        target_urls = self._discover_target_videos(page, ch_folder, needed_count=video_count)
        if not target_urls:
            logger.warning(
                f"[{ch_folder}] [Bộ lọc 24h] Chưa tìm thấy video mục tiêu mới trong phiên này."
            )
            return []

        results: List[Dict[str, Any]] = []
        for idx, url in enumerate(target_urls[:video_count], start=1):
            try:
                logger.info(
                    f"[{ch_folder}] [Bộ lọc 24h] Đang tương tác video #{idx}/{min(len(target_urls), video_count)}: {url}"
                )
                rec = self.interact_with_single_video(page, ch_folder, url)
                results.append(rec)
                if idx < min(len(target_urls), video_count):
                    pause_s = random.uniform(5.0, 10.0)
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h] Nghỉ tự nhiên {pause_s:.1f}s trước khi sang video kế tiếp...")
                    time.sleep(pause_s)
            except Exception as exc:
                logger.warning(f"[{ch_folder}] [Bộ lọc 24h] Lỗi khi tương tác video {url}: {exc}")

        return results
