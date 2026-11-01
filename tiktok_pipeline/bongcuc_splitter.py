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

from .utils import ensure_ffmpeg, get_media_duration, logger

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
        theo thứ tự **SO LE / XEN KẼ THEO CỤM (Wave-Batched Round-Robin)**:
        - Cụm 1 (Ưu tiên số 1): Toàn bộ 12 tập đã chiếu Phần 1 trên kênh (`141, 142, 143, 144, 146, 137, 119, 145, 138, 110, 70, 10`)
          -> Xoay vòng so le: `Phần 2` của cả 12 tập -> `Phần 3` của cả 12 tập -> ... -> `Phần Cuối`.
        - Cụm 2, 3, ... (Toàn bộ 136+ tập còn lại từ Số 148 lùi về Số 01):
          -> Chia thành từng cụm 10 tập, trong mỗi cụm xoay vòng so le `Phần 1` -> `Phần 2` -> ... -> `Phần Cuối`.
        Đảm bảo:
        1. Chỉ lấy duy nhất nguồn từ YouTube `@bongcucaudio`.
        2. Không bao giờ có 2 phần của cùng 1 tập đứng cạnh nhau.
        3. Tự động kiểm tra và báo trước cho người dùng khi số phần còn lại sắp hết.
        """
        catalog = self.get_full_catalog()
        part1_group = [ep for ep in catalog if bool(ep.get("part1_posted"))]
        full_eps_group = [ep for ep in catalog if not bool(ep.get("part1_posted"))]

        batches: List[List[Dict[str, Any]]] = []
        if part1_group:
            batches.append(part1_group)
        batch_size = 10
        for i in range(0, len(full_eps_group), batch_size):
            batches.append(full_eps_group[i : i + batch_size])

        interleaved: List[Dict[str, Any]] = []
        remaining_eps_count = 0
        fetched_online_count = 0

        for batch in batches:
            per_episode_pending: List[List[Dict[str, Any]]] = []
            for ep_info in batch:
                allow_net = fetched_online_count < 14
                plan = self.compute_episode_parts_plan(ep_info, allow_network=allow_net)
                pending_for_ep = [
                    p for p in plan if str(p["story_id"]) not in known_posted_or_ready_ids
                ]
                if pending_for_ep:
                    per_episode_pending.append(pending_for_ep)
                    remaining_eps_count += 1
                    fetched_online_count += 1

            max_wave = max((len(lst) for lst in per_episode_pending), default=0)
            for wave_idx in range(max_wave):
                for ep_list in per_episode_pending:
                    if wave_idx < len(ep_list):
                        interleaved.append(ep_list[wave_idx])

        self.check_and_alert_low_inventory(
            remaining_parts=len(interleaved),
            remaining_episodes=remaining_eps_count,
        )
        return interleaved

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

        _draw_centered(235, line1, font_header, (255, 230, 65, 255))
        _draw_centered(312, line2, font_part, (255, 255, 255, 255))

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

        y_sub = 1505
        for sl in sub_lines[:2]:
            h_used = _draw_centered(y_sub, sl, font_sub, (255, 255, 255, 255))
            y_sub += h_used + 14

        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        ov.save(str(overlay_path))
        return overlay_path

    @staticmethod
    def build_part_caption_and_hashtags(part_spec: Dict[str, Any]) -> Tuple[str, str]:
        """Tạo Caption và Hashtag chuẩn (đã qua Bộ lọc 5 Commercial Safe-Guard) cho từng phần của Bông Cúc Trắng trên kênh `audio_dem_khuyen`."""
        ep_num = int(part_spec["ep_num"])
        part_num = int(part_spec["part_num"])
        total_parts = int(part_spec["total_parts"])
        title = str(part_spec["title"]).strip()
        is_final = bool(part_spec.get("is_final_part"))

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
            f"📖 Bông Cúc Số {ep_num} [{part_label}]: {title}. Diễn biến tiếp theo cực kỳ kịch tính, nghe hết để xem màn đáp trả phía sau nhé!",
            f"🎧 [Truyện Đêm Khuya] Bông Cúc Số {ep_num} ({part_label}) — {title}. Từng tình tiết đều cuốn không rời tai, bạn nghĩ sao về diễn biến phần này?",
            f"🔥 Bông Cúc Số {ep_num} [{part_label}]: {title}. Lòng người khó đoán, chỉ khi biến cố ập tới mới rõ chân tướng! Cùng nghe tiếp nhé!",
            f"🌙 Thức đêm nghe truyện: Bông Cúc Số {ep_num} ({part_label}) - {title}. Đừng bỏ lỡ những tình tiết gay cấn nhất trong phần này nhé!",
        ]
        caption = hooks[(ep_num + part_num) % len(hooks)]
        try:
            from .translator import SubtitleTranslator

            caption = SubtitleTranslator._apply_commercial_safeguard(caption)
        except Exception:
            pass
        hashtags = (
            f"#bongcucso{ep_num} #bongcucaudio #audiodemkhuya #truyenaudio "
            "#kechuyendemkhuya #truyenngan #dramatiktok #xuhuong"
        )
        return caption, hashtags

    def render_single_part(
        self,
        part_spec: Dict[str, Any],
        target_ready_dir: Path,
        seq_offset_seconds: int = 0,
    ) -> Dict[str, Any]:
        """
        Cắt, tua `~1.42x`, đóng khung dọc `1080x1920` và áp dụng ĐẦY ĐỦ 6 BỘ LỌC CHỐNG PHÁT HIỆN TIKTOK
        (Visual Anti-pHash/vHash + AudioPrint Evasion + Device Metadata Signature + Commercial Safe-Guard)
        cho 1 phần từ video YouTube `@bongcucaudio`, xuất trực tiếp vào `output/audio_dem_khuyen/ready/`.
        """
        ep_num = int(part_spec["ep_num"])
        part_num = int(part_spec["part_num"])
        total_parts = int(part_spec["total_parts"])
        yt_id = str(part_spec["yt_id"])
        title = str(part_spec["title"])
        story_id = str(part_spec["story_id"])
        start_sec = float(part_spec["start_sec"])
        orig_dur = float(part_spec["orig_duration"])

        try:
            from .translator import SubtitleTranslator

            title = SubtitleTranslator._apply_commercial_safeguard(title)
        except Exception:
            pass

        full_mp4 = self.ensure_youtube_full_video(yt_id)
        overlay_png = self.cache_dir / f"ov_v2_s{ep_num}_p{part_num}of{total_parts}.png"
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
        out_name = f"{ts_val}_tiktok_story_{story_id}_bongcuc_v2_s{ep_num}_p{part_num}.mp4"
        out_path = target_ready_dir / out_name
        tmp_render = self.cache_dir / f"rendering_{story_id}.mp4"
        tmp_render.unlink(missing_ok=True)

        # --- BỘ LỌC 1 & 2: Biến thiên vi mô (Deterministic Per-Part Jitter) chống trùng lặp cấu trúc ---
        seed_val = ep_num * 31 + part_num * 17
        speed_factor = round(1.412 + (seed_val % 25) * 0.001, 3)  # 1.412x .. 1.436x (quanh 1.42x)
        crop_x_ratio = round(0.206 + (seed_val % 9) * 0.001, 3)   # 0.206 .. 0.214 (cắt sạch chữ dọc bên trái)
        crop_y_ratio = round(0.126 + ((seed_val // 3) % 9) * 0.001, 3)  # 0.126 .. 0.134 (cắt sạch viền trên/dưới)

        # Dịch cao độ vi mô (+1.6% .. +2.1%) kết hợp bù tốc độ chính xác để phá vỡ 100% AudioPrint YouTube/TikTok
        pitch_factor = round(1.016 + (seed_val % 6) * 0.001, 4)
        atempo_comp = round(speed_factor / pitch_factor, 5)

        # --- BỘ LỌC 3: Đa dạng hóa GOP Keyframe, CRF & Bitrate âm thanh + Chữ ký VN Video Editor (iOS) ---
        gop_val = 48 + (seed_val % 17)  # 48 .. 64
        crf_val = 21 + (seed_val % 2)   # 21 hoặc 22
        audio_br = "192k" if (seed_val % 2 == 0) else "160k"
        creation_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000000Z")

        # Lật ngang (`hflip`) phần tranh tô màu `[fg]` TRƯỚC khi đóng khung và chèn tiêu đề Tiếng Việt `[1:v]`
        # -> Phá vỡ hoàn toàn mã băm hình ảnh pHash/vHash so với YouTube mà chữ Tiếng Việt vẫn thuận chiều 100%!
        filter_complex = (
            f"[0:v]setpts=PTS/{speed_factor},fps=24,split=2[bg][fg];"
            "[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,hflip,"
            "boxblur=9:2,eq=brightness=-0.18:saturation=0.86:contrast=1.04,scale=1080:1920[bg_out];"
            f"[fg]crop=ih*0.80:ih*0.74:iw*{crop_x_ratio}:ih*{crop_y_ratio},hflip,"
            "eq=contrast=1.04:brightness=0.008:saturation=1.06:gamma=1.02,"
            "unsharp=3:3:0.55:3:3:0.0,vignette=PI/7,scale=968:-2,"
            "pad=w=iw+12:h=ih+12:x=6:y=6:color=white@0.22[fg_out];"
            "[bg_out][fg_out]overlay=(W-w)/2:(H-h)/2,noise=alls=3:allf=t+u[v1];"
            "[v1][1:v]overlay=0:0[vout];"
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
            "-i",
            str(overlay_png),
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
            f"[BongCucSplitter] Đang cắt & áp dụng 6 Lớp Bộ Lọc Anti-Detection ({speed_factor}x, pitch={pitch_factor}x, hflip+grain, GOP={gop_val}): "
            f"Bông Cúc Số {ep_num} [Phần {part_num}/{total_parts}] ({start_sec:.1f}s -> {start_sec + orig_dur:.1f}s gốc)..."
        )
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=300)
        if res.returncode != 0 or not tmp_render.exists() or tmp_render.stat().st_size < 500_000:
            raise RuntimeError(
                f"Lỗi ffmpeg khi cắt Bông Cúc Số {ep_num} Phần {part_num}: {res.stderr[-400:]}"
            )

        shutil.move(str(tmp_render), str(out_path))
        actual_out_dur = get_media_duration(out_path) or part_spec["tiktok_duration"]
        caption, hashtags = self.build_part_caption_and_hashtags(part_spec)

        logger.info(
            f"[BongCucSplitter] Hoàn tất xuất bản (Chuẩn 6 Bộ Lọc): {out_path.name} "
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
        }
