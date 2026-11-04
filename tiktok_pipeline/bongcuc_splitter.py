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

# Kho video nền (Background Visual Pool) thay thế hình nền tô màu cũ theo khung giờ Ngày / Đêm:
# - Ban đêm (18:00 - 04:59, khung 21:15): Ghép nền Mukbang đồ ăn đêm hấp dẫn (tắt 100% tiếng gốc của clip Mukbang).
# - Ban ngày (05:00 - 17:59, khung 08:48 & 13:25): Ghép nền Nghệ thuật làm bánh kem / Gốm sứ / Cắt cát 3D tươi sáng.
BONGCUC_NIGHT_MUKBANG_POOL: List[Dict[str, Any]] = [
    {
        "yt_id": "07Q8NHoZjdc",
        "title": "Eat with Boki Crunchy Seasoned Spicy Chicken and Jajang Ramyeon Mukbang 1080p",
        "duration": 524.0,
    },
    {
        "yt_id": "EuySE6259wQ",
        "title": "SULGI Spicy Chicken Tteokbokki Cheese Kimchi Gimbap Mukbang 1080p",
        "duration": 1027.0,
    },
    {
        "yt_id": "s85cKxJIpo0",
        "title": "GONGSAM TABLE Fire Noodle Hot Dog Gimbap Tteokbokki Mukbang 1080p",
        "duration": 1017.0,
    },
    {
        "yt_id": "zFkeOaM9IXo",
        "title": "Eat with Boki Pelicana Hot Devil Seasoned Fried Chicken Cheese Balls Mukbang 1080p",
        "duration": 609.0,
    },
    {
        "yt_id": "ghUgHf8VIOM",
        "title": "SULGI Ramen Tteokbokki Omurice Seasoned Fried Chicken Mukbang 1080p",
        "duration": 866.0,
    },
    {
        "yt_id": "Ky4Cbdx7jdc",
        "title": "Eat with Boki BBQ Jamaican Chicken Thighs and Jumbo Cheese Sticks Mukbang 1080p",
        "duration": 549.0,
    },
]

BONGCUC_DAY_AESTHETIC_POOL: List[Dict[str, Any]] = [
    {
        "yt_id": "1jFbyGvyAAE",
        "title": "The Icing Artist 10 Amazing Cakes Decorating Compilation 1080p",
        "duration": 651.0,
    },
    {
        "yt_id": "1Gic5nYiAjM",
        "title": "Florian Gadsby Artisan Pottery Wheel Bowl Turning ASMR 1080p",
        "duration": 817.0,
    },
    {
        "yt_id": "rF6awqlStl4",
        "title": "Satisfying & Relaxing 3D Kinetic Art Compilation 1080p60",
        "duration": 543.0,
    },
    {
        "yt_id": "7cJGE6cUoao",
        "title": "Relaxing Pottery Art Ceramic Masterpieces 1080p",
        "duration": 646.0,
    },
    {
        "yt_id": "AQmRNOLthSs",
        "title": "So Yummy 10 Amazing Dessert & Cake Decorating Recipes 1080p",
        "duration": 605.0,
    },
    {
        "yt_id": "HHtuYQxJMUQ",
        "title": "Colorful Cake Decorating Art Compilation 1080p",
        "duration": 634.0,
    },
]


def get_target_bg_mode_for_hour(hour: Optional[int] = None) -> str:
    """
    Xác định chế độ hình nền (`night_mukbang` hoặc `day_aesthetic`) theo khung giờ trong ngày:
    - Ban đêm (`18:00` -> `04:59`, bao gồm khung đăng tối `21:15`): `night_mukbang` (Mukbang đồ ăn đêm).
    - Ban ngày (`05:00` -> `17:59`, bao gồm khung sáng `08:48` & trưa `13:25`): `day_aesthetic` (Bánh kem / Gốm / 3D Satisfying).
    """
    if hour is None:
        hour = datetime.now().hour
    if hour >= 18 or hour < 5:
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
        "ep_num": 145,
        "yt_id": "e9oSX2s0ARs",
        "title": "Con Ma Nhà Tôi Vừa Giỏi Vừa Biết Tăng Ca",
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
        (`data/bongcuc_full_catalog.json`), ưu tiên 12 tập đã đăng Phần 1 lên đầu rồi đến toàn bộ các tập
        từ mới nhất về Số 01.
        """
        if self.catalog_path.exists():
            try:
                data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) >= len(BONGCUC_EPISODES_CATALOG):
                    return data
            except Exception:
                pass
        return list(BONGCUC_EPISODES_CATALOG)

    def sync_new_episodes_from_youtube(self) -> int:
        """
        Tự động quét kênh YouTube `https://www.youtube.com/@bongcucaudio/videos` để cập nhật thêm
        các tập mới đăng (ví dụ: Số 149, 150...) vào `data/bongcuc_full_catalog.json`.
        Trả về số lượng tập mới phát hiện được.
        """
        try:
            import yt_dlp

            current_cat = self.get_full_catalog()
            known_yt_ids = {str(item.get("yt_id")) for item in current_cat}
            part1_posted_eps = {141, 142, 143, 144, 146, 137, 119, 145, 138, 110, 70, 10}

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
                if ep_num <= 0:
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

        if part1_posted:
            start_offset = BONGCUC_PART1_END_YOUTUBE_SECONDS
            rem_dur = max(120.0, total_dur - start_offset)
            num_rem_parts = max(1, round(rem_dur / target_orig_chunk))
            slice_orig_dur = rem_dur / num_rem_parts
            total_parts = 1 + num_rem_parts
            start_part_idx = 2
        else:
            start_offset = 0.0
            rem_dur = max(120.0, total_dur)
            num_rem_parts = max(1, round(rem_dur / target_orig_chunk))
            slice_orig_dur = rem_dur / num_rem_parts
            total_parts = num_rem_parts
            start_part_idx = 1

        parts_plan: List[Dict[str, Any]] = []
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

    def build_interleaved_queue(
        self, known_posted_or_ready_ids: Set[str]
    ) -> List[Dict[str, Any]]:
        """
        Xây dựng hàng đợi độc quyền 100% từ kênh YouTube `@bongcucaudio` (148+ tập truyện)
        theo chiến lược **ĐAN XEN 1:1 GIỮA [TẬP MỚI VIEW CAO (PHẦN 1)] <-> [PHẦN TIẾP THEO (PHẦN 2+)]**:
        - Nhánh A (`fresh_part1_specs`): Các tập mới chưa từng đăng `Phần 1` (`part1_posted == False`),
          được sắp xếp theo **`view_count` thực tế trên YouTube giảm dần** (Số 20: 161,231 views,
          Số 39: 112,208 views, Số 23: 103,146 views, Số 45: 102,834 views, Số 60: 102,409 views...)
          để kéo tệp người xem mới từ FYP TikTok.
        - Nhánh B (`continuation_specs`): Các `Phần 2, 3...` của những tập đã chiếu `Phần 1`
          (sắp xếp ưu tiên view cao + xoay vòng theo đợt `Wave` để không trùng tập liên tiếp).
        - Đan xen 1:1: `[Tập Mới View Cao - Phần 1]` -> `[Tập Đang Chiếu - Phần 2+]` -> `[Tập Mới View Cao - Phần 1]`...
        """
        catalog = self.get_full_catalog()

        # Phân loại các tập đã chiếu Phần 1 (trong lịch sử hoặc trong known_posted_or_ready_ids) và các tập mới chưa chiếu Phần 1
        active_cont_eps: List[Dict[str, Any]] = []
        brand_new_eps: List[Dict[str, Any]] = []
        for ep in catalog:
            ep_num = int(ep.get("ep_num") or 0)
            p1_sid = make_bongcuc_story_id(ep_num, 1)
            if bool(ep.get("part1_posted")) or p1_sid in known_posted_or_ready_ids:
                active_cont_eps.append(ep)
            else:
                brand_new_eps.append(ep)

        # Sắp xếp tập mới ưu tiên các tập có độ dài chuẩn TikTok (4 - 13 phần, <= 5400s) + lượt view YouTube cao nhất (>100k view: Số 39, 45, 60, 35, 21, 120...)
        brand_new_eps.sort(
            key=lambda x: (
                1 if 600 <= float(x.get("duration") or 3200) <= 5400 else 0,
                int(x.get("view_count") or 0),
                int(x.get("ep_num") or 0),
            ),
            reverse=True,
        )
        # Sắp xếp tập đang chiếu dở theo độ dài chuẩn + lượt view giảm dần (Số 10: 472k view, Số 119: 245k view, Số 141: 40k view...)
        active_cont_eps.sort(
            key=lambda x: (
                1 if 600 <= float(x.get("duration") or 3200) <= 5400 else 0,
                int(x.get("view_count") or 0),
                int(x.get("ep_num") or 0),
            ),
            reverse=True,
        )

        fresh_part1_specs: List[Dict[str, Any]] = []
        new_eps_followup_lists: List[List[Dict[str, Any]]] = []
        cont_per_ep_lists: List[List[Dict[str, Any]]] = []

        remaining_eps_count = 0
        fetched_online_count = 0

        for ep_info in active_cont_eps:
            allow_net = fetched_online_count < 8 and not ep_info.get("duration")
            plan = self.compute_episode_parts_plan(ep_info, allow_network=allow_net)
            if allow_net:
                fetched_online_count += 1
            pending = [p for p in plan if str(p["story_id"]) not in known_posted_or_ready_ids]
            if pending:
                cont_per_ep_lists.append(pending)
                remaining_eps_count += 1

        for ep_info in brand_new_eps:
            allow_net = fetched_online_count < 14 and not ep_info.get("duration")
            plan = self.compute_episode_parts_plan(ep_info, allow_network=allow_net)
            if allow_net:
                fetched_online_count += 1
            pending = [p for p in plan if str(p["story_id"]) not in known_posted_or_ready_ids]
            if not pending:
                continue
            remaining_eps_count += 1
            if int(pending[0]["part_num"]) == 1:
                fresh_part1_specs.append(pending[0])
                if len(pending) > 1:
                    new_eps_followup_lists.append(pending[1:])
            else:
                cont_per_ep_lists.append(pending)

        # Trải phẳng danh sách Phần 2+ theo từng đợt (Wave) để không bao giờ có 2 phần cùng tập đứng gần nhau
        continuation_specs: List[Dict[str, Any]] = []
        max_cont_wave = max((len(lst) for lst in cont_per_ep_lists), default=0)
        for w_idx in range(max_cont_wave):
            for ep_list in cont_per_ep_lists:
                if w_idx < len(ep_list):
                    continuation_specs.append(ep_list[w_idx])

        # Nối thêm các phần 2+ của những tập mới (theo từng cụm 10 tập) vào sau
        batch_size = 10
        for b_start in range(0, len(new_eps_followup_lists), batch_size):
            sub_lists = new_eps_followup_lists[b_start : b_start + batch_size]
            max_w = max((len(lst) for lst in sub_lists), default=0)
            for w_idx in range(max_w):
                for ep_list in sub_lists:
                    if w_idx < len(ep_list):
                        continuation_specs.append(ep_list[w_idx])

        # Đan xen 1:1 giữa [Tập Mới View Cao - Phần 1] và [Phần Tiếp Theo - Phần 2+]
        # Đồng thời gán xoay vòng bg_mode ("day_aesthetic" / "night_mukbang") cho từng nhóm
        interleaved: List[Dict[str, Any]] = []
        max_len = max(len(fresh_part1_specs), len(continuation_specs))
        for idx in range(max_len):
            bg_choice = "day_aesthetic" if (idx % 2 == 0) else "night_mukbang"
            if idx < len(fresh_part1_specs):
                p1_item = dict(fresh_part1_specs[idx])
                p1_item["bg_mode"] = bg_choice
                interleaved.append(p1_item)
            if idx < len(continuation_specs):
                c_item = dict(continuation_specs[idx])
                c_item["bg_mode"] = bg_choice
                interleaved.append(c_item)

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

        import yt_dlp

        ytdlp_macos_bin = self._ensure_ytdlp_macos_bin()
        ordered_indices = [(seed_val + i) % len(pool) for i in range(len(pool))]
        chosen_mp4: Optional[Path] = None
        for idx in ordered_indices:
            cand_spec = pool[idx]
            yt_id = str(cand_spec["yt_id"])
            target_mp4 = bg_dir / f"bg_{yt_id}.mp4"
            if target_mp4.exists() and target_mp4.stat().st_size > 2_000_000:
                try:
                    w_chk, h_chk = get_video_resolution(target_mp4)
                except Exception:
                    w_chk, h_chk = 0, 0
                if h_chk >= 720 and w_chk >= 1080:
                    chosen_mp4 = target_mp4
                    break
                logger.info(
                    f"[BongCucSplitter] Xóa bản Background độ phân giải thấp cũ ({w_chk}x{h_chk}) của {yt_id} để tải bản Full HD 1080p..."
                )
                target_mp4.unlink(missing_ok=True)
            tmp_out = bg_dir / f"bg_{yt_id}.part.mp4"
            tmp_out.unlink(missing_ok=True)
            url = f"https://www.youtube.com/watch?v={yt_id}"
            try:
                logger.info(
                    f"[BongCucSplitter] Đang tải video Background Full HD 1080p [{eff_mode}] từ YouTube ({url} - {cand_spec.get('title')})..."
                )
                if ytdlp_macos_bin is not None:
                    subprocess.run(
                        [
                            str(ytdlp_macos_bin),
                            "-f",
                            "137/299/bestvideo[height>=1080][ext=mp4]/bestvideo[height>=720][ext=mp4]/18/best[ext=mp4]",
                            "-o",
                            str(tmp_out),
                            url,
                        ],
                        capture_output=True,
                        timeout=180,
                        check=False,
                    )
                if not tmp_out.exists() or tmp_out.stat().st_size < 2_000_000:
                    ydl_opts = {
                        "format": "137/299/bestvideo[height>=1080][ext=mp4]/bestvideo[height>=720][ext=mp4]/18/best[ext=mp4]",
                        "outtmpl": str(tmp_out),
                        "quiet": True,
                        "no_warnings": True,
                        "Retries": 2,
                        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url])
                if tmp_out.exists() and tmp_out.stat().st_size > 2_000_000:
                    shutil.move(str(tmp_out), str(target_mp4))
                    chosen_mp4 = target_mp4
                    w_dl, h_dl = get_video_resolution(target_mp4)
                    logger.info(
                        f"[BongCucSplitter] Đã tải xong Background Full HD [{eff_mode}] {yt_id} ({w_dl}x{h_dl}, {target_mp4.stat().st_size / 1e6:.1f} MB)."
                    )
                    break
            except Exception as dl_exc:
                logger.warning(
                    f"[BongCucSplitter] Thử video Background tiếp theo trong pool [{eff_mode}] do lỗi tải {yt_id}: {dl_exc}"
                )
                tmp_out.unlink(missing_ok=True)

        if chosen_mp4 is None:
            existing = sorted(
                p for p in bg_dir.glob("bg_*.mp4") if p.is_file() and p.stat().st_size > 2_000_000
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
            "Retries": 3,
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
    def _render_part_overlay_png(
        overlay_path: Path,
        ep_num: int,
        part_num: int,
        total_parts: int,
        title: str,
    ) -> Path:
        """
        Tạo lớp phủ PNG trong suốt `1080x1920` hiển thị:
        - Phía trên: `"BÔNG CÚC SỐ <ep_num>"` + `"PHẦN <part_num> / <total_parts>"` (hoặc `"PHẦN CUỐI"`).
        - Phía dưới: Tên tập truyện (tự động xuống dòng nếu dài).
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
            font_header = ImageFont.truetype(font_path, 56)
            font_part = ImageFont.truetype(font_path, 50)
            font_sub = ImageFont.truetype(font_path, 38)
        else:
            font_header = ImageFont.load_default()
            font_part = ImageFont.load_default()
            font_sub = ImageFont.load_default()

        line1 = f"BÔNG CÚC SỐ {ep_num}"
        if part_num == total_parts:
            line2 = f"PHẦN {part_num} / {total_parts} (CUỐI)"
        else:
            line2 = f"PHẦN {part_num} / {total_parts}"

        def _draw_centered(y_pos: int, text: str, font: Any, fill_rgba: Tuple[int, int, int, int]) -> int:
            bbox = draw.textbbox((0, 0), text, font=font, stroke_width=5)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            x_pos = max(40, (1080 - tw) // 2)
            draw.text(
                (x_pos, y_pos),
                text,
                font=font,
                fill=fill_rgba,
                stroke_width=5,
                stroke_fill=(0, 0, 0, 255),
            )
            return th

        # Đa dạng hóa bảng màu tiêu đề & dịch chuyển tọa độ Y nhẹ (±12px) theo từng phần
        # để chống thuật toán TikTok quét "Template tĩnh lặp lại hàng loạt (Anti-Template Spam)"
        theme_palettes = [
            ((255, 230, 65, 255), (255, 255, 255, 255)),   # Vàng hoàng kim & Trắng
            ((255, 195, 85, 255), (240, 250, 255, 255)),   # Cam hổ phách & Trắng băng
            ((125, 245, 215, 255), (255, 245, 210, 255)),  # Xanh ngọc bích & Kem ấm
            ((255, 175, 210, 255), (255, 255, 255, 255)),  # Hồng ánh đào & Trắng
            ((255, 245, 130, 255), (225, 245, 255, 255)),  # Vàng ánh trăng & Xanh nhạt
            ((165, 225, 255, 255), (255, 235, 145, 255)),  # Xanh thiên thanh & Vàng kem
        ]
        t_seed = ep_num * 19 + part_num * 37
        header_color, part_color = theme_palettes[t_seed % len(theme_palettes)]
        y_jitter = ((t_seed % 9) - 4) * 3  # -12px .. +12px

        _draw_centered(365 + y_jitter, line1, font_header, header_color)
        _draw_centered(445 + y_jitter, line2, font_part, part_color)

        # Tự động ngắt dòng tiêu đề truyện ở phía dưới
        words = title.strip().split()
        sub_lines: List[str] = []
        cur_line = ""
        for w in words:
            test_l = f"{cur_line} {w}".strip()
            bbox = draw.textbbox((0, 0), test_l, font=font_sub, stroke_width=4)
            if (bbox[2] - bbox[0]) <= 940 or not cur_line:
                cur_line = test_l
            else:
                sub_lines.append(cur_line)
                cur_line = w
        if cur_line:
            sub_lines.append(cur_line)

        y_sub = 1365 - y_jitter
        for sl in sub_lines[:2]:
            h_used = _draw_centered(y_sub, sl, font_sub, (255, 255, 255, 255))
            y_sub += h_used + 14

        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        ov.save(str(overlay_path))
        return overlay_path

    @staticmethod
    def build_part_caption_and_hashtags(part_spec: Dict[str, Any]) -> Tuple[str, str]:
        """Tạo Caption và Hashtag đa dạng (12 mẫu câu + xoay vòng tổ hợp Hashtag) cho từng phần của Bông Cúc Trắng trên kênh `audio_dem_khuyen`."""
        ep_num = int(part_spec["ep_num"])
        part_num = int(part_spec["part_num"])
        total_parts = int(part_spec["total_parts"])
        title = str(part_spec["title"]).strip()
        is_final = bool(part_spec.get("is_final_part"))
        bg_mode = str(part_spec.get("bg_mode") or "day_aesthetic")

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
        vibe_phrase = (
            "Vừa xem Mukbang đêm khuya vừa nghe"
            if bg_mode == "night_mukbang"
            else "Vừa xem clip thư giãn vừa nghe"
        )
        hooks = [
            f"📖 Bông Cúc Số {ep_num} [{part_label}]: {title}. Diễn biến cực kỳ kịch tính, nghe hết để xem màn đáp trả phía sau nhé!",
            f"🎧 [Truyện Đêm Khuya] Bông Cúc Số {ep_num} ({part_label}) — {title}. Từng tình tiết đều cuốn không rời tai, bạn nghĩ sao về phần này?",
            f"🔥 Bông Cúc Số {ep_num} [{part_label}]: {title}. Lòng người khó đoán, chỉ khi biến cố ập tới mới rõ chân tướng! Cùng nghe nhé!",
            f"🌙 Thức đêm nghe truyện: Bông Cúc Số {ep_num} ({part_label}) - {title}. Đừng bỏ lỡ những tình tiết gay cấn nhất trong phần này nhé!",
            f"✨ Bông Cúc Trắng Số {ep_num} [{part_label}]: {title}. Càng nghe càng thấm, câu chuyện bước sang nút thắt bất ngờ nhất!",
            f"🎙️ [Góc Kể Truyện] Bông Cúc Số {ep_num} ({part_label}): {title}. {vibe_phrase} câu chuyện cuốn hút này nhé!",
            f"💫 Bông Cúc Số {ep_num} — {part_label}: {title}. Một quyết định thay đổi tất cả, liệu cái kết có xứng đáng?",
            f"🍂 Nghe truyện Bông Cúc Số {ep_num} [{part_label}]: {title}. Ai đang hóng câu chuyện này thì vào nghe ngay nhé!",
            f"📌 Bông Cúc Số {ep_num} ({part_label}) | {title}. Sự thật dần hé lộ, nghe đến phút cuối để không bỏ sót chi tiết nào!",
            f"🌌 [Audio Truyện Ngắn] Bông Cúc Số {ep_num} [{part_label}]: {title}. Nghe xong phần này nhớ để lại cảm nhận của bạn nhé!",
            f"🕯️ Bông Cúc Trắng Số {ep_num} ({part_label}) — {title}. Những góc khuất dần được phơi bày qua từng lời kể!",
            f"🎶 {vibe_phrase} Bông Cúc Số {ep_num} [{part_label}]: {title}. Tập này cuốn từ đầu đến cuối!",
        ]
        seed_idx = ep_num * 7 + part_num * 13
        caption = hooks[seed_idx % len(hooks)]
        try:
            from .translator import SubtitleTranslator

            caption = SubtitleTranslator._apply_commercial_safeguard(caption)
        except Exception:
            pass

        rotating_tags = [
            "#audiodemkhuya",
            "#truyenaudio",
            "#kechuyendemkhuya",
            "#truyenngan",
            "#dramatiktok",
            "#xuhuong",
            "#truyentamly",
            "#nghetruyendemkhuya",
            "#bongcuctrang",
            "#mukbang" if bg_mode == "night_mukbang" else "#satisfying",
            "#gocnhinkhuya",
            "#chuyengiadinh",
            "#truyenhaymoingay",
            "#fyp",
        ]
        picked_tags = [f"#bongcucso{ep_num}", "#bongcucaudio"]
        for offset in range(6):
            tag_cand = rotating_tags[(seed_idx + offset * 3) % len(rotating_tags)]
            if tag_cand not in picked_tags:
                picked_tags.append(tag_cand)
        hashtags = " ".join(picked_tags)
        return caption, hashtags

    def render_single_part(
        self,
        part_spec: Dict[str, Any],
        target_ready_dir: Path,
        seq_offset_seconds: int = 0,
        bg_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Cắt âm thanh từ `@bongcucaudio` (tua `~1.42x`), THAY THẾ HOÀN TOÀN BACKGROUND HÌNH ẢNH PHÍA SAU AUDIO:
        - Khung giờ BAN ĐÊM (`night_mukbang`): Ghép nền video Mukbang đồ ăn đêm hấp dẫn (tắt 100% tiếng của video Mukbang).
        - Khung giờ BAN NGÀY (`day_aesthetic`): Ghép nền video Nghệ thuật làm bánh kem / Gốm sứ / 3D Satisfying tươi sáng.
        Áp dụng đầy đủ 6 Lớp Bộ Lọc Chống Phát Hiện TikTok và xuất trực tiếp vào `output/audio_dem_khuyen/ready/`.
        """
        ep_num = int(part_spec["ep_num"])
        part_num = int(part_spec["part_num"])
        total_parts = int(part_spec["total_parts"])
        yt_id = str(part_spec["yt_id"])
        title = str(part_spec["title"])
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

        overlay_png = self.cache_dir / f"ov_v4_s{ep_num}_p{part_num}of{total_parts}.png"
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
            f"{ts_val}_tiktok_story_{story_id}_bongcuc_v4_s{ep_num}_p{part_num}_bg_{bg_short}.mp4"
        )
        out_path = target_ready_dir / out_name
        tmp_render = self.cache_dir / f"rendering_{story_id}_{bg_short}.mp4"
        tmp_render.unlink(missing_ok=True)

        # --- BỘ LỌC 1 & 2: Biến thiên vi mô (Deterministic Per-Part Jitter) chống trùng lặp cấu trúc ---
        speed_factor = round(1.412 + (seed_val % 25) * 0.001, 3)  # 1.412x .. 1.436x (quanh 1.42x)
        bg_speed = round(1.12 + (seed_val % 11) * 0.01, 2)       # 1.12x .. 1.22x cho video nền sinh động
        target_out_dur = max(30.0, round(orig_dur / speed_factor, 2))
        crop_y_shift = round(0.005 + (seed_val % 5) * 0.003, 3)   # Giữ trọn vẹn 98% chiều cao từ đỉnh đầu (không cắt mất người)

        # Dịch cao độ vi mô (+1.6% .. +2.1%) kết hợp bù tốc độ chính xác để phá vỡ 100% AudioPrint YouTube/TikTok
        pitch_factor = round(1.016 + (seed_val % 6) * 0.001, 4)
        atempo_comp = round(speed_factor / pitch_factor, 5)

        # --- BỘ LỌC 3: Đa dạng hóa GOP Keyframe, CRF 19-20 (Full HD sắc nét) & Bitrate âm thanh + Chữ ký VN Video Editor (iOS) ---
        gop_val = 48 + (seed_val % 17)  # 48 .. 64
        crf_val = 19 + (seed_val % 2)   # 19 hoặc 20 (Full HD sắc nét, không vỡ hạt)
        audio_br = "192k" if (seed_val % 2 == 0) else "160k"
        creation_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000000Z")

        # Input [0]: Video @bongcucaudio (CHỈ LẤY AUDIO [0:a], bỏ hoàn toàn hình tô màu cũ [0:v])
        # Input [1]: Video Background Full HD 1080p Mukbang / Daytime Aesthetic (CHỈ LẤY HÌNH [1:v], tắt 100% tiếng [1:a])
        # Input [2]: Lớp phủ tiêu đề PNG trong suốt 1080x1920 [2:v]
        # Giữ trọn khung hình gốc 16:9 (95% rộng × 98% cao) để hiển thị đầy đủ cả người, khuôn mặt và bàn ăn/tác phẩm!
        filter_complex = (
            f"[1:v]setpts=PTS/{bg_speed},fps=25,trim=duration={target_out_dur:.2f},setpts=PTS-STARTPTS,split=2[bg][fg];"
            "[bg]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,hflip,"
            "boxblur=16:3,eq=brightness=-0.18:saturation=0.92:contrast=1.04,scale=1080:1920:flags=bilinear[bg_out];"
            f"[fg]crop=iw*0.95:ih*0.98:(iw-iw*0.95)/2:ih*{crop_y_shift},hflip,"
            "eq=contrast=1.04:brightness=0.01:saturation=1.07:gamma=1.02,"
            "unsharp=5:5:0.65:3:3:0.0,scale=w=1056:h=720:force_original_aspect_ratio=decrease:flags=lanczos,"
            "pad=w=iw+8:h=ih+8:x=4:y=4:color=white@0.32[fg_out];"
            "[bg_out][fg_out]overlay=(W-w)/2:(H-h)/2[v1];"
            "[v1][2:v]overlay=0:0[vout];"
            f"[0:a]highpass=f=55,lowpass=f=13500,"
            f"asetrate=44100*{pitch_factor},aresample=44100,atempo={atempo_comp},"
            "equalizer=f=140:t=q:w=1.2:g=1.5,equalizer=f=3200:t=q:w=1.5:g=-1.0,equalizer=f=5800:t=q:w=1.4:g=1.3,"
            "extrastereo=m=1.08,"
            "acompressor=threshold=-18dB:ratio=2.2:attack=15:release=180:makeup=2dB,"
            "volume=1.16,alimiter=limit=0.95[aout]"
        )

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
            "veryfast",
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

        logger.info(
            f"[BongCucSplitter] Đang cắt & ghép Background [{eff_bg_mode} -> {bg_mp4.name} @{bg_start_sec:.0f}s] "
            f"({speed_factor}x, pitch={pitch_factor}x, GOP={gop_val}): "
            f"Bông Cúc Số {ep_num} [Phần {part_num}/{total_parts} | views={part_spec.get('view_count', 0):,}]..."
        )
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=360)
        if res.returncode != 0 or not tmp_render.exists() or tmp_render.stat().st_size < 500_000:
            raise RuntimeError(
                f"Lỗi ffmpeg khi cắt Bông Cúc Số {ep_num} Phần {part_num}: {res.stderr[-400:]}"
            )

        shutil.move(str(tmp_render), str(out_path))
        actual_out_dur = get_media_duration(out_path) or part_spec["tiktok_duration"]
        spec_with_bg = dict(part_spec)
        spec_with_bg["bg_mode"] = eff_bg_mode
        caption, hashtags = self.build_part_caption_and_hashtags(spec_with_bg)

        logger.info(
            f"[BongCucSplitter] Hoàn tất xuất bản (v3 Background {eff_bg_mode} + 6 Bộ Lọc): {out_path.name} "
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
