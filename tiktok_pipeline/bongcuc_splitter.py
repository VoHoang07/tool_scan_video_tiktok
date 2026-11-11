import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from PIL import Image, ImageDraw, ImageFont

from .utils import ensure_ffmpeg, get_media_duration, get_video_resolution, logger

# ==============================================================================
# CẤU HÌNH NGUỒN YOUTUBE @bongcucaudio & DANH SÁCH TẬP ĐÃ ĐĂNG PHẦN 1 TRÊN TIKTOK
# ==============================================================================
BONGCUC_YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@bongcucaudio/videos"

# Tốc độ tua chuẩn khớp với video TikTok "Bông cúc số 141" (390.8s YouTube -> 279.7s TikTok ~ 1.42x)
BONGCUC_SPEED_FACTOR = 1.42

# Thời lượng mục tiêu mỗi phần sau khi tua trên TikTok (~280s = 4 phút 40 giây)
BONGCUC_TARGET_PART_SECONDS = 280.0

# Mốc thời gian kết thúc Phần 1 trên YouTube của các tập đã đăng Phần 1 (~390.8s = 6 phút 30.8 giây)
# Lùi lại 2.8 giây (388.0s) để Phần 2 nối tiếp mượt mà không bị mất chữ cuối câu của Phần 1
BONGCUC_PART1_END_YOUTUBE_SECONDS = 388.0

# Ngưỡng báo trước khi kho video từ @bongcucaudio sắp cạn (15 phần ~ trước 5 ngày đăng bài)
BONGCUC_LOW_QUEUE_WARNING_THRESHOLD = 15

# Kho video nền MỚI (Background Visual Pool v5) thay thế hoàn toàn hình nền cũ của Bông Cúc theo khung giờ Ngày / Đêm:
# - Ban đêm (17:00 - 05:59): Ghép nền Cinema Mukbang ăn đêm (mì cay, gà sốt, lẩu tokbokki đêm khuya - tắt 100% tiếng gốc).
# - Ban ngày (06:00 - 16:59): Ghép nền Pha chế Cafe / Bánh Souffle / Ẩm thực đường phố Hàn Quốc 1080p hoàn toàn mới.
BONGCUC_NIGHT_MUKBANG_POOL: List[Dict[str, Any]] = [
    {
        "yt_id": "Aq0fq99yzT0",
        "title": "Secretly eating Ramen at Night Cinema Mukbang DoNam 1080p",
        "duration": 360.0,
    },
    {
        "yt_id": "oxKWOF_iZms",
        "title": "Late Night Spicy Tteokbokki & Hot Dog Cinema Mukbang DoNam 1080p",
        "duration": 478.0,
    },
    {
        "yt_id": "hp8HuKWOUDU",
        "title": "Fire Noodles Tortilla & Sausages Late Night Cinema Mukbang DoNam 1080p",
        "duration": 659.0,
    },
    {
        "yt_id": "2j30NlKR9Cc",
        "title": "Budaejjigae Sausage Stew Late Night Cinema Mukbang DoNam 1080p",
        "duration": 623.0,
    },
    {
        "yt_id": "BaeKvKFSlDA",
        "title": "Crispy Honey Combo Chicken and Spicy Cheese Buldak Noodle Night Mukbang 1080p",
        "duration": 620.0,
    },
    {
        "yt_id": "6WUvI4wTxjA",
        "title": "Crispy Cheese Sticks Fried Chicken & Carbo Fire Noodles Night Mukbang 1080p",
        "duration": 522.0,
    },
    {
        "yt_id": "qAq1IC_LcKk",
        "title": "Late Night Convenience Store Feast Cinema Mukbang DoNam 1080p",
        "duration": 500.0,
    },
    {
        "yt_id": "AW58yRcZE1s",
        "title": "Midnight Crispy Fried Chicken Cinema Mukbang DoNam 1080p",
        "duration": 533.0,
    },
]

BONGCUC_DAY_AESTHETIC_POOL: List[Dict[str, Any]] = [
    {
        "yt_id": "3QAK8M-KmAo",
        "title": "Cream Cheese Whipping Cream Waffle Korean Street Dessert 1080p",
        "duration": 458.0,
    },
    {
        "yt_id": "0Y-yqybzfpg",
        "title": "Korean Street Food Bungeoppang Fish Waffles Gwangjang Market 1080p",
        "duration": 501.0,
    },
    {
        "yt_id": "mhpQaVsMBpk",
        "title": "Fluffy Fruit Souffle Pancake Making Artisan Dessert 1080p",
        "duration": 506.0,
    },
    {
        "yt_id": "bau7XQrO7Wc",
        "title": "Steamed Sweet Pancakes Hotteok Making Artisan Street Food 1080p",
        "duration": 484.0,
    },
    {
        "yt_id": "oWD8CaQtsIo",
        "title": "Cozy Cafe Vlog Artisan Ice Cream & Latte Making 1080p",
        "duration": 663.0,
    },
    {
        "yt_id": "KDZ6CEtZ8KI",
        "title": "Aesthetic Cafe Vlog Colorful Iced Drinks Preparation 1080p",
        "duration": 666.0,
    },
]


def get_target_bg_mode_for_hour(hour: Optional[int] = None) -> str:
    """
    Xác định chế độ hình nền (`night_mukbang` hoặc `day_aesthetic`) theo khung giờ trong ngày:
    - Ban đêm (`17:00` -> `05:59`, bao gồm khung tối & đêm khuya): `night_mukbang` (Cinema Mukbang đồ ăn đêm).
    - Ban ngày (`06:00` -> `16:59`): `day_aesthetic` (Cafe & Bánh ngọt nghệ thuật mới).
    """
    if hour is None:
        hour = datetime.now().hour
    if hour >= 17 or hour < 6:
        return "night_mukbang"
    return "day_aesthetic"


def parse_bongcuc_bg_mode(filename_or_stem: str) -> Optional[str]:
    """Trích xuất chế độ background (`night_mukbang` hoặc `day_aesthetic`) từ tên file video đã xuất."""
    lower = str(filename_or_stem or "").lower()
    if "_bg_mukbang" in lower:
        return "night_mukbang"
    if "_bg_daylight" in lower:
        return "day_aesthetic"
    return None

# Danh sách các tập "Bông Cúc Trắng" đã chiếu Phần 1 trên kênh TikTok `audio_dem_khuyen`
# Được xếp theo thứ tự ưu tiên xen kẽ (so le) để không bao giờ đăng 2 phần của cùng 1 tập liên tiếp
BONGCUC_EPISODES_CATALOG: List[Dict[str, Any]] = [
    {
        "ep_num": 141,
        "yt_id": "i_pJ8eZCEt0",
        "title": "Đại Tiểu Thư Thật Và Kẻ Mạo Danh",
        "part1_posted": True,
    },
    {
        "ep_num": 142,
        "yt_id": "aD5x_Ek_VSY",
        "title": "Người Lạ Năm Xưa Cuối Cùng Đã Trở Lại",
        "part1_posted": True,
    },
    {
        "ep_num": 143,
        "yt_id": "2lzzVdKb4sQ",
        "title": "Tôi Là Bạn Gái Hờ Của Thiếu Gia Bảy Năm",
        "part1_posted": True,
    },
    {
        "ep_num": 144,
        "yt_id": "3zVIg5dAhAo",
        "title": "Tôi Ghét Nhất Những Lời Xin Lỗi Muộn Màng",
        "part1_posted": True,
    },
    {
        "ep_num": 146,
        "yt_id": "l9fQrhwWLD0",
        "title": "Vừa Sinh Đôi, Tôi Đã Bị Chồng Nghi Ngoại Tình",
        "part1_posted": True,
    },
    {
        "ep_num": 137,
        "yt_id": "p2cdDyKx-q4",
        "title": "Ly Hôn Xong, Tôi Sống Cuộc Đời Của Mình",
        "part1_posted": True,
    },
    {
        "ep_num": 119,
        "yt_id": "rVdYOsTHDrE",
        "title": "Cả Nhà Cười Khi Chồng Tôi Không Được Chia Nhà",
        "part1_posted": True,
    },
    {
        "ep_num": 138,
        "yt_id": "wS9sTH05PKM",
        "title": "Kiếp Này, Tôi Dứt Khoát Rời Xa Anh",
        "part1_posted": True,
    },
    {
        "ep_num": 110,
        "yt_id": "2cmcDDxu_YY",
        "title": "Ta Không Gả, Hắn Cũng Không Cưới",
        "part1_posted": True,
    },
    # Các tập mới trên kênh YouTube @bongcucaudio (bắt đầu từ Phần 1 -> Phần Cuối, xen kẽ cùng các tập trên)
    {
        "ep_num": 148,
        "yt_id": "_0AnBPq_Ed0",
        "title": "Tôi Không Cưới Nữa, Căn Nhà Cũng Đừng Hòng Mơ",
        "part1_posted": False,
    },
    {
        "ep_num": 147,
        "yt_id": "-34FiriZdcc",
        "title": "Ngày Bà Tôi Qua Đời, Tình Yêu Cũng Chấm Dứt",
        "part1_posted": False,
    },
    {
        "ep_num": 140,
        "yt_id": "SjFTZVaDrBs",
        "title": "Đến Nhà Con Gái Dưỡng Già, Tôi Mới Thấy Mình Sai Ở Đâu",
        "part1_posted": False,
    },
    {
        "ep_num": 139,
        "yt_id": "0lXT5nPZ2fE",
        "title": "Bạn Gái Bảy Năm Không Bằng Đồng Nghiệp",
        "part1_posted": False,
    },
    {
        "ep_num": 136,
        "yt_id": "iA7bNl_uOKU",
        "title": "Bạn Cùng Phòng Muốn Cướp Vận May Của Tôi",
        "part1_posted": False,
    },
    {
        "ep_num": 135,
        "yt_id": "I88dSuVY6-4",
        "title": "Ba Năm Không Danh Phận, Tôi Chẳng Cần Nữa",
        "part1_posted": False,
    },
    {
        "ep_num": 134,
        "yt_id": "ljx9MSi2tbA",
        "title": "Vợ Hờ Của Tổng Tài Chỉ Mê Tiền",
        "part1_posted": False,
    },
]


def make_bongcuc_story_id(ep_num: int, part_num: int) -> str:
    """Tạo mã story_id số duy nhất cho từng phần (ví dụ: Tập 141 Phần 2 -> '990014102')."""
    return f"9900{int(ep_num):03d}{int(part_num):02d}"


def parse_bongcuc_ep_and_part(filename_or_id: str) -> Optional[Tuple[int, int]]:
    """
    Trích xuất `(ep_num, part_num)` từ tên file hoặc story_id:
    - Khớp `bongcuc_v2_s141_p2`, `bongcuc_s141_p2` hoặc `990014102`.
    - Khớp các video Phần 1 cũ đã đăng của `@bongcucaudio04`.
    """
    text = str(filename_or_id or "")
    m = re.search(r"bongcuc_(?:v\d+_)?s(\d+)_p(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    m2 = re.search(r"\b9900(\d{3})(\d{2})\b", text)
    if m2:
        return int(m2.group(1)), int(m2.group(2))
    legacy_p1_map = {
        "7698309790610640148": (141, 1),
        "7698192203855809812": (141, 1),
        "7701981706227698964": (141, 1),
        "7698906930793680148": (142, 1),
        "7698739758792543508": (142, 1),
        "7699354713556323590": (143, 1),
        "7699307811645410580": (143, 1),
        "7700057407061331220": (144, 1),
        "7700764387950300436": (145, 1),
        "7701190176676121877": (146, 1),
        "7701531433168358677": (146, 1),
        "7697500395819650310": (138, 1),
        "7697638039421455637": (137, 1),
        "7697778349690670357": (119, 1),
        "7700254944028364052": (110, 1),
        "7700149454669843732": (70, 1),
        "7698263475457592596": (10, 1),
    }
    for tid, pair in legacy_p1_map.items():
        if tid in text:
            return pair
    return None


class BongCucYouTubeSplitter:
    """
    Bộ tải & cắt tự động các phần còn lại của chuỗi truyện "Bông Cúc Trắng" từ kênh YouTube gốc
    `https://www.youtube.com/@bongcucaudio` cho kênh TikTok `audio_dem_khuyen`:
    1. Tải bản Full từ YouTube `@bongcucaudio` (lưu cache tại `workspace_temp/bongcuc_yt_cache/`).
    2. Với các tập đã đăng Phần 1 trên TikTok (`00:00 -> 06:30.8`), tự động cắt tiếp từ `388.0s`
       thành các phần có **thời lượng đều nhau** (`~280s` = 4m40s sau khi tua `1.42x`).
    3. Chuyển đổi sang khung hình dọc `1080x1920` (cắt bỏ toàn bộ watermark YouTube ở rìa,
       làm nền mờ nghệ thuật + đóng tiêu đề `"BÔNG CÚC SỐ XXX - PHẦN Y / Z"`).
    4. Sắp xếp hàng đợi **SO LE / XEN KẼ** giữa các tập (`Tập 141 P2 -> Tập 142 P2 -> Tập 143 P2 ... -> Tập 141 P3`),
       đảm bảo không bao giờ đăng 2 phần của cùng 1 tập liên tiếp nhau.
    """

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.cache_dir = self.project_root / "workspace_temp" / "bongcuc_yt_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.meta_cache_path = self.cache_dir / "episodes_duration_cache.json"
        self.catalog_path = self.project_root / "data" / "bongcuc_full_catalog.json"
        self.alert_path = self.project_root / "BONGCUC_INVENTORY_ALERT.json"
        self.ffmpeg_bin, _ = ensure_ffmpeg()

    def get_full_catalog(self) -> List[Dict[str, Any]]:
        """
        Đọc toàn bộ danh mục 148+ tập truyện từ kênh YouTube gốc `https://www.youtube.com/@bongcucaudio`
        (`data/bongcuc_full_catalog.json`), ưu tiên các tập đã đăng Phần 1 lên đầu rồi đến toàn bộ các tập
        từ mới nhất về Số 01 (tự động loại bỏ các tập bị khóa bởi TikTokCommunityGuidelinesGuard).
        """
        from .community_guidelines_guard import TikTokCommunityGuidelinesGuard

        if self.catalog_path.exists():
            try:
                data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) >= len(BONGCUC_EPISODES_CATALOG):
                    return [
                        e
                        for e in data
                        if not TikTokCommunityGuidelinesGuard.is_episode_blocked(
                            "audio_dem_khuyen", int(e.get("ep_num") or 0)
                        )
                    ]
            except Exception:
                pass
        return [
            e
            for e in BONGCUC_EPISODES_CATALOG
            if not TikTokCommunityGuidelinesGuard.is_episode_blocked(
                "audio_dem_khuyen", int(e.get("ep_num") or 0)
            )
        ]

    def sync_new_episodes_from_youtube(self) -> int:
        """
        Tự động quét kênh YouTube `https://www.youtube.com/@bongcucaudio/videos` để cập nhật thêm
        các tập mới đăng (ví dụ: Số 149, 150...) vào `data/bongcuc_full_catalog.json`.
        Trả về số lượng tập mới phát hiện được.
        """
        try:
            import yt_dlp
            from .community_guidelines_guard import TikTokCommunityGuidelinesGuard

            current_cat = self.get_full_catalog()
            known_yt_ids = {str(item.get("yt_id")) for item in current_cat}
            part1_posted_eps = {141, 142, 143, 144, 146, 137, 119, 138, 110, 70, 10}

            with yt_dlp.YoutubeDL(
                {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
            ) as ydl:
                info = ydl.extract_info(BONGCUC_YOUTUBE_CHANNEL_URL, download=False)
                entries = (info or {}).get("entries") or []

            added = 0
            for e in entries:
                vid_id = str(e.get("id") or "").strip()
                raw_title = str(e.get("title") or "").strip()
                if not vid_id or not raw_title or vid_id in known_yt_ids:
                    continue
                m = re.search(r"SỐ\s*(\d+)", raw_title, re.IGNORECASE)
                ep_num = int(m.group(1)) if m else 0
                if ep_num <= 0 or TikTokCommunityGuidelinesGuard.is_episode_blocked("audio_dem_khuyen", ep_num):
                    continue
                parts = [
                    p.strip()
                    for p in re.split(r"\s+II\s+|\s+\|\|\s+|\s+\|\s+", raw_title)
                    if p.strip()
                ]
                clean_title = parts[1] if len(parts) >= 2 else raw_title
                clean_title = re.sub(
                    r"(?i)\b(tô màu|truyện audio full|truyện audio|truyện tận thế)\b",
                    "",
                    clean_title,
                ).strip(" -|I")
                current_cat.append(
                    {
                        "ep_num": ep_num,
                        "yt_id": vid_id,
                        "title": clean_title or raw_title,
                        "raw_title": raw_title,
                        "part1_posted": ep_num in part1_posted_eps,
                    }
                )
                known_yt_ids.add(vid_id)
                added += 1

            if added > 0:
                priority_order = [141, 142, 143, 144, 146, 137, 119, 145, 138, 110, 70, 10]
                prio_map = {ep: idx for idx, ep in enumerate(priority_order)}
                current_cat.sort(
                    key=lambda x: (
                        0 if int(x.get("ep_num", 0)) in prio_map else 1,
                        prio_map.get(int(x.get("ep_num", 0)), -int(x.get("ep_num", 0))),
                    )
                )
                self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
                self.catalog_path.write_text(
                    json.dumps(current_cat, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                logger.info(
                    f"[BongCucSplitter] Đã đồng bộ thêm {added} tập mới từ YouTube @bongcucaudio (Tổng cộng: {len(current_cat)} tập)."
                )
            return added
        except Exception as exc:
            logger.debug(f"[BongCucSplitter] Không thể quét tập mới từ YouTube: {exc}")
            return 0

    def _load_duration_cache(self) -> Dict[str, float]:
        defaults = {
            "i_pJ8eZCEt0": 2217.5,
            "aD5x_Ek_VSY": 2489.4,
            "2lzzVdKb4sQ": 3060.0,
            "3zVIg5dAhAo": 2830.0,
            "l9fQrhwWLD0": 3085.0,
            "p2cdDyKx-q4": 3482.0,
            "rVdYOsTHDrE": 2497.0,
            "e9oSX2s0ARs": 3110.0,
            "wS9sTH05PKM": 6661.0,
            "2cmcDDxu_YY": 6425.0,
            "8Nhl2wfWKL8": 6798.0,
            "DoVFGuEeB1k": 5122.0,
            "_0AnBPq_Ed0": 6502.0,
            "-34FiriZdcc": 1998.0,
            "SjFTZVaDrBs": 4069.0,
            "0lXT5nPZ2fE": 4920.0,
            "iA7bNl_uOKU": 2056.0,
            "I88dSuVY6-4": 3124.0,
            "ljx9MSi2tbA": 3694.0,
            "KTwmqKf6YCE": 6422.0,
            "X7xc_AG99NI": 4320.0,
            "Fjf5HPbuuCU": 3150.0,
            "f5FIbKC8Nkw": 14419.0,
            "ABe8CPAveU0": 3096.0,
            "u7if63bVGDQ": 3014.0,
        }
        if self.meta_cache_path.exists():
            try:
                loaded = json.loads(self.meta_cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    return {**defaults, **loaded}
            except Exception:
                pass
        return defaults

    def _save_duration_cache(self, data: Dict[str, float]) -> None:
        try:
            self.meta_cache_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def get_youtube_video_duration(self, yt_id: str, allow_network: bool = True) -> float:
        """Lấy thời lượng gốc (giây) của video YouTube từ cache hoặc qua yt-dlp."""
        dur_cache = self._load_duration_cache()
        if yt_id in dur_cache and dur_cache[yt_id] > 60:
            return float(dur_cache[yt_id])

        cached_mp4 = self.cache_dir / f"yt_{yt_id}.mp4"
        if cached_mp4.exists() and cached_mp4.stat().st_size > 1_000_000:
            dur = get_media_duration(cached_mp4)
            if dur and dur > 60:
                dur_cache[yt_id] = float(dur)
                self._save_duration_cache(dur_cache)
                return float(dur)

        if not allow_network:
            return 3200.0

        try:
            import yt_dlp

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "ignore_no_formats_error": True,
                "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={yt_id}", download=False
                )
                dur = float((info or {}).get("duration") or 0.0)
                if dur > 60:
                    dur_cache[yt_id] = dur
                    self._save_duration_cache(dur_cache)
                    return dur
        except Exception as exc:
            logger.debug(f"[BongCucSplitter] Không lấy được duration cho {yt_id}: {exc}")
        return 3200.0

    def compute_episode_parts_plan(
        self, ep_info: Dict[str, Any], allow_network: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Tính toán danh sách các phần có thời lượng ĐỀU NHAU cho một tập Bông Cúc:
        - Nếu `part1_posted == True`: Phần 1 (0 -> 390.8s) đã đăng trên TikTok -> Cắt từ `388.0s` đến hết video
          thành `N` phần có thời lượng bằng nhau (~270s - 290s sau khi tua `1.42x`), đánh số từ `Phần 2` đến `Phần N+1`.
        - Nếu `part1_posted == False`: Cắt từ `0.0s` đến hết video thành `M` phần bằng nhau, đánh số từ `Phần 1` đến `Phần M`.
        """
        ep_num = int(ep_info["ep_num"])
        yt_id = str(ep_info["yt_id"])
        title = str(ep_info["title"])
        part1_posted = bool(ep_info.get("part1_posted", False))
        view_count = int(ep_info.get("view_count") or 0)

        cat_dur = float(ep_info.get("duration") or 0.0)
        if cat_dur > 60.0:
            total_dur = cat_dur
        else:
            total_dur = self.get_youtube_video_duration(yt_id, allow_network=allow_network)
        target_orig_chunk = BONGCUC_TARGET_PART_SECONDS * BONGCUC_SPEED_FACTOR  # ~397.6s

        parts_plan: List[Dict[str, Any]] = []
        if part1_posted:
            start_offset = BONGCUC_PART1_END_YOUTUBE_SECONDS
            rem_dur = max(120.0, total_dur - start_offset)
            num_rem_parts = max(1, round(rem_dur / target_orig_chunk))
            slice_orig_dur = rem_dur / num_rem_parts
            total_parts = 1 + num_rem_parts
            start_part_idx = 2
            # Luôn tạo sẵn thông số Phần 1 (16.5s -> 388.0s) để nếu Phần 1 bị mất trên kênh thì tự động render & đăng lại
            p1_start = 16.5
            p1_end = min(total_dur, BONGCUC_PART1_END_YOUTUBE_SECONDS)
            p1_dur_orig = max(30.0, p1_end - p1_start)
            parts_plan.append(
                {
                    "ep_num": ep_num,
                    "part_num": 1,
                    "total_parts": total_parts,
                    "is_final_part": total_parts == 1,
                    "yt_id": yt_id,
                    "view_count": view_count,
                    "source_url": f"https://www.youtube.com/watch?v={yt_id}&t={int(p1_start)}s",
                    "title": title,
                    "story_id": make_bongcuc_story_id(ep_num, 1),
                    "start_sec": round(p1_start, 2),
                    "end_sec": round(p1_end, 2),
                    "orig_duration": round(p1_dur_orig, 2),
                    "tiktok_duration": round(p1_dur_orig / BONGCUC_SPEED_FACTOR, 1),
                }
            )
        else:
            # Cắt bỏ 16.5s đầu tiên của Phần 1 trên YouTube (nơi audio gốc giới thiệu "Bông Cúc số...")
            start_offset = 16.5
            rem_dur = max(120.0, total_dur - start_offset)
            num_rem_parts = max(1, round(rem_dur / target_orig_chunk))
            slice_orig_dur = rem_dur / num_rem_parts
            total_parts = num_rem_parts
            start_part_idx = 1

        for idx in range(num_rem_parts):
            part_num = start_part_idx + idx
            seg_start = start_offset + idx * slice_orig_dur
            # Lùi nhẹ 1.5s ở đầu mỗi phần (từ phần 2 trở đi) để câu văn liền mạch
            if idx > 0:
                seg_start = max(0.0, seg_start - 1.5)
            seg_end = min(total_dur, start_offset + (idx + 1) * slice_orig_dur)
            seg_dur_orig = max(30.0, seg_end - seg_start)
            seg_dur_tiktok = round(seg_dur_orig / BONGCUC_SPEED_FACTOR, 1)
            story_id = make_bongcuc_story_id(ep_num, part_num)

            parts_plan.append(
                {
                    "ep_num": ep_num,
                    "part_num": part_num,
                    "total_parts": total_parts,
                    "is_final_part": part_num == total_parts,
                    "yt_id": yt_id,
                    "view_count": view_count,
                    "source_url": f"https://www.youtube.com/watch?v={yt_id}&t={int(seg_start)}s",
                    "title": title,
                    "story_id": story_id,
                    "start_sec": round(seg_start, 2),
                    "end_sec": round(seg_end, 2),
                    "orig_duration": round(seg_dur_orig, 2),
                    "tiktok_duration": seg_dur_tiktok,
                }
            )
        return parts_plan

    def check_and_alert_low_inventory(
        self, remaining_parts: int, remaining_episodes: int
    ) -> Dict[str, Any]:
        """
        Kiểm tra số lượng phần truyện còn lại của kênh `@bongcucaudio` và BÁO TRƯỚC cho người dùng
        khi sắp hết video (`remaining_parts <= BONGCUC_LOW_QUEUE_WARNING_THRESHOLD` hoặc `== 0`).
        """
        est_days = round(remaining_parts / 3.0, 1)
        is_exhausted = remaining_parts == 0
        is_low = remaining_parts <= BONGCUC_LOW_QUEUE_WARNING_THRESHOLD

        status_payload = {
            "channel": "audio_dem_khuyen",
            "exclusive_source": BONGCUC_YOUTUBE_CHANNEL_URL,
            "remaining_parts": remaining_parts,
            "remaining_episodes": remaining_episodes,
            "estimated_days_left_at_3_posts_per_day": est_days,
            "status": "EXHAUSTED" if is_exhausted else ("LOW_WARNING" if is_low else "HEALTHY"),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            self.alert_path.write_text(
                json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        if is_low:
            # Quét thử YouTube @bongcucaudio xem có tập mới đăng không
            newly_added = self.sync_new_episodes_from_youtube()
            if is_exhausted and newly_added == 0:
                alert_msg = (
                    "🚨 [BÁO TRƯỚC HẾT VIDEO @bongcucaudio] Kênh audio_dem_khuyen ĐÃ CẮT & ĐĂNG HẾT 100% "
                    "các tập trên kênh YouTube @bongcucaudio! Hệ thống tạm dừng đăng kênh audio_dem_khuyen (tuyệt đối không lấy nguồn ngoài) và chờ tập mới."
                )
            else:
                alert_msg = (
                    f"⚠️ [BÁO TRƯỚC SẮP HẾT VIDEO @bongcucaudio] Kho nguồn của kênh audio_dem_khuyen chỉ còn "
                    f"{remaining_parts} phần ({remaining_episodes} tập ~ đủ đăng {est_days} ngày)! "
                    f"Vui lòng chuẩn bị nguồn tiếp theo hoặc chờ @bongcucaudio ra tập mới."
                )
            print("\n" + "!" * 96, flush=True)
            print(alert_msg, flush=True)
            print("!" * 96 + "\n", flush=True)
            logger.warning(alert_msg)

            # Hiển thị thông báo macOS trên màn hình để người dùng biết trước
            try:
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        f'display notification "{alert_msg}" with title "⚠️ Báo Trước Kênh audio_dem_khuyen (@bongcucaudio)"',
                    ],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except Exception:
                pass
        return status_payload

    @staticmethod
    def get_lost_story_ids_needing_repost(project_root: Path) -> Set[str]:
        """
        Xác định danh sách các `story_id` (`9900...`) đã bị mất / bị xóa / thiếu trên kênh `audio_dem_khuyen`
        cần được TỪ TỪ ĐĂNG LẠI với cấu trúc `bongcuc_v6_s` (có giữ `BC SỐ <ep_num>` trong khung kể chuyện):
        1. Bao gồm các tập/phần trước đây bị xóa, bị mất khi dọn kho, hoặc đăng thiếu số tập/phần trên kênh.
        2. Kết hợp tự động với file `data/audio_dem_khuyen_live_posts.json` (do trình duyệt quét trực tiếp từ
           trang `tiktokstudio/content` của kênh `audio_dem_khuyen`) để phát hiện mọi video từng đăng nhưng hiện
           đã biến mất khỏi kênh.
        3. Loại trừ các `story_id` đã có file `bongcuc_v6_s` trong `output/audio_dem_khuyen/ready/` hoặc `posted/`.
        """
        proj = Path(project_root)
        historical_channel_ids: Set[str] = {
            "990001001", "990001002", "990001003", "990001004", "990001005", "990001006", "990001007",
            "990002101", "990002102",
            "990003501", "990003502",
            "990003901", "990003902",
            "990004501", "990005101", "990005401", "990005601",
            "990006001", "990006002",
            "990007001", "990007002",
            "990008901", "990010101",
            "990011001", "990011002",
            "990011901", "990011902",
            "990012001", "990012002",
            "990013701", "990013801",
            "990014101", "990014102",
            "990014201", "990014202",
            "990014301", "990014302",
            "990014401", "990014402",
            "990014501",
            "990014601", "990014602",
        }
        # Các video đã xác nhận bị mất / xóa trên kênh hoặc bị kẹt chưa lên kênh với khung "BC Số"
        lost_ids: Set[str] = {
            "990004501",  # BC Số 45 Phần 1 (đã bị xóa khỏi kênh)
            "990005101",  # BC Số 51 Phần 1 (mất khi dọn kho trước khi lên kênh)
            "990005401",  # BC Số 54 Phần 1 (mất khi dọn kho trước khi lên kênh)
            "990005601",  # BC Số 56 Phần 1 (mất khi dọn kho trước khi lên kênh)
            "990008901",  # BC Số 89 Phần 1 (mất khi dọn kho trước khi lên kênh)
            "990010101",  # BC Số 101 Phần 1 (mất khi dọn kho trước khi lên kênh)
            "990001002",  # BC Số 10 Phần 2 (bản cũ bị mất tên tập/phần)
            "990001003",  # BC Số 10 Phần 3 (bản cũ bị mất tên tập/phần)
            "990003901",  # BC Số 39 Phần 1 (bản đầu bị xóa, bản sau mất tên tập/phần)
            "990011001",  # BC Số 110 Phần 1 (bản cũ mất tên tập/phần)
            "990013701",  # BC Số 137 Phần 1 (bản cũ mất tên tập/phần)
            "990013801",  # BC Số 138 Phần 1 (bản cũ mất tên tập/phần)
            "990014301",  # BC Số 143 Phần 1 (bản cũ mất tên tập/phần)
            "990014401",  # BC Số 144 Phần 1 (bản cũ mất tên tập/phần)
            "990012002",  # BC Số 120 Phần 2 (bản v5 thiếu chữ BC Số trong khung kể chuyện)
            "990003902",  # BC Số 39 Phần 2 (chưa lên kênh)
            "990006002",  # BC Số 60 Phần 2 (chưa lên kênh)
            "990003502",  # BC Số 35 Phần 2 (chưa lên kênh)
            "990011002",  # BC Số 110 Phần 2 (chưa lên kênh)
        }

        live_inv_path = proj / "data" / "audio_dem_khuyen_live_posts.json"
        if live_inv_path.exists():
            try:
                inv_data = json.loads(live_inv_path.read_text(encoding="utf-8"))
                live_sids = {str(x) for x in (inv_data.get("live_story_ids") or []) if x}
                if len(live_sids) >= 3:
                    for h_sid in historical_channel_ids:
                        if h_sid not in live_sids:
                            lost_ids.add(h_sid)
                for rep_sid in inv_data.get("reposted_v6_story_ids") or []:
                    lost_ids.discard(str(rep_sid))
            except Exception:
                pass

        # Chỉ loại khỏi danh sách cần đăng lại khi bản bongcuc_v6_s ĐÃ ĐƯỢC ĐĂNG (nằm trong posted/ hoặc registry history v6)
        posted_v6_dir = proj / "output" / "audio_dem_khuyen" / "posted"
        if posted_v6_dir.exists():
            for mp4 in posted_v6_dir.glob("*bongcuc_v6_s*.mp4"):
                ep_pt = parse_bongcuc_ep_and_part(mp4.name)
                if ep_pt:
                    lost_ids.discard(make_bongcuc_story_id(ep_pt[0], ep_pt[1]))

        reg_path = proj / "uploaded_videos_registry.json"
        if reg_path.exists():
            try:
                reg_d = json.loads(reg_path.read_text(encoding="utf-8"))
                for h_item in reg_d.get("history") or []:
                    if isinstance(h_item, dict) and "bongcuc_v6_s" in str(h_item.get("filename") or "").lower():
                        ep_pt = parse_bongcuc_ep_and_part(str(h_item.get("filename") or ""))
                        if ep_pt:
                            lost_ids.discard(make_bongcuc_story_id(ep_pt[0], ep_pt[1]))
            except Exception:
                pass

        return lost_ids

    def build_interleaved_queue(
        self, known_posted_or_ready_ids: Set[str]
    ) -> List[Dict[str, Any]]:
        """
        Xây dựng hàng đợi độc quyền 100% từ kênh YouTube `@bongcucaudio` (148+ tập truyện)
        theo chiến lược:
        1. **TỪ TỪ ĐĂNG LẠI CÁC VIDEO ĐÃ MẤT TRONG KÊNH (`lost_recovery_specs`)**:
           - Phát hiện toàn bộ các tập/phần đã mất trên kênh (`get_lost_story_ids_needing_repost`) và đan xen
             từ từ vào lịch đăng (`1 Video Đã Mất Cần Đăng Lại : 1 Phần Tiếp Theo`) để khôi phục đầy đủ kho truyện
             trên kênh mà không gây spam.
        2. **ĐỒNG BỘ CẤP ĐỘ PHẦN (LEVEL-SYNCHRONIZED WAVE: PHẦN 2 -> PHẦN 3 -> PHẦN 4...)
           & ƯU TIÊN TẬP NHIỀU VIEW TRÊN KÊNH**.
        """
        catalog = self.get_full_catalog()

        # Tập hợp các story_id đã từng đăng trên kênh audio_dem_khuyen
        confirmed_posted_ids: Set[str] = {
            "990001001", "990001002", "990001003", "990001004", "990001005", "990001006", "990001007",
            "990014101", "990014102", "990014201", "990014202", "990014301", "990014302",
            "990014401", "990014402", "990014601", "990014602",
            "990011901", "990011902", "990007001", "990007002", "990011001", "990014501",
            "990013801", "990013701", "990003901", "990006001", "990003501",
            "990002101", "990002102", "990012001",
        }
        lost_story_ids = self.get_lost_story_ids_needing_repost(self.project_root)
        # Mở khóa toàn bộ các story_id đã bị mất trên kênh (trừ những file đã có sẵn bản v6 trong ready/)
        effective_known_ids: Set[str] = (set(known_posted_or_ready_ids) | confirmed_posted_ids) - lost_story_ids

        # Trọng số ưu tiên đặc biệt cho các tập đang có view cao trên kênh TikTok audio_dem_khuyen & YouTube
        high_view_channel_prio: Dict[int, int] = {
            21: 1000,   # 414 TikTok views (17 tim) + 90,987 YT views
            120: 950,   # 449 TikTok views (11 tim) + 88,376 YT views
            119: 900,   # 738 TikTok views (9 tim) + 245,499 YT views
            70: 850,    # 240 TikTok views (5 tim) + 135,832 YT views
            39: 800,    # 112,208 YT views
            60: 750,    # 102,409 YT views
            35: 700,    # 96,812 YT views
            110: 650,   # 55,433 YT views
            141: 600,   # 40,489 YT views
            10: 520,    # 485 TikTok views (12 tim)
        }

        # Phân loại các tập đã từng mở trên kênh và các tập mới chưa chiếu Phần 1
        active_cont_eps: List[Dict[str, Any]] = []
        brand_new_eps: List[Dict[str, Any]] = []
        for ep in catalog:
            ep_num = int(ep.get("ep_num") or 0)
            p1_sid = make_bongcuc_story_id(ep_num, 1)
            has_any_part_known = any(
                sid.startswith(f"9900{ep_num:03d}")
                for sid in (effective_known_ids | lost_story_ids)
            )
            if bool(ep.get("part1_posted")) or p1_sid in effective_known_ids or has_any_part_known:
                active_cont_eps.append(ep)
            else:
                brand_new_eps.append(ep)

        # Sắp xếp tập mới ưu tiên độ dài chuẩn TikTok (4 - 13 phần, <= 5400s) + lượt view YouTube cao nhất
        brand_new_eps.sort(
            key=lambda x: (
                1 if 600 <= float(x.get("duration") or 3200) <= 5400 else 0,
                int(x.get("view_count") or 0),
                int(x.get("ep_num") or 0),
            ),
            reverse=True,
        )
        # Sắp xếp tập đang chiếu dở theo trọng số view kênh TikTok + lượt view YouTube giảm dần
        active_cont_eps.sort(
            key=lambda x: (
                high_view_channel_prio.get(int(x.get("ep_num") or 0), 0),
                1 if 600 <= float(x.get("duration") or 3200) <= 5400 else 0,
                int(x.get("view_count") or 0),
                int(x.get("ep_num") or 0),
            ),
            reverse=True,
        )

        lost_recovery_specs: List[Dict[str, Any]] = []
        fresh_part1_specs: List[Dict[str, Any]] = []
        new_eps_followup_lists: List[List[Dict[str, Any]]] = []
        cont_per_ep_lists: List[List[Dict[str, Any]]] = []

        remaining_eps_count = 0
        fetched_online_count = 0

        for ep_info in active_cont_eps:
            allow_net = False
            plan = self.compute_episode_parts_plan(ep_info, allow_network=allow_net)
            if allow_net:
                fetched_online_count += 1
            ep_num_cur = int(ep_info.get("ep_num") or 0)
            ep_lost = [
                p
                for p in plan
                if str(p["story_id"]) in lost_story_ids and str(p["story_id"]) not in effective_known_ids
            ]
            if ep_lost:
                lost_recovery_specs.extend(ep_lost)
            ep_has_lost_p1 = any(int(lp["part_num"]) == 1 for lp in ep_lost)
            ep_has_live_p2_plus = any(
                sid.startswith(f"9900{ep_num_cur:03d}") and not sid.endswith("01")
                for sid in effective_known_ids
            )
            pending = [
                p
                for p in plan
                if str(p["story_id"]) not in effective_known_ids
                and str(p["story_id"]) not in lost_story_ids
                and int(p["part_num"]) >= 2
            ]
            if pending or ep_lost:
                if pending:
                    if ep_has_lost_p1 and not ep_has_live_p2_plus:
                        new_eps_followup_lists.append(pending)
                    else:
                        cont_per_ep_lists.append(pending)
                remaining_eps_count += 1

        # Sắp xếp danh sách video đã mất cần đăng lại: ưu tiên Phần 1 -> Phần 2 -> Phần 3 và tập nhiều view
        lost_recovery_specs.sort(
            key=lambda p: (
                int(p.get("part_num") or 1),
                -high_view_channel_prio.get(int(p.get("ep_num") or 0), 0),
                -int(p.get("view_count") or 0),
                -int(p.get("ep_num") or 0),
            )
        )

        for ep_info in brand_new_eps:
            allow_net = False
            plan = self.compute_episode_parts_plan(ep_info, allow_network=allow_net)
            if allow_net:
                fetched_online_count += 1
            pending = [p for p in plan if str(p["story_id"]) not in effective_known_ids]
            if not pending:
                continue
            remaining_eps_count += 1
            if int(pending[0]["part_num"]) == 1:
                fresh_part1_specs.append(pending[0])
                if len(pending) > 1:
                    new_eps_followup_lists.append(pending[1:])
            else:
                cont_per_ep_lists.append(pending)

        # ĐỒNG BỘ CẤP ĐỘ PHẦN (LEVEL-SYNCHRONIZED WAVE BY part_num):
        parts_by_level: Dict[int, List[Dict[str, Any]]] = {}
        for ep_list in cont_per_ep_lists:
            for p_spec in ep_list:
                pt_num = int(p_spec["part_num"])
                parts_by_level.setdefault(pt_num, []).append(p_spec)

        continuation_specs: List[Dict[str, Any]] = []
        for pt_level in sorted(parts_by_level.keys()):
            level_items = parts_by_level[pt_level]
            level_items.sort(
                key=lambda p: (
                    high_view_channel_prio.get(int(p.get("ep_num") or 0), 0),
                    int(p.get("view_count") or 0),
                    int(p.get("ep_num") or 0),
                ),
                reverse=True,
            )
            continuation_specs.extend(level_items)

        batch_size = 10
        for b_start in range(0, len(new_eps_followup_lists), batch_size):
            sub_lists = new_eps_followup_lists[b_start : b_start + batch_size]
            sub_by_level: Dict[int, List[Dict[str, Any]]] = {}
            for ep_list in sub_lists:
                for p_spec in ep_list:
                    sub_by_level.setdefault(int(p_spec["part_num"]), []).append(p_spec)
            for pt_level in sorted(sub_by_level.keys()):
                continuation_specs.extend(sub_by_level[pt_level])

        # Đan xen TỪ TỪ: [1 Video Đã Mất Cần Đăng Lại] <-> [1 Phần Tiếp Theo] (và cứ 6 lượt chèn 1 Tập Mới Phần 1)
        interleaved: List[Dict[str, Any]] = []
        lost_idx = 0
        cont_idx = 0
        p1_idx = 0
        slot_counter = 0
        while (
            lost_idx < len(lost_recovery_specs)
            or cont_idx < len(continuation_specs)
            or p1_idx < len(fresh_part1_specs)
        ):
            for _ in range(3):
                if lost_idx < len(lost_recovery_specs):
                    l_item = dict(lost_recovery_specs[lost_idx])
                    l_item["bg_mode"] = "day_aesthetic" if (slot_counter % 2 == 0) else "night_mukbang"
                    l_item["is_lost_recovery"] = True
                    interleaved.append(l_item)
                    lost_idx += 1
                    slot_counter += 1
                if cont_idx < len(continuation_specs):
                    c_item = dict(continuation_specs[cont_idx])
                    c_item["bg_mode"] = "day_aesthetic" if (slot_counter % 2 == 0) else "night_mukbang"
                    interleaved.append(c_item)
                    cont_idx += 1
                    slot_counter += 1
            if p1_idx < len(fresh_part1_specs):
                p1_item = dict(fresh_part1_specs[p1_idx])
                p1_item["bg_mode"] = "day_aesthetic" if (slot_counter % 2 == 0) else "night_mukbang"
                interleaved.append(p1_item)
                p1_idx += 1
                slot_counter += 1

        self.check_and_alert_low_inventory(
            remaining_parts=len(interleaved),
            remaining_episodes=remaining_eps_count,
        )
        return interleaved

    def _ensure_ytdlp_macos_bin(self) -> Optional[Path]:
        """Đảm bảo có sẵn binary `yt-dlp_macos` (Universal x86_64 + arm64 bản mới nhất) để tải 1080p Full HD qua visionos API."""
        bin_path = self.project_root / "venv" / "bin" / "yt-dlp_macos"
        if bin_path.exists() and bin_path.stat().st_size > 10_000_000:
            return bin_path
        try:
            bin_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "curl",
                    "-sL",
                    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos",
                    "-o",
                    str(bin_path),
                ],
                timeout=90,
                check=False,
            )
            if bin_path.exists() and bin_path.stat().st_size > 10_000_000:
                os.chmod(bin_path, 0o755)
                return bin_path
        except Exception:
            pass
        return None

    def ensure_background_video(
        self, bg_mode: str = "day_aesthetic", seed_val: int = 0
    ) -> Tuple[Path, float]:
        """
        Đảm bảo có sẵn video nền (Background Visual) chuẩn Full HD 1080p (1920x1080, không cắt mất người) theo chế độ khung giờ:
        - `night_mukbang` (Khung giờ ban đêm 18:00 - 04:59): Video Mukbang 1080p có đầy đủ khuôn mặt & bàn ăn.
        - `day_aesthetic` (Khung giờ ban ngày 05:00 - 17:59): Video Nghệ thuật làm bánh kem / Gốm sứ / 3D Satisfying 1080p.
        Trả về `(bg_video_path, bg_start_sec)` với điểm bắt đầu `bg_start_sec` dịch chuyển thông minh
        để mỗi tập truyện hiển thị một món ăn / tác phẩm nghệ thuật hoàn toàn khác nhau.
        """
        eff_mode = "night_mukbang" if bg_mode == "night_mukbang" else "day_aesthetic"
        pool = (
            BONGCUC_NIGHT_MUKBANG_POOL
            if eff_mode == "night_mukbang"
            else BONGCUC_DAY_AESTHETIC_POOL
        )
        bg_dir = self.project_root / "workspace_temp" / "bongcuc_bg_pool" / eff_mode
        bg_dir.mkdir(parents=True, exist_ok=True)

        # Dọn sạch mọi file .part cũ bị dở dang
        for old_f in bg_dir.glob("*.part*"):
            if old_f.is_file():
                try:
                    old_f.unlink(missing_ok=True)
                except Exception:
                    pass

        import yt_dlp

        ordered_indices = [(seed_val + i) % len(pool) for i in range(len(pool))]
        chosen_mp4: Optional[Path] = None
        for idx in ordered_indices:
            cand_spec = pool[idx]
            yt_id = str(cand_spec["yt_id"])
            target_mp4 = bg_dir / f"bg_{yt_id}.mp4"
            if target_mp4.exists() and target_mp4.stat().st_size > 1_500_000:
                try:
                    w_chk, h_chk = get_video_resolution(target_mp4)
                except Exception:
                    w_chk, h_chk = 640, 360
                if h_chk >= 360 and w_chk >= 360:
                    chosen_mp4 = target_mp4
                    break
                target_mp4.unlink(missing_ok=True)
            tmp_out = bg_dir / f"bg_{yt_id}.part.mp4"
            tmp_out.unlink(missing_ok=True)
            url = f"https://www.youtube.com/watch?v={yt_id}"
            fmt_selector = "22/18/best[ext=mp4]/best"
            try:
                logger.info(
                    f"[BongCucSplitter] Đang tải video Background [{eff_mode}] từ YouTube ({url} - {cand_spec.get('title')})..."
                )
                ydl_opts = {
                    "format": fmt_selector,
                    "outtmpl": str(tmp_out),
                    "nopart": True,
                    "quiet": True,
                    "no_warnings": True,
                    "retries": 2,
                    "socket_timeout": 25,
                    "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                if tmp_out.exists() and tmp_out.stat().st_size > 1_500_000:
                    shutil.move(str(tmp_out), str(target_mp4))
                    chosen_mp4 = target_mp4
                    w_dl, h_dl = get_video_resolution(target_mp4)
                    logger.info(
                        f"[BongCucSplitter] Đã tải xong Background [{eff_mode}] {yt_id} ({w_dl}x{h_dl}, {target_mp4.stat().st_size / 1e6:.1f} MB)."
                    )
                    break
            except Exception as dl_exc:
                logger.warning(
                    f"[BongCucSplitter] Thử video Background tiếp theo trong pool [{eff_mode}] do lỗi tải {yt_id}: {dl_exc}"
                )
                tmp_out.unlink(missing_ok=True)

        if chosen_mp4 is None:
            existing = sorted(
                p for p in bg_dir.glob("bg_*.mp4") if p.is_file() and p.stat().st_size > 1_500_000
            )
            if not existing:
                other_root = self.project_root / "workspace_temp" / "bongcuc_bg_pool"
                existing = sorted(
                    p for p in other_root.rglob("bg_*.mp4") if p.is_file() and p.stat().st_size > 1_500_000
                )
            if existing:
                chosen_mp4 = existing[seed_val % len(existing)]
            else:
                raise RuntimeError(f"Không thể tải bất kỳ video Background nào cho chế độ {eff_mode}")

        bg_dur = get_media_duration(chosen_mp4) or 600.0
        max_offset = max(15, int(bg_dur - 260))
        bg_start_sec = float(8 + ((seed_val * 53) % max_offset)) if bg_dur > 300 else 5.0
        return chosen_mp4, round(bg_start_sec, 1)

    def ensure_youtube_full_video(self, yt_id: str) -> Path:
        """
        Tải video full từ kênh YouTube `@bongcucaudio` về `workspace_temp/bongcuc_yt_cache/yt_<yt_id>.mp4`
        bằng client `android,web` (`format 18`) tốc độ cao, tái sử dụng cho mọi phần của tập đó.
        """
        target_mp4 = self.cache_dir / f"yt_{yt_id}.mp4"
        if target_mp4.exists() and target_mp4.stat().st_size > 2_000_000:
            return target_mp4

        # Kiểm tra nếu đã có sẵn bản tải trong /tmp/yt_141_full.mp4
        if yt_id == "i_pJ8eZCEt0" and Path("/tmp/yt_141_full.mp4").exists():
            shutil.copy2("/tmp/yt_141_full.mp4", target_mp4)
            return target_mp4

        import yt_dlp

        tmp_out = self.cache_dir / f"yt_{yt_id}.part.mp4"
        tmp_out.unlink(missing_ok=True)
        url = f"https://www.youtube.com/watch?v={yt_id}"
        logger.info(
            f"[BongCucSplitter] Đang tải video gốc Full từ YouTube @bongcucaudio ({url})..."
        )
        ydl_opts = {
            "format": "18/best[ext=mp4]/best",
            "outtmpl": str(tmp_out),
            "quiet": True,
            "no_warnings": True,
            "retries": 3,
            "socket_timeout": 25,
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not tmp_out.exists() or tmp_out.stat().st_size < 1_000_000:
            raise RuntimeError(f"Không thể tải video YouTube {url} về {tmp_out}")

        shutil.move(str(tmp_out), str(target_mp4))
        actual_dur = get_media_duration(target_mp4)
        if actual_dur and actual_dur > 60:
            dur_cache = self._load_duration_cache()
            dur_cache[yt_id] = float(actual_dur)
            self._save_duration_cache(dur_cache)

        logger.info(
            f"[BongCucSplitter] Đã tải xong YouTube {yt_id} ({target_mp4.stat().st_size / 1e6:.1f} MB, thời lượng={actual_dur:.1f}s)."
        )
        return target_mp4

    @staticmethod
    def _clean_story_title_no_bongcuc(raw_title: str) -> str:
        """Làm sạch tiêu đề truyện, loại bỏ tuyệt đối mọi chữ 'Bông Cúc', 'Bông Cúc Số...', 'BC Số...' và từ nhạy cảm Quy tắc Cộng đồng."""
        from .community_guidelines_guard import TikTokCommunityGuidelinesGuard

        cleaned = str(raw_title or "").strip()
        cleaned = re.sub(
            r"(?i)\b(?:bông\s*cúc(?:\s*trắng)?(?:\s*số\s*\d+)?|bc\s*số\s*\d+|số\s*\d+)\b[:\s\-—|]*",
            "",
            cleaned,
        ).strip(" -—|:.")
        cleaned = TikTokCommunityGuidelinesGuard.sanitize_text(cleaned)
        return cleaned or "Chuyện Đêm Khuya Có Thật"

    @staticmethod
    def _slugify_vietnamese_hashtag(text: str) -> str:
        """Chuyển tên nội dung audio thành hashtag viết liền không dấu (ví dụ: 'Đại Tiểu Thư Thật' -> '#daitieuthuthat')."""
        import unicodedata

        s = str(text or "").strip().lower()
        s = s.replace("đ", "d").replace("Đ", "d")
        s = unicodedata.normalize("NFD", s)
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
        s = re.sub(r"[^a-z0-9]+", "", s)
        if not s:
            return "#truyenaudio"
        return f"#{s[:36]}"

    def _ensure_interjection_audio(self, seed_val: int, pitch_factor: float) -> Optional[Tuple[Path, float]]:
        """
        Tạo đoạn bình luận / dẫn chuyện ngắn gọn (3.2s - 5.2s) để chèn vào giữa nội dung audio gốc
        rồi quay trở lại mạch truyện chính ngay lập tức (phá vỡ 100% dấu vân tay thời gian AudioPrint).
        """
        import urllib.parse
        import urllib.request

        interj_dir = self.cache_dir / "interjections_v5"
        interj_dir.mkdir(parents=True, exist_ok=True)
        interjections = [
            "Nghe đến đoạn này mà thấy xót xa thật sự mọi người ạ. Cùng quay lại mạch truyện chính ngay nhé!",
            "Lòng người đúng là chẳng ai đoán trước được chữ ngờ. Mình nghe tiếp diễn biến chính ngay sau đây nhé!",
            "Tình tiết bắt đầu căng thẳng rồi đây, mọi người nghe tiếp xem nhân vật chính xử lý thế nào nhé!",
            "Một bước ngoặt không ai ngờ tới. Cùng quay lại câu chuyện ngay lúc này nhé!",
            "Đúng là chỉ khi biến cố ập tới mới thấu rõ lòng người. Quay lại mạch truyện chính ngay nhé!",
            "Nghe mà vừa thương vừa giận đúng không mọi người? Cùng nghe tiếp diễn biến ngay sau đây nhé!",
            "Càng nghe càng cuốn vào từng tình tiết. Mình quay lại mạch chính của câu chuyện ngay nhé!",
            "Sự thật dần được hé lộ rồi. Cùng nghe tiếp xem màn đáp trả phía sau ra sao nhé!",
            "Đặt mình vào hoàn cảnh này mới thấy nghẹt thở làm sao. Cùng quay lại câu chuyện ngay nhé!",
            "Tưởng đâu mọi chuyện đã êm xuôi, nào ngờ sóng gió mới chỉ bắt đầu. Nghe tiếp ngay nhé!",
            "Mỗi lời nói ra đều như cứa vào lòng. Mình quay lại mạch truyện chính để nghe tiếp nhé!",
            "Không biết quyết định tiếp theo sẽ thay đổi tất cả thế nào. Cùng nghe tiếp ngay bây giờ nhé!",
        ]
        idx = seed_val % len(interjections)
        voice_name = "vi-VN-HoaiMyNeural" if (seed_val % 2 == 0) else "vi-VN-NamMinhNeural"
        target_mp3 = interj_dir / f"interj_v5_{idx}_{voice_name}.mp3"

        if not target_mp3.exists() or target_mp3.stat().st_size < 4_000:
            edge_tts_bin = self.project_root / "venv" / "bin" / "edge-tts"
            cmd_tts = [
                str(edge_tts_bin) if edge_tts_bin.exists() else "edge-tts",
                "--voice",
                voice_name,
                "--rate=+18%",
                "--text",
                interjections[idx],
                "--write-media",
                str(target_mp3),
            ]
            try:
                subprocess.run(cmd_tts, capture_output=True, timeout=12, check=False)
            except Exception as exc:
                logger.debug(f"[BongCucSplitter] Lỗi tạo interjection Edge-TTS: {exc}")

        if not target_mp3.exists() or target_mp3.stat().st_size < 4_000:
            try:
                raw_gtts = interj_dir / f"interj_v5_{idx}_raw.mp3"
                url = (
                    "https://translate.google.com/translate_tts?ie=UTF-8&tl=vi&client=tw-ob&q="
                    + urllib.parse.quote(interjections[idx])
                )
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(req, timeout=12).read()
                if len(data) >= 4_000:
                    raw_gtts.write_bytes(data)
                    # Tăng tốc độ đọc tự nhiên (atempo=1.28) + chỉnh âm sắc ấm áp chuẩn radio đêm khuya
                    interj_pitch = 0.92 if (seed_val % 2 == 1) else 1.04
                    cmd_polish = [
                        self.ffmpeg_bin,
                        "-y",
                        "-i",
                        str(raw_gtts),
                        "-af",
                        (
                            f"aresample=44100,asetrate=44100*{interj_pitch:.3f},aresample=44100,"
                            f"atempo={1.28 / interj_pitch:.3f},"
                            "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-42dB"
                        ),
                        "-c:a",
                        "libmp3lame",
                        "-q:a",
                        "2",
                        str(target_mp3),
                    ]
                    subprocess.run(cmd_polish, capture_output=True, timeout=15, check=False)
                    if not target_mp3.exists() or target_mp3.stat().st_size < 4_000:
                        raw_gtts.replace(target_mp3)
                    else:
                        raw_gtts.unlink(missing_ok=True)
            except Exception as exc:
                logger.debug(f"[BongCucSplitter] Lỗi tạo interjection Google-TTS fallback: {exc}")

        if target_mp3.exists() and target_mp3.stat().st_size >= 4_000:
            dur = get_media_duration(target_mp3) or 3.8
            if 1.5 <= dur <= 7.5:
                return target_mp3, round(float(dur), 2)
        return None

    @staticmethod
    def _render_part_overlay_png(
        overlay_path: Path,
        ep_num: int,
        part_num: int,
        total_parts: int,
        title: str,
    ) -> Path:
        """
        Tạo lớp phủ PNG `1080x1920` chuẩn Cấu trúc mới `v6`:
        - CHỈ TRONG KHUNG KỂ CHUYỆN hiển thị chữ `"BC SỐ <ep_num>"` tương ứng, TUYỆT ĐỐI KHÔNG ghi rõ chữ `"Bông Cúc"`.
        - Phía trên: Thẻ bo góc bán trong suốt hiển thị `BC SỐ <ep_num>` + nhãn đêm khuya & TÊN NỘI DUNG AUDIO IN HOA nổi bật.
        - Phía dưới: Huy hiệu bo tròn hiển thị `BC SỐ <ep_num> - PHẦN <part_num> / <total_parts>`.
        """
        ov = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        draw = ImageDraw.Draw(ov)

        font_candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
        ]
        font_path = next((p for p in font_candidates if Path(p).exists()), None)
        if font_path:
            font_tag = ImageFont.truetype(font_path, 32)
            font_title = ImageFont.truetype(font_path, 44)
            font_part = ImageFont.truetype(font_path, 44)
        else:
            font_tag = ImageFont.load_default()
            font_title = ImageFont.load_default()
            font_part = ImageFont.load_default()

        clean_title = BongCucYouTubeSplitter._clean_story_title_no_bongcuc(title).upper()
        header_badges = [
            f"BC SỐ {ep_num} • ĐÊM KHUYA KỂ CHUYỆN",
            f"BC SỐ {ep_num} • GÓC TÂM SỰ ĐÊM KHUYA",
            f"BC SỐ {ep_num} • TRUYỆN AUDIO ĐÊM KHUYA",
            f"BC SỐ {ep_num} • CHUYỆN ĐỜI CÓ THẬT",
            f"BC SỐ {ep_num} • RADIO ĐÊM KHUYA",
            f"BC SỐ {ep_num} • GÓC KỂ CHUYỆN ĐÊM",
        ]
        t_seed = ep_num * 23 + part_num * 41
        badge_text = header_badges[t_seed % len(header_badges)]
        if part_num == total_parts:
            part_text = f"BC SỐ {ep_num} - PHẦN {part_num} / {total_parts} (KẾT)"
        else:
            part_text = f"BC SỐ {ep_num} - PHẦN {part_num} / {total_parts}"

        theme_palettes = [
            ((255, 215, 70, 255), (255, 255, 255, 255)),   # Vàng Hổ Phách & Trắng
            ((110, 240, 215, 255), (255, 248, 220, 255)),  # Xanh Ngọc Bích & Kem Ấm
            ((255, 185, 105, 255), (240, 248, 255, 255)),  # Cam Hoàng Hôn & Trắng Băng
            ((160, 220, 255, 255), (255, 235, 140, 255)),  # Xanh Ánh Trăng & Vàng Kim
            ((255, 170, 205, 255), (255, 255, 255, 255)),  # Hồng Ánh Đào & Trắng
        ]
        accent_color, text_color = theme_palettes[t_seed % len(theme_palettes)]
        y_jitter = ((t_seed % 9) - 4) * 3

        def _draw_centered(y_pos: int, text: str, font: Any, fill_rgba: Tuple[int, int, int, int], stroke_w: int = 4) -> int:
            bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            x_pos = max(45, (1080 - tw) // 2)
            draw.text(
                (x_pos, y_pos),
                text,
                font=font,
                fill=fill_rgba,
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0, 255),
            )
            return th

        # Tự động ngắt dòng Tên Nội Dung Audio ở khung trên (tối đa 2 dòng)
        words = clean_title.split()
        title_lines: List[str] = []
        cur_line = ""
        for w in words:
            test_l = f"{cur_line} {w}".strip()
            bbox = draw.textbbox((0, 0), test_l, font=font_title, stroke_width=4)
            if (bbox[2] - bbox[0]) <= 910 or not cur_line:
                cur_line = test_l
            else:
                title_lines.append(cur_line)
                cur_line = w
        if cur_line:
            title_lines.append(cur_line)
        title_lines = title_lines[:2]

        # Khung Thẻ Tiêu Đề Phía Trên (Dark Glass Card)
        card_top = 240 + y_jitter
        card_bottom = card_top + (225 if len(title_lines) > 1 else 175)
        draw.rounded_rectangle(
            [48, card_top, 1032, card_bottom],
            radius=28,
            fill=(12, 16, 26, 210),
            outline=accent_color,
            width=3,
        )
        _draw_centered(card_top + 22, badge_text, font_tag, accent_color, stroke_w=3)
        y_cursor = card_top + 74
        for tl in title_lines:
            h_used = _draw_centered(y_cursor, tl, font_title, text_color, stroke_w=4)
            y_cursor += h_used + 12

        # Khung Pill Hiển Thị BC Số + Phần Phía Dưới
        pill_top = 1410 - y_jitter
        pill_bottom = pill_top + 96
        draw.rounded_rectangle(
            [115, pill_top, 965, pill_bottom],
            radius=48,
            fill=(15, 20, 32, 222),
            outline=accent_color,
            width=3,
        )
        _draw_centered(pill_top + 24, part_text, font_part, accent_color, stroke_w=4)

        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        ov.save(str(overlay_path))
        return overlay_path

    @staticmethod
    def build_part_caption_and_hashtags(part_spec: Dict[str, Any]) -> Tuple[str, str]:
        """
        Tạo Caption và Hashtag chuẩn `v6` cho kênh `audio_dem_khuyen`:
        - TUYỆT ĐỐI KHÔNG chứa "Bông Cúc", "Bông Cúc Số", "BC Số", "#bongcuc...", "#bc..." ở ngoài caption/hashtag.
        - Chỉ hiển thị Tên Nội Dung Audio + Phần và hashtag tên nội dung audio + hashtag đêm khuya.
        """
        ep_num = int(part_spec["ep_num"])
        part_num = int(part_spec["part_num"])
        total_parts = int(part_spec["total_parts"])
        raw_title = str(part_spec["title"]).strip()
        title = BongCucYouTubeSplitter._clean_story_title_no_bongcuc(raw_title)
        is_final = bool(part_spec.get("is_final_part"))
        bg_mode = str(part_spec.get("bg_mode") or "night_mukbang")

        try:
            from .translator import SubtitleTranslator

            title = SubtitleTranslator._apply_commercial_safeguard(title)
        except Exception:
            pass

        part_label = (
            f"Phần {part_num}/{total_parts} - Kết"
            if is_final
            else f"Phần {part_num}/{total_parts}"
        )
        hooks = [
            f"🎧 {title} [{part_label}] — Diễn biến cực kỳ kịch tính, nghe đến cuối để xem màn đáp trả phía sau nhé!",
            f"🌙 {title} ({part_label}) — Chuyện đêm khuya cuốn không rời tai, bạn nghĩ sao về quyết định trong phần này?",
            f"📖 {title} [{part_label}] — Lòng người khó đoán, chỉ khi biến cố ập tới mới rõ chân tướng!",
            f"🔥 {title} ({part_label}) — Càng nghe càng thấm từng câu chữ, nút thắt bất ngờ nhất bắt đầu lộ diện!",
            f"🎙️ {title} [{part_label}] — Góc kể chuyện đêm khuya đầy cảm xúc, đừng bỏ lỡ chi tiết nào nhé!",
            f"✨ {title} ({part_label}) — Một bước ngoặt thay đổi tất cả, liệu cái kết có xứng đáng?",
            f"🕯️ {title} [{part_label}] — Những góc khuất dần được phơi bày qua từng lời kể đêm khuya!",
            f"🌌 {title} ({part_label}) — Ai đang theo dõi câu chuyện này thì vào nghe tiếp ngay nhé!",
        ]
        seed_idx = ep_num * 7 + part_num * 13
        caption = hooks[seed_idx % len(hooks)]
        try:
            from .translator import SubtitleTranslator

            caption = SubtitleTranslator._apply_commercial_safeguard(caption)
        except Exception:
            pass

        title_slug_tag = BongCucYouTubeSplitter._slugify_vietnamese_hashtag(title)
        part_tag = f"#phan{part_num}"
        is_daytime = 5 <= datetime.now().hour < 18
        if is_daytime:
            rotating_tags = [
                "#truyenaudio",
                "#kechuyen",
                "#truyenngan",
                "#nghetruyen",
                "#chuyengiadinh",
                "#tamlyhonnhan",
                "#gocnhincuocsong",
                "#mukbang" if bg_mode == "night_mukbang" else "#baihoccuocsong",
                "#xuhuong",
                "#fyp",
            ]
        else:
            rotating_tags = [
                "#audiodemkhuya",
                "#kechuyendemkhuya",
                "#truyenaudio",
                "#truyenngan",
                "#nghetruyendemkhuya",
                "#chuyengiadinh",
                "#tamlyhonnhan",
                "#gocnhinkhuya",
                "#mukbangdemkhuya" if bg_mode == "night_mukbang" else "#chuyendemsau",
                "#xuhuong",
                "#fyp",
            ]
        picked_tags = [title_slug_tag, part_tag]
        for offset in range(6):
            tag_cand = rotating_tags[(seed_idx + offset * 3) % len(rotating_tags)]
            if tag_cand not in picked_tags:
                picked_tags.append(tag_cand)
        hashtags = " ".join(picked_tags)
        try:
            from .community_guidelines_guard import TikTokCommunityGuidelinesGuard

            caption, hashtags = TikTokCommunityGuidelinesGuard.sanitize_caption_and_hashtags(
                caption, hashtags, "audio_dem_khuyen"
            )
        except Exception:
            pass
        return caption, hashtags

    def render_single_part(
        self,
        part_spec: Dict[str, Any],
        target_ready_dir: Path,
        seq_offset_seconds: int = 0,
        bg_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        [CẤU TRÚC VIDEO MỚI `bongcuc_v6` CHO KÊNH `audio_dem_khuyen`]:
        1. Chỉ trong khung kể chuyện trên video giữ chữ "BC SỐ <ep_num>", tuyệt đối không ghi rõ chữ "Bông Cúc" ở bất kỳ đâu.
        2. Thay mới toàn bộ kho Background HD (giữ Cinema Mukbang vào ban đêm `night_mukbang`).
        3. Đổi mạnh giọng đọc audio (biến đổi cao độ & âm sắc Formant +/-2 semitones + hiệu ứng phòng thu ấm).
        4. Tự động chèn 1 đoạn bình luận/dẫn chuyện ngắn (~3.5s) vào giữa nội dung gốc rồi quay lại mạch chính ngay.
        5. Khung hình `bongcuc_v6` với Midnight Cinema Background + Viền đôi Hổ Phách + Thanh tiến trình chạy động.
        """
        ep_num = int(part_spec["ep_num"])
        part_num = int(part_spec["part_num"])
        total_parts = int(part_spec["total_parts"])
        yt_id = str(part_spec["yt_id"])
        title = self._clean_story_title_no_bongcuc(str(part_spec["title"]))
        story_id = str(part_spec["story_id"])
        start_sec = float(part_spec["start_sec"])
        orig_dur = float(part_spec["orig_duration"])

        eff_bg_mode = (
            bg_mode
            or str(part_spec.get("bg_mode") or "").strip()
            or get_target_bg_mode_for_hour()
        )
        if eff_bg_mode not in ("night_mukbang", "day_aesthetic"):
            eff_bg_mode = get_target_bg_mode_for_hour()
        bg_short = "mukbang" if eff_bg_mode == "night_mukbang" else "daylight"

        try:
            from .translator import SubtitleTranslator

            title = SubtitleTranslator._apply_commercial_safeguard(title)
        except Exception:
            pass

        seed_val = ep_num * 31 + part_num * 17
        full_mp4 = self.ensure_youtube_full_video(yt_id)
        bg_mp4, bg_start_sec = self.ensure_background_video(
            bg_mode=eff_bg_mode, seed_val=seed_val
        )

        overlay_png = self.cache_dir / f"ov_v6_s{ep_num}_p{part_num}of{total_parts}.png"
        self._render_part_overlay_png(
            overlay_path=overlay_png,
            ep_num=ep_num,
            part_num=part_num,
            total_parts=total_parts,
            title=title,
        )

        target_ready_dir.mkdir(parents=True, exist_ok=True)
        ts_val = datetime.fromtimestamp(time.time() + seq_offset_seconds).strftime(
            "%Y%m%d_%H%M%S"
        )
        out_name = (
            f"{ts_val}_tiktok_story_{story_id}_bongcuc_v6_s{ep_num}_p{part_num}_bg_{bg_short}.mp4"
        )
        out_path = target_ready_dir / out_name
        tmp_render = self.cache_dir / f"rendering_v6_{story_id}_{bg_short}.mp4"
        tmp_render.unlink(missing_ok=True)

        # --- ĐỔI MẠNH GIỌNG ĐỌC AUDIO (Voice Timbre & Pitch Restyling) ---
        speed_factor = round(1.385 + (seed_val % 20) * 0.002, 3)  # 1.385x .. 1.423x
        bg_speed = round(1.14 + (seed_val % 11) * 0.01, 2)       # 1.14x .. 1.24x
        # Xoay vòng giữa Tone Trầm Ấm Đêm Khuya (0.888..0.902) và Tone Thanh Sáng Truyền Cảm (1.112..1.126)
        if seed_val % 2 == 0:
            pitch_factor = round(0.888 + (seed_val % 8) * 0.002, 4)
            formant_eq = (
                "equalizer=f=175:t=q:w=1.1:g=3.2,"
                "equalizer=f=420:t=q:w=1.2:g=2.0,"
                "equalizer=f=1850:t=q:w=1.4:g=-2.6,"
                "equalizer=f=3600:t=q:w=1.2:g=2.2,"
                "equalizer=f=6200:t=q:w=1.4:g=1.6"
            )
        else:
            pitch_factor = round(1.112 + (seed_val % 8) * 0.002, 4)
            formant_eq = (
                "equalizer=f=195:t=q:w=1.1:g=2.8,"
                "equalizer=f=920:t=q:w=1.3:g=1.8,"
                "equalizer=f=2450:t=q:w=1.5:g=-2.2,"
                "equalizer=f=4600:t=q:w=1.3:g=1.8,"
                "equalizer=f=6800:t=q:w=1.4:g=1.4"
            )
        atempo_comp = round(speed_factor / pitch_factor, 5)

        # --- CHÈN ĐOẠN DẪN NGẮN (3-4s) VÀO GIỮA NỘI DUNG GỐC RỒI QUAY LẠI MẠCH CHÍNH NGAY ---
        base_out_dur = max(35.0, round(orig_dur / speed_factor, 2))
        interj_info = self._ensure_interjection_audio(seed_val=seed_val, pitch_factor=pitch_factor)
        split_sec = round(min(base_out_dur * 0.35, 36.0 + (seed_val % 14)), 2)
        resume_sec = max(0.0, round(split_sec - 0.65, 2))
        if interj_info is not None and base_out_dur >= 45.0:
            interj_path, interj_dur = interj_info
            target_out_dur = round(base_out_dur + interj_dur + 0.65, 2)
        else:
            interj_path = None
            interj_dur = 0.0
            target_out_dur = base_out_dur

        crop_y_shift = round(0.005 + (seed_val % 5) * 0.003, 3)
        gop_val = 48 + (seed_val % 17)
        crf_val = 19 + (seed_val % 2)
        audio_br = "192k" if (seed_val % 2 == 0) else "160k"
        creation_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000000Z")

        # Cấu trúc hình ảnh mới v6: Midnight Cinema Background + Khung Mukbang viền Hổ Phách + Thanh tiến trình động
        visual_chain = (
            f"[1:v]setpts=PTS/{bg_speed},fps=25,trim=duration={target_out_dur:.2f},setpts=PTS-STARTPTS,split=2[bg][fg];"
            "[bg]scale=240:426:force_original_aspect_ratio=increase,crop=240:426,hflip,"
            "boxblur=12:2,colorbalance=rs=0.06:gs=-0.01:bs=-0.08,"
            "eq=brightness=-0.22:saturation=0.88:contrast=1.08,vignette=PI/5,"
            "scale=1080:1920:flags=fast_bilinear[bg_out];"
            f"[fg]crop=iw*0.95:ih*0.98:(iw-iw*0.95)/2:ih*{crop_y_shift},hflip,"
            "scale=w=1036:h=696:force_original_aspect_ratio=decrease:flags=bicubic,"
            "eq=contrast=1.05:brightness=0.015:saturation=1.09:gamma=1.02,"
            "pad=w=iw+12:h=ih+12:x=6:y=6:color=0xFFCA28@0.85,"
            f"drawbox=x=6:y=ih-12:w='(iw-12)*(t/{target_out_dur:.2f})':h=6:color=0xFF6F00@0.95:t=fill[fg_out];"
            "[bg_out][fg_out]overlay=(W-w)/2:(H-h)/2+32[v1];"
            "[v1][2:v]overlay=0:0[vout];"
        )

        main_audio_transform = (
            f"[0:a]aresample=44100,highpass=f=60,lowpass=f=13200,"
            f"asetrate=44100*{pitch_factor},aresample=44100,atempo={atempo_comp},"
            f"{formant_eq},"
            "aecho=0.8:0.88:16:0.10,extrastereo=m=1.14,"
            "acompressor=threshold=-18dB:ratio=2.4:attack=12:release=170:makeup=2.2dB,"
            "volume=1.18,aformat=sample_rates=44100:channel_layouts=stereo"
        )

        if interj_path is not None:
            fade_out_st = max(0.5, round(split_sec - 0.18, 2))
            interj_fade_out = max(0.2, round(interj_dur - 0.18, 2))
            audio_chain = (
                f"{main_audio_transform},asplit=2[ma1][ma2];"
                f"[ma1]atrim=0:{split_sec:.2f},asetpts=PTS-STARTPTS,afade=t=out:st={fade_out_st:.2f}:d=0.18[a_seg1];"
                f"[3:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
                f"equalizer=f=180:t=q:w=1.1:g=2.2,extrastereo=m=1.10,volume=1.22,"
                f"afade=t=in:st=0:d=0.12,afade=t=out:st={interj_fade_out:.2f}:d=0.18,asetpts=PTS-STARTPTS[a_interj];"
                f"[ma2]atrim=start={resume_sec:.2f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.18[a_seg2];"
                "[a_seg1][a_interj][a_seg2]concat=n=3:v=0:a=1,alimiter=limit=0.95[aout]"
            )
        else:
            audio_chain = f"{main_audio_transform},alimiter=limit=0.95[aout]"

        filter_complex = visual_chain + audio_chain

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-ss",
            f"{start_sec:.2f}",
            "-t",
            f"{orig_dur:.2f}",
            "-i",
            str(full_mp4),
            "-stream_loop",
            "-1",
            "-ss",
            f"{bg_start_sec:.1f}",
            "-i",
            str(bg_mp4),
            "-i",
            str(overlay_png),
        ]
        if interj_path is not None:
            cmd.extend(["-i", str(interj_path)])
        cmd.extend(
            [
                "-t",
                f"{target_out_dur:.2f}",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-filter_complex",
                filter_complex,
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                "-c:v",
                "libx264",
                "-preset",
                "superfast",
                "-crf",
                str(crf_val),
                "-g",
                str(gop_val),
                "-keyint_min",
                str(gop_val // 2),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                audio_br,
                "-ar",
                "44100",
                "-fflags",
                "+bitexact",
                "-flags:v",
                "+bitexact",
                "-flags:a",
                "+bitexact",
                "-metadata",
                "encoder=VN Video Editor 2.24.1 (iOS)",
                "-metadata",
                "comment=VN Video Editor 2.24.1 (iOS)",
                "-metadata",
                f"creation_time={creation_iso}",
                "-metadata:s:v:0",
                "handler_name=CoreMedia Video",
                "-metadata:s:a:0",
                "handler_name=CoreMedia Audio",
                "-movflags",
                "+faststart",
                str(tmp_render),
            ]
        )

        logger.info(
            f"[BongCucSplitter v6] Đang cắt & dựng cấu trúc mới [{eff_bg_mode} -> {bg_mp4.name} @{bg_start_sec:.0f}s] "
            f"(speed={speed_factor}x, pitch={pitch_factor}x, chèn dẫn chuyện @{split_sec:.0f}s={bool(interj_path)}, GOP={gop_val}): "
            f"{title} [Phần {part_num}/{total_parts} | views={part_spec.get('view_count', 0):,}]..."
        )
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)
        if res.returncode != 0 or not tmp_render.exists() or tmp_render.stat().st_size < 500_000:
            raise RuntimeError(
                f"Lỗi ffmpeg khi dựng v5 {title} Phần {part_num}: {res.stderr[-400:]}"
            )

        shutil.move(str(tmp_render), str(out_path))
        actual_out_dur = get_media_duration(out_path) or target_out_dur
        spec_with_bg = dict(part_spec)
        spec_with_bg["bg_mode"] = eff_bg_mode
        caption, hashtags = self.build_part_caption_and_hashtags(spec_with_bg)

        logger.info(
            f"[BongCucSplitter v5] Hoàn tất xuất bản (v5 {eff_bg_mode} + Đổi giọng + Chèn dẫn chuyện + Xóa Bông Cúc): {out_path.name} "
            f"(thời lượng={actual_out_dur:.1f}s, size={out_path.stat().st_size / 1e6:.1f}MB)"
        )
        return {
            "channel": "audio_dem_khuyen",
            "platform": "tiktok",
            "video_id": story_id,
            "sound_id": f"bongcuc_yt_{yt_id}_p{part_num}",
            "source_url": part_spec["source_url"],
            "detected_language": "vi",
            "segment_count": 10,
            "raw_video": str(full_mp4),
            "extracted_audio": str(full_mp4),
            "final_video": str(out_path),
            "filename": f"audio_dem_khuyen/ready/{out_name}",
            "caption": caption,
            "hashtags": hashtags,
            "status": "READY",
            "posted_at": "",
            "speech_ratio": 0.95,
            "spoken_seconds": round(actual_out_dur, 1),
            "ep_num": ep_num,
            "part_num": part_num,
            "total_parts": total_parts,
            "bg_mode": eff_bg_mode,
            "view_count": int(part_spec.get("view_count") or 0),
        }
