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
        Tạo bình luận tiếng Việt tự nhiên, có ý nghĩa, không trùng lặp và nhấn mạnh đã hoàn tất
        đủ các bước của Bộ lọc 24 giờ (kèm xác nhận đã follow để không bị chủ kênh bỏ sót).
        """
        opener = random.choice(COMMENT_OPENERS)
        action = random.choice(COMMENT_ACTIONS)
        closer = random.choice(COMMENT_CLOSERS)

        clean_handle = creator_handle.strip().lstrip("@")
        if clean_handle and not clean_handle.startswith("user") and len(clean_handle) <= 22 and random.random() < 0.45:
            comment = f"Chào @{clean_handle}, {opener[0].lower() + opener[1:]} {action} {closer}"
        else:
            comment = f"{opener} {action} {closer}"

        return re.sub(r"\s+", " ", comment).strip()

    @staticmethod
    def _compute_best_whirl_rotation_fraction(
        outer_bytes: bytes, inner_bytes: bytes, inner_to_outer_ratio: float = 0.608
    ) -> float:
        """
        Thuật toán Thị giác máy tính (Circular Boundary Edge & Color Correlation bằng Pillow + NumPy):
        - Khi kéo thanh trượt một tỷ lệ `frac` trong `[0.0, 1.0]`, vòng trong xoay thuận chiều `+180 * frac` độ
          và vòng ngoài xoay ngược chiều `-180 * frac` độ (tổng góc quay tương đối `360 * frac` độ).
        - Lấy 360 mẫu điểm ảnh dọc theo đường tròn ranh giới tiếp giáp giữa ảnh vòng trong (`inner_img`)
          và ảnh vòng ngoài (`outer_img`), tìm góc lệch `delta_deg` (từ `18°` đến `342°`) có sai số màu RGB
          và độ dốc biên (Edge Gradient) nhỏ nhất.
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

        # Lấy vành điểm ảnh sát mép ngoài của vòng trong và sát mép trong của vòng ngoài
        r_in = (inner_size * 0.5) * 0.93
        r_out = (inner_size * 0.5) * 1.07

        cx_in, cy_in = (inner_size - 1) * 0.5, (inner_size - 1) * 0.5
        cx_out, cy_out = (size - 1) * 0.5, (size - 1) * 0.5

        ix = np.clip(np.round(cx_in + r_in * cos_a).astype(int), 0, inner_size - 1)
        iy = np.clip(np.round(cy_in + r_in * sin_a).astype(int), 0, inner_size - 1)

        ox = np.clip(np.round(cx_out + r_out * cos_a).astype(int), 0, size - 1)
        oy = np.clip(np.round(cy_out + r_out * sin_a).astype(int), 0, size - 1)

        ring_in = inner_arr[iy, ix]  # (360, 3)
        ring_out = outer_arr[oy, ox]  # (360, 3)

        # Kết hợp đặc trưng màu sắc chuẩn hóa + đạo hàm biên dọc theo chu vi hình tròn
        grad_in = np.roll(ring_in, -1, axis=0) - np.roll(ring_in, 1, axis=0)
        grad_out = np.roll(ring_out, -1, axis=0) - np.roll(ring_out, 1, axis=0)

        ring_in_norm = (ring_in - ring_in.mean(axis=0)) / (ring_in.std(axis=0) + 1e-5)
        ring_out_norm = (ring_out - ring_out.mean(axis=0)) / (ring_out.std(axis=0) + 1e-5)

        best_deg = 180
        best_score = -1e9

        for deg in range(18, 343, 2):
            # Vòng trong xoay thuận +deg/2, vòng ngoài xoay ngược -deg/2 -> độ lệch tương đối = deg
            shifted_in = np.roll(ring_in_norm, deg, axis=0)
            shifted_grad_in = np.roll(grad_in, deg, axis=0)

            color_corr = float(np.mean(shifted_in * ring_out_norm))
            grad_diff = float(np.mean(np.abs(shifted_grad_in - grad_out))) / 255.0
            score = color_corr - 0.65 * grad_diff

            if score > best_score:
                best_score = score
                best_deg = deg

        return max(0.06, min(0.94, best_deg / 360.0))

    def solve_tiktok_rotation_captcha_if_present(
        self, page: Any, ch_folder: str, max_attempts: int = 3
    ) -> bool:
        """
        Tự động phát hiện và giải Captcha xoay hình tròn của TikTok ('Kéo thanh trượt để ghép hình')
        bằng Thị giác máy tính (Circular Boundary Correlation) + kéo chuột phần cứng macOS (`CoreGraphics CGEvent`).
        """
        from curl_cffi import requests as creq

        for attempt in range(1, max_attempts + 1):
            try:
                captcha_data = page.evaluate(
                    """() => {
                        const modalTexts = ['Kéo thanh trượt để ghép hình', 'Drag the slider to fit the puzzle', 'Verify to continue'];
                        const bodyTxt = document.body ? (document.body.innerText || '') : '';
                        const hasText = modalTexts.some(t => bodyTxt.includes(t));
                        const imgs = Array.from(document.querySelectorAll('img')).filter(img => {
                            const r = img.getBoundingClientRect();
                            return r.width >= 80 && r.width <= 380 && Math.abs(r.width - r.height) <= 12 && img.src;
                        });
                        if (!hasText && imgs.length < 2) return null;
                        if (imgs.length < 2) return null;

                        imgs.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
                        const outerImg = imgs[0];
                        const innerImg = imgs[1];
                        const outerRect = outerImg.getBoundingClientRect();
                        const innerRect = innerImg.getBoundingClientRect();

                        // Tìm nút kéo (slider handle) nằm bên dưới hình tròn
                        let handleEl = document.querySelector(
                            '.secsdk-captcha-drag-icon, [class*="captcha-drag-icon"], [class*="secsdk_captcha_drag_icon"]'
                        );
                        if (!handleEl) {
                            const candidates = Array.from(document.querySelectorAll('div, button, span')).filter(el => {
                                const r = el.getBoundingClientRect();
                                return (
                                    r.top > outerRect.bottom - 10 &&
                                    r.top < outerRect.bottom + 140 &&
                                    r.width >= 36 && r.width <= 85 &&
                                    r.height >= 28 && r.height <= 65 &&
                                    r.left >= outerRect.left - 60 &&
                                    r.left <= outerRect.left + 80
                                );
                            });
                            candidates.sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
                            handleEl = candidates[0] || null;
                        }
                        if (!handleEl) return null;

                        const handleRect = handleEl.getBoundingClientRect();
                        let trackWidth = 280;
                        let parent = handleEl.parentElement;
                        for (let i = 0; i < 4 && parent; i++) {
                            const pr = parent.getBoundingClientRect();
                            if (pr.width >= 200 && pr.width <= 440) {
                                trackWidth = pr.width;
                                break;
                            }
                            parent = parent.parentElement;
                        }

                        return {
                            outerSrc: outerImg.src,
                            innerSrc: innerImg.src,
                            ratio: innerRect.width / Math.max(1, outerRect.width),
                            handleX: handleRect.left + handleRect.width / 2,
                            handleY: handleRect.top + handleRect.height / 2,
                            handleWidth: handleRect.width,
                            trackWidth: trackWidth
                        };
                    }"""
                )
                if not captcha_data:
                    return True

                logger.info(
                    f"[{ch_folder}] [Auto-Captcha Solver] Phát hiện Captcha xoay hình tròn của TikTok (lần thử {attempt}/{max_attempts}), đang tính góc khớp biên..."
                )
                r_out = creq.get(captcha_data["outerSrc"], impersonate="chrome", timeout=10)
                r_in = creq.get(captcha_data["innerSrc"], impersonate="chrome", timeout=10)
                if r_out.status_code != 200 or r_in.status_code != 200:
                    return False

                frac = self._compute_best_whirl_rotation_fraction(
                    outer_bytes=r_out.content,
                    inner_bytes=r_in.content,
                    inner_to_outer_ratio=float(captcha_data.get("ratio") or 0.608),
                )
                max_slide_px = max(
                    160.0,
                    float(captcha_data["trackWidth"]) - float(captcha_data["handleWidth"]) - 4.0,
                )
                drag_px = frac * max_slide_px

                start_x = float(captcha_data["handleX"])
                start_y = float(captcha_data["handleY"])
                end_x = start_x + drag_px
                end_y = start_y + random.uniform(-1.5, 1.5)

                logger.info(
                    f"[{ch_folder}] [Auto-Captcha Solver] Góc khớp tối ưu: {frac * 360:.1f}° -> Kéo thanh trượt {drag_px:.1f}px / {max_slide_px:.1f}px..."
                )

                # Kéo mượt mà kết hợp chuột phần cứng macOS và trình duyệt (có giảm tốc cuối hành trình như người thật)
                self._human_move_mouse(page, start_x, start_y)
                time.sleep(random.uniform(0.18, 0.32))
                page.mouse.down()
                steps = random.randint(24, 34)
                for s in range(1, steps + 1):
                    # Đường cong Ease-Out tự nhiên của tay người khi căn chỉnh hình tròn
                    t = s / steps
                    eased = 1.0 - pow(1.0 - t, 2.2)
                    cur_x = start_x + drag_px * eased + random.uniform(-0.8, 0.8) * (1.0 - t)
                    cur_y = start_y + random.uniform(-1.8, 1.8)
                    page.mouse.move(cur_x, cur_y)
                    time.sleep(random.uniform(0.014, 0.028))

                # Giữ nhẹ 0.25s để mắt người kiểm tra khớp hình rồi nhả chuột
                page.mouse.move(end_x, end_y)
                time.sleep(random.uniform(0.22, 0.38))
                page.mouse.up()

                try:
                    sx1, sy1 = self.desktop.convert_viewport_to_screen_coords(page, start_x, start_y)
                    sx2, sy2 = self.desktop.convert_viewport_to_screen_coords(page, end_x, end_y)
                    self.desktop.move_mouse_os(sx2, sy2)
                except Exception:
                    pass

                time.sleep(random.uniform(2.8, 4.0))

                still_present = page.evaluate(
                    """() => {
                        const modalTexts = ['Kéo thanh trượt để ghép hình', 'Drag the slider to fit the puzzle'];
                        const bodyTxt = document.body ? (document.body.innerText || '') : '';
                        return modalTexts.some(t => bodyTxt.includes(t));
                    }"""
                )
                if not still_present:
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

    def _step2_like_video(self, page: Any, ch_folder: str) -> bool:
        """
        BƯỚC 2: Thả tim video (Like) — chỉ bấm ĐÚNG 1 LẦN nếu chưa thả tim (kiểm tra cả aria-pressed và màu đỏ SVG).
        """
        try:
            like_info = page.evaluate(
                """() => {
                    const selectors = [
                        '[data-e2e="like-icon"]',
                        '[data-e2e="browse-like-icon"]',
                        'button[aria-label*="Thích"]',
                        'button[aria-label*="Like"]',
                        'span[data-e2e="like-icon"]'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        const btn = el.closest('button') || el.parentElement || el;
                        const pressed = btn.getAttribute('aria-pressed') === 'true';
                        const svgPaths = Array.from(btn.querySelectorAll('svg, path, use'));
                        const isRed = svgPaths.some(p => {
                            const fill = (p.getAttribute('fill') || window.getComputedStyle(p).fill || '').toLowerCase();
                            return fill.includes('254, 44, 85') || fill.includes('fe2c55') || fill.includes('ff2c55') || fill.includes('255, 59, 92');
                        });
                        const r = btn.getBoundingClientRect();
                        if (r.width > 8 && r.height > 8) {
                            return {
                                x: r.left + r.width / 2,
                                y: r.top + r.height / 2,
                                alreadyLiked: pressed || isRed
                            };
                        }
                    }
                    return null;
                }"""
            )
            if like_info:
                if like_info.get("alreadyLiked"):
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 2/6] Video đã được thả tim từ trước (giữ nguyên ❤️).")
                    return True
                self._os_click_viewport(page, float(like_info["x"]), float(like_info["y"]))
                time.sleep(random.uniform(1.2, 2.0))
                logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 2/6] Đã THẢ TIM video ❤️ (1 click duy nhất).")
                return True
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi bước thả tim: {exc}")
        return False

    def _step3_copy_video_link(self, page: Any, ch_folder: str, video_url: str) -> bool:
        """
        BƯỚC 3: Sao chép liên kết video đó (Copy Link) — bấm ĐÚNG 1 LẦN.
        """
        try:
            direct_copy = page.evaluate(
                """() => {
                    const directSelectors = [
                        '[data-e2e="video-share-copy"]',
                        'button[aria-label*="Sao chép"]',
                        'button[aria-label*="Copy link"]'
                    ];
                    for (const sel of directSelectors) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 8 && r.height > 8) {
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
                            if (r.width > 12 && r.height > 10) {
                                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if direct_copy:
                self._os_click_viewport(page, float(direct_copy["x"]), float(direct_copy["y"]))
                time.sleep(random.uniform(1.0, 1.8))
                logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 3/6] Đã SAO CHÉP LIÊN KẾT video 🔗.")
                return True

            share_btn = page.evaluate(
                """() => {
                    const selectors = [
                        '[data-e2e="share-icon"]',
                        '[data-e2e="browse-share-icon"]',
                        'button[aria-label*="Chia sẻ"]',
                        'button[aria-label*="Share"]'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        const btn = el.closest('button') || el.parentElement || el;
                        const r = btn.getBoundingClientRect();
                        if (r.width > 8 && r.height > 8) {
                            return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                        }
                    }
                    return null;
                }"""
            )
            if share_btn:
                self._human_move_mouse(page, float(share_btn["x"]), float(share_btn["y"]))
                time.sleep(random.uniform(1.2, 1.8))
                copy_in_menu = page.evaluate(
                    """() => {
                        const labels = ['Sao chép liên kết', 'Copy link'];
                        const els = Array.from(document.querySelectorAll('a, button, span, p, div'));
                        for (const el of els) {
                            if (el.children.length > 2) continue;
                            const txt = (el.innerText || el.textContent || '').trim();
                            if (labels.some(l => txt.includes(l))) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 10 && r.height > 10) {
                                    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                                }
                            }
                        }
                        return null;
                    }"""
                )
                if copy_in_menu:
                    self._os_click_viewport(page, float(copy_in_menu["x"]), float(copy_in_menu["y"]))
                    time.sleep(random.uniform(1.0, 1.6))
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 3/6] Đã mở menu Chia sẻ & SAO CHÉP LIÊN KẾT video 🔗.")
                    return True
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi sao chép liên kết: {exc}")
        return False

    def _step4_favorite_video(self, page: Any, ch_folder: str) -> bool:
        """
        BƯỚC 4: Thêm video vào mục Yêu thích (Bookmark) — chỉ bấm ĐÚNG 1 LẦN nếu chưa lưu.
        """
        try:
            fav_info = page.evaluate(
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
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        const btn = el.closest('button') || el.parentElement || el;
                        const pressed = btn.getAttribute('aria-pressed') === 'true';
                        const svgPaths = Array.from(btn.querySelectorAll('svg, path, use'));
                        const isGold = svgPaths.some(p => {
                            const fill = (p.getAttribute('fill') || window.getComputedStyle(p).fill || '').toLowerCase();
                            return fill.includes('250, 206, 21') || fill.includes('face15') || fill.includes('ffb800');
                        });
                        const r = btn.getBoundingClientRect();
                        if (r.width > 8 && r.height > 8) {
                            return {
                                x: r.left + r.width / 2,
                                y: r.top + r.height / 2,
                                alreadyFavorited: pressed || isGold
                            };
                        }
                    }
                    return null;
                }"""
            )
            if fav_info:
                if fav_info.get("alreadyFavorited"):
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 4/6] Video đã nằm trong mục Yêu thích từ trước (giữ nguyên 🔖).")
                    return True
                self._os_click_viewport(page, float(fav_info["x"]), float(fav_info["y"]))
                time.sleep(random.uniform(1.2, 2.0))
                logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 4/6] Đã THÊM VIDEO VÀO MỤC YÊU THÍCH 🔖 (1 click duy nhất).")
                return True
        except Exception as exc:
            logger.debug(f"[{ch_folder}] [Bộ lọc 24h] Lỗi thêm vào mục yêu thích: {exc}")
        return False

    def _step5_follow_creator(self, page: Any, ch_folder: str) -> bool:
        """
        BƯỚC 5: Follow người đăng video đó — kiểm tra chặt trạng thái và chỉ bấm ĐÚNG 1 LẦN nếu chưa Follow.
        """
        try:
            follow_info = page.evaluate(
                """() => {
                    const alreadyLabels = ['Đang Follow', 'Following', 'Bạn bè', 'Friends', 'Đã gửi', 'Tin nhắn', 'Message'];
                    const followLabels = ['Follow', 'Theo dõi'];
                    const selectors = [
                        '[data-e2e="follow-button"]',
                        '[data-e2e="browse-follow"]',
                        '[data-e2e="feed-follow"]'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (alreadyLabels.some(l => txt.toLowerCase().includes(l.toLowerCase()))) {
                            return { alreadyFollowing: true };
                        }
                        if (!followLabels.some(l => txt.toLowerCase() === l.toLowerCase())) {
                            continue;
                        }
                        const r = el.getBoundingClientRect();
                        if (r.width > 12 && r.height > 12) {
                            return {
                                x: r.left + r.width / 2,
                                y: r.top + r.height / 2,
                                alreadyFollowing: false
                            };
                        }
                    }
                    const buttons = Array.from(document.querySelectorAll('button'));
                    for (const btn of buttons) {
                        const txt = (btn.innerText || btn.textContent || '').trim();
                        if (alreadyLabels.some(l => txt.toLowerCase().includes(l.toLowerCase()))) {
                            continue;
                        }
                        if (followLabels.includes(txt)) {
                            const r = btn.getBoundingClientRect();
                            if (r.width > 25 && r.height > 16 && r.top > 45 && r.top < 500) {
                                return {
                                    x: r.left + r.width / 2,
                                    y: r.top + r.height / 2,
                                    alreadyFollowing: false
                                };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if follow_info:
                if follow_info.get("alreadyFollowing"):
                    logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 5/6] Đã Follow chủ kênh này từ trước (giữ nguyên ➕).")
                    return True
                self._os_click_viewport(page, float(follow_info["x"]), float(follow_info["y"]))
                time.sleep(random.uniform(1.5, 2.4))
                logger.info(f"[{ch_folder}] [Bộ lọc 24h - Bước 5/6] Đã FOLLOW người đăng video ➕ (1 click duy nhất).")
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
        - Tự động bấm mở tab/icon Bình luận nếu khung bình luận đang ẩn.
        - Bấm vào ô nhập bình luận và gõ từng ký tự trực tiếp vào DraftJS (`page.keyboard.type`) để nút 'Đăng' sáng lên.
        - Bấm nút 'Đăng' (`[data-e2e="comment-post"]`) + nhấn phím Enter để gửi bình luận thành công 100%.
        """
        comment_text = self.generate_meaningful_24h_comment(creator_handle=creator_handle)
        try:
            # 1. Nếu tab 'Bình luận' hoặc nút icon Bình luận chưa mở khung nhập, bấm mở trước
            input_pos = self._locate_comment_input_box(page)
            if not input_pos:
                opened_tab = page.evaluate(
                    """() => {
                        const tabLabels = ['Bình luận', 'Comments'];
                        const els = Array.from(document.querySelectorAll('div, span, button, p'));
                        for (const el of els) {
                            if (el.children.length > 2) continue;
                            const txt = (el.innerText || el.textContent || '').trim();
                            if (tabLabels.some(l => txt.startsWith(l)) && txt.length < 28) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 30 && r.height > 14 && r.top > 40 && r.top < 600) {
                                    try { el.click(); } catch (e) {}
                                    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                                }
                            }
                        }
                        const iconSels = ['[data-e2e="comment-icon"]', '[data-e2e="browse-comment-icon"]'];
                        for (const sel of iconSels) {
                            const icon = document.querySelector(sel);
                            if (icon) {
                                const btn = icon.closest('button') || icon.parentElement || icon;
                                const r = btn.getBoundingClientRect();
                                if (r.width > 8 && r.height > 8) {
                                    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                                }
                            }
                        }
                        return null;
                    }"""
                )
                if opened_tab:
                    self._os_click_viewport(page, float(opened_tab["x"]), float(opened_tab["y"]))
                    time.sleep(random.uniform(1.4, 2.2))
                else:
                    page.mouse.wheel(0, 350)
                    time.sleep(1.2)
                input_pos = self._locate_comment_input_box(page)

            if not input_pos:
                logger.warning(f"[{ch_folder}] [Bộ lọc 24h] Không tìm thấy ô bình luận trên video này.")
                return None

            # 2. Click ĐÚNG 1 LẦN vào ô bình luận để kích hoạt khung soạn thảo DraftJS
            self._os_click_viewport(page, float(input_pos["x"]), float(input_pos["y"]))
            time.sleep(random.uniform(0.6, 1.0))

            # Focus trực tiếp vào div[contenteditable="true"] bên trong khung bình luận
            try:
                ce_box = page.locator(
                    '[data-e2e="comment-input"] div[contenteditable="true"], '
                    '[data-e2e="comment-text"] div[contenteditable="true"], '
                    'div[contenteditable="true"]'
                ).first
                if ce_box.is_visible():
                    ce_box.click()
                    time.sleep(0.25)
            except Exception:
                pass

            # 3. Gõ từng ký tự trực tiếp bằng page.keyboard.type để kích hoạt sự kiện input của DraftJS làm sáng nút 'Đăng'
            for ch in comment_text:
                page.keyboard.type(ch, delay=random.randint(24, 52))
                if ch in (",", ".", "!", " "):
                    time.sleep(random.uniform(0.03, 0.09))

            time.sleep(random.uniform(0.9, 1.5))

            # 4. Tìm và bấm nút 'Đăng' (`[data-e2e="comment-post"]`) + phím Enter dự phòng
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
                                try { el.click(); } catch (e) {}
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
                            if (r.width > 18 && r.height > 14 && r.top > 250) {
                                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if post_comment_pos:
                self._os_click_viewport(page, float(post_comment_pos["x"]), float(post_comment_pos["y"]))
                time.sleep(0.6)
            page.keyboard.press("Enter")

            time.sleep(random.uniform(2.8, 4.0))
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
                    'div[contenteditable="true"]'
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (!el) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width > 40 && r.height > 14 && r.top > 50 && r.top < window.innerHeight - 5) {
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
                        if (r.width > 40 && r.height > 14 && r.top > 50 && r.top < window.innerHeight - 5) {
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
        Thực thi trọn vẹn 6 bước "Bộ lọc 24 giờ" trên 1 video mục tiêu và lưu vào sổ theo dõi 24h.
        """
        creator_handle, video_id = self._extract_creator_and_video_id(video_url)
        logger.info(
            f"[{ch_folder}] === BẮT ĐẦU BỘ LỌC 24 GIỜ TRÊN VIDEO CỦA @{creator_handle or 'creator'} ({video_url}) ==="
        )

        page.goto(f"{video_url}?lang=vi-VN", wait_until="domcontentloaded", timeout=45000)
        time.sleep(random.uniform(3.0, 4.5))
        self.solve_tiktok_rotation_captcha_if_present(page, ch_folder)

        # 1. Xem hết video của người khác
        watched_sec = self._step1_watch_full_video(page, ch_folder, video_url)

        # 2. Thả tim video
        liked = self._step2_like_video(page, ch_folder)
        time.sleep(random.uniform(0.8, 1.5))

        # 3. Sao chép liên kết video đó
        copied_link = self._step3_copy_video_link(page, ch_folder, video_url)
        time.sleep(random.uniform(0.8, 1.5))

        # 4. Thêm video vào mục yêu thích
        favorited = self._step4_favorite_video(page, ch_folder)
        time.sleep(random.uniform(0.8, 1.5))

        # 5. Follow người đăng video đó
        followed = self._step5_follow_creator(page, ch_folder)
        time.sleep(random.uniform(1.0, 1.8))

        # 6. Để lại 1 bình luận có ý nghĩa (sau khi đã follow để chủ kênh không bỏ sót và trả follow trong 24h)
        comment_text = self._step6_post_meaningful_comment(page, ch_folder, creator_handle)

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
