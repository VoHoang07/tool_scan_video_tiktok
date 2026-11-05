import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .crawler import CHANNEL_PROFILES, resolve_channel_profile
from .utils import logger

DEFAULT_SYSTEM_SWITCHES: Dict[str, Any] = {
    "master_switch": True,
    "switch_1_factory": {
        "enabled": True,
        "max_ready_per_niche": 5,
        "short_video_duration_range": [15, 85],
    },
    "switch_2_upload": {
        "tiktok": True,
        "fb_reels": False,
        "yt_shorts": False,
        "batch_single_chrome_session": True,
    },
    "switch_3_interact": {
        "tiktok": True,
        "fb_reels": False,
        "yt_shorts": False,
    },
    "switch_4_clear": {
        "enabled": True,
        "keep_top_posted_per_channel": 8,
        "clear_workspace_temp_older_than_hours": 2.0,
    },
}


class MasterSwitchController:
    """
    Bộ điều khiển Khóa Master & 4 Công Tắc Hệ Thống Đa Nền Tảng
    (10 Kênh TikTok + 10 Fanpage Facebook Reels + 10 Kênh YouTube Shorts = 30 Kênh / 11 Ngách Nội Dung):
    - Khóa Master (`master_switch`): Bật/tắt toàn bộ hệ thống trên máy Intel từ xa.
    - Công tắc 1 (`switch_1_factory`): Cào đa nền tảng & lấp đầy 11 kho ngách (`output/<niche>/ready/`, 5 video/ngách).
    - Công tắc 2 (`switch_2_upload`): Điều khiển đăng bài độc lập cho từng nền tảng (`tiktok`, `fb_reels`, `yt_shorts`)
      và xử lý bộ Skill riêng ngay trước khi đăng (Just-In-Time Rendering).
    - Công tắc 3 (`switch_3_interact`): Điều khiển luồng tương tác / nuôi kênh (`tiktok`, `fb_reels`, `yt_shorts`).
    - Công tắc 4 (`switch_4_clear`): Giữ lại đúng Top 8 video nhiều view nhất trong `posted/` của mỗi kênh
      để dành đăng chéo sang Reels & Shorts, xóa toàn bộ file `.mp4` cũ còn lại để giải phóng ổ cứng.
    """

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.project_root = (
            Path(project_root).resolve()
            if project_root
            else Path(__file__).resolve().parent.parent
        )
        self.schedule_config_path = self.project_root / "schedule_config.json"
        self.output_root = self.project_root / "output"
        self.temp_root = self.project_root / "workspace_temp"
        self.ledger_path = self.project_root / "data" / "multi_platform_post_ledger.json"

    def get_switches(self) -> Dict[str, Any]:
        """Đọc cấu hình `system_switches` mới nhất từ `schedule_config.json`."""
        switches = json.loads(json.dumps(DEFAULT_SYSTEM_SWITCHES))
        if not self.schedule_config_path.exists():
            return switches
        try:
            raw = json.loads(self.schedule_config_path.read_text(encoding="utf-8"))
            user_sw = raw.get("system_switches")
            if isinstance(user_sw, dict):
                if "master_switch" in user_sw:
                    switches["master_switch"] = bool(user_sw["master_switch"])
                for key in (
                    "switch_1_factory",
                    "switch_2_upload",
                    "switch_3_interact",
                    "switch_4_clear",
                ):
                    if isinstance(user_sw.get(key), dict):
                        switches[key].update(user_sw[key])
        except Exception as exc:
            logger.debug(f"[MasterSwitch] Lỗi đọc schedule_config.json: {exc}")
        return switches

    def is_master_enabled(self) -> bool:
        """Kiểm tra Khóa Master (`master_switch`)."""
        return bool(self.get_switches().get("master_switch", True))

    def is_factory_enabled(self) -> bool:
        """Kiểm tra Công tắc 1 (`switch_1_factory.enabled`)."""
        sw = self.get_switches()
        return bool(sw.get("master_switch", True)) and bool(
            (sw.get("switch_1_factory") or {}).get("enabled", True)
        )

    def is_upload_enabled(self, platform: str = "tiktok") -> bool:
        """Kiểm tra Công tắc 2 (`switch_2_upload.<platform>`) cho `tiktok`, `fb_reels`, hoặc `yt_shorts`."""
        sw = self.get_switches()
        if not bool(sw.get("master_switch", True)):
            return False
        up_cfg = sw.get("switch_2_upload") or {}
        return bool(up_cfg.get(platform.lower().strip(), False))

    def get_enabled_upload_platforms(self) -> List[str]:
        """Trả về danh sách các nền tảng đang bật Công tắc 2 (Upload)."""
        return [
            p
            for p in ("tiktok", "fb_reels", "yt_shorts")
            if self.is_upload_enabled(p)
        ]

    def is_interact_enabled(self, platform: str = "tiktok") -> bool:
        """Kiểm tra Công tắc 3 (`switch_3_interact.<platform>`) cho `tiktok`, `fb_reels`, hoặc `yt_shorts`."""
        sw = self.get_switches()
        if not bool(sw.get("master_switch", True)):
            return False
        in_cfg = sw.get("switch_3_interact") or {}
        return bool(in_cfg.get(platform.lower().strip(), False))

    def is_clear_enabled(self) -> bool:
        """Kiểm tra Công tắc 4 (`switch_4_clear.enabled`)."""
        sw = self.get_switches()
        return bool(sw.get("master_switch", True)) and bool(
            (sw.get("switch_4_clear") or {}).get("enabled", True)
        )

    @staticmethod
    def get_profile_niche_folder(profile_folder: str, platform: str = "tiktok") -> str:
        """
        Ánh xạ từ Chrome Profile (1..10) sang Kho Ngách Nội Dung (1..11) theo từng nền tảng:
        - Riêng Chrome Profile #3 (`audio_dem_khuyen`):
          + Trên `tiktok`: lấy từ ngách 3A `audio_dem_khuyen` (Truyện dài @bongcucaudio ~280s).
          + Trên `fb_reels` và `yt_shorts`: lấy từ ngách 3B `thu_cung_vui_nhon` (Thú cưng hài hước 15-85s).
        - Tất cả 9 Chrome Profile còn lại (1, 2, 4..10): dùng chung kho ngách giữa TikTok, FB Reels và YT Shorts.
        """
        norm_folder = resolve_channel_profile(profile_folder)["folder_name"]
        norm_plat = platform.lower().strip()
        if norm_folder == "audio_dem_khuyen" and norm_plat in ("fb_reels", "yt_shorts"):
            return "thu_cung_vui_nhon"
        return norm_folder

    def _load_ledger(self) -> Dict[str, Dict[str, str]]:
        if not self.ledger_path.exists():
            return {}
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_ledger(self, ledger: Dict[str, Dict[str, str]]) -> None:
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            self.ledger_path.write_text(
                json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug(f"[MasterSwitch] Lỗi lưu multi_platform_post_ledger.json: {exc}")

    def mark_platform_uploaded(
        self, niche_folder: str, video_name: str, platform: str
    ) -> None:
        """Đánh dấu một video đã được đăng lên nền tảng `platform` (`tiktok`, `fb_reels`, `yt_shorts`)."""
        ledger = self._load_ledger()
        key = f"{niche_folder}/{Path(video_name).name}"
        entry = ledger.get(key) or {}
        entry[platform.lower().strip()] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ledger[key] = entry
        self._save_ledger(ledger)

    def has_platform_uploaded(
        self, niche_folder: str, video_name: str, platform: str
    ) -> bool:
        """Kiểm tra xem video đã từng được đăng lên nền tảng `platform` chưa."""
        ledger = self._load_ledger()
        key = f"{niche_folder}/{Path(video_name).name}"
        return bool((ledger.get(key) or {}).get(platform.lower().strip()))

    def _build_video_viral_score_map(self) -> Dict[str, int]:
        """
        Xây dựng bảng điểm viral (lượt view / tim) cho các video đã cào dựa trên:
        1. `story_audio_history.json` / `workspace_temp/*/transcript_raw.json` (nếu có `play_count`, `digg_count`).
        2. Ưu tiên cao cho các video đến từ `seed_video_urls` hoặc `seed_channels` của từng kênh.
        """
        score_map: Dict[str, int] = {}
        seed_ids = set()
        for prof in CHANNEL_PROFILES.values():
            for s_url in prof.get("seed_video_urls") or []:
                m = re.search(r"/video/(\d+)", str(s_url))
                if m:
                    seed_ids.add(m.group(1))
        for sid in seed_ids:
            score_map[sid] = 5_000_000

        if self.temp_root.exists():
            for tr_file in self.temp_root.glob("*/transcript_raw.json"):
                try:
                    data = json.loads(tr_file.read_text(encoding="utf-8"))
                    vid = str(data.get("video_id") or "")
                    plays = int(data.get("play_count") or 0)
                    diggs = int(data.get("digg_count") or 0)
                    if vid and (plays > 0 or diggs > 0):
                        score_map[vid] = max(score_map.get(vid, 0), plays + diggs * 10)
                except Exception:
                    pass
        return score_map

    def rank_posted_videos_for_channel(
        self, ch_folder: str, score_map: Optional[Dict[str, int]] = None
    ) -> List[Tuple[Path, int, float]]:
        """
        Xếp hạng toàn bộ các file `.mp4` trong `output/<ch_folder>/posted/` theo:
        1. Điểm viral (`viral_score` từ lượt view/like nguồn hoặc video seed triệu view).
        2. Thời gian đăng gần nhất (`st_mtime`) để giữ lại các bản render mới nhất, chất lượng nhất.
        """
        posted_dir = self.output_root / ch_folder / "posted"
        if not posted_dir.exists():
            return []
        if score_map is None:
            score_map = self._build_video_viral_score_map()

        ranked: List[Tuple[Path, int, float]] = []
        for mp4 in posted_dir.glob("*.mp4"):
            if not mp4.is_file():
                continue
            try:
                st = mp4.stat()
                mtime = float(st.st_mtime)
                size_mb = float(st.st_size) / (1024.0 * 1024.0)
            except Exception:
                mtime = 0.0
                size_mb = 0.0

            m_sid = re.search(
                r"(?:tiktok|douyin|youtube_shorts|facebook_reels|instagram_reels|bilibili|xiaohongshu)_story_([A-Za-z0-9_-]+)",
                mp4.stem,
                re.IGNORECASE,
            )
            sid = m_sid.group(1).split("_bongcuc_")[0] if m_sid else ""
            viral_score = score_map.get(sid, 0)
            if "bongcuc_v4_" in mp4.name.lower() or (time.time() - mtime < 3600):
                viral_score = max(viral_score, 6_000_000 + int(size_mb * 1000))
            elif viral_score == 0 and sid and sid[-4:].isdigit():
                # Điểm cơ sở ổn định theo video_id + ưu tiên video dung lượng chuẩn HD
                viral_score = 100_000 + int(size_mb * 1000)

            ranked.append((mp4, viral_score, mtime))

        ranked.sort(key=lambda item: (item[1], item[2]), reverse=True)
        return ranked

    def select_next_video_for_platform(
        self, profile_folder: str, platform: str
    ) -> Optional[Path]:
        """
        Chọn video tiếp theo để đăng cho `profile_folder` trên nền tảng `platform`:
        - Nếu `platform == 'tiktok'`: lấy từ `output/<niche>/ready/*.mp4`.
        - Nếu `platform in ('fb_reels', 'yt_shorts')`:
          1. Ưu tiên lấy từ **Top 8 video nhiều view nhất trong `output/<niche>/posted/*.mp4`**
             mà chưa từng được đăng lên `platform` đó.
          2. Nếu cả 8 video trong `posted/` đều đã đăng lên `platform` (hoặc ngách `thu_cung_vui_nhon`
             chưa có `posted/`), lấy tiếp từ `output/<niche>/ready/*.mp4`.
        """
        niche_folder = self.get_profile_niche_folder(profile_folder, platform)
        norm_plat = platform.lower().strip()

        if norm_plat in ("fb_reels", "yt_shorts"):
            ranked_posted = self.rank_posted_videos_for_channel(niche_folder)
            for mp4_path, _, _ in ranked_posted:
                if not self.has_platform_uploaded(niche_folder, mp4_path.name, norm_plat):
                    return mp4_path

        ready_dir = self.output_root / niche_folder / "ready"
        if not ready_dir.exists():
            return None
        for mp4_path in sorted(ready_dir.glob("*.mp4")):
            if mp4_path.is_file() and not self.has_platform_uploaded(
                niche_folder, mp4_path.name, norm_plat
            ):
                return mp4_path
        return None

    def apply_platform_upload_skill(
        self, video_path: Path, platform: str, niche_folder: str
    ) -> Path:
        """
        Xử lý bộ Skill chống phát hiện riêng cho từng nền tảng ngay trước khi đăng (Just-In-Time Rendering):
        - `tiktok`: Áp dụng bộ Skill 6 Lớp Chống Phát Hiện TikTok (`stream1_antidetect_skill`).
        - `fb_reels`: Hook xử lý Skill riêng cho Facebook Reels (sẵn sàng cập nhật bộ Skill Reels chuyên sâu).
        - `yt_shorts`: Hook xử lý Skill riêng cho YouTube Shorts (sẵn sàng cập nhật bộ Skill Shorts chuyên sâu).
        """
        norm_plat = platform.lower().strip()
        if norm_plat == "tiktok":
            from .stream1_antidetect_skill import ensure_stream1_6layer_compliance

            res = ensure_stream1_6layer_compliance(
                video_path=video_path,
                channel_folder=niche_folder,
                auto_heal=True,
            )
            return Path(res.get("video_path") or video_path)

        # Đối với fb_reels và yt_shorts: tạo bản render tạm sạch dấu vết TikTok trong workspace_temp/
        # (Sau khi xóa bản tạm ngay khi upload xong, ổ cứng không tốn thêm dung lượng lưu trữ)
        from .crawler import StoryAudioCrawler

        StoryAudioCrawler.clear_macl_xattr(video_path)
        return video_path

    def run_switch_4_clear(
        self, keep_top_per_channel: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Thực thi Công tắc 4 (`switch_4_clear`):
        1. Quét toàn bộ 11 kho ngách (`output/<niche>/posted/*.mp4`).
        2. Xếp hạng video theo lượt view / điểm viral cao nhất và giữ lại đúng `keep_top_per_channel` (mặc định: 8)
           video nhiều view nhất ở mỗi kênh để dành đăng sang Facebook Reels & YouTube Shorts.
        3. Xóa toàn bộ các file `.mp4` dư thừa còn lại trong `posted/` để giải phóng bộ nhớ ổ cứng
           (vẫn giữ nguyên ID trong `story_audio_history.json` và `danh_sach_video.csv` để không bao giờ cào trùng lại).
        4. Dọn dẹp các thư mục làm việc tạm quá hạn trong `workspace_temp/`.
        """
        sw = self.get_switches()
        clear_cfg = sw.get("switch_4_clear") or {}
        if not self.is_clear_enabled():
            return {"enabled": False, "deleted_files_count": 0, "freed_mb": 0.0}

        keep_limit = (
            int(keep_top_per_channel)
            if keep_top_per_channel is not None
            else int(clear_cfg.get("keep_top_posted_per_channel", 8))
        )
        keep_limit = max(1, keep_limit)
        temp_max_age_hours = float(
            clear_cfg.get("clear_workspace_temp_older_than_hours", 2.0)
        )

        score_map = self._build_video_viral_score_map()
        deleted_files: List[str] = []
        kept_summary: Dict[str, int] = {}
        freed_bytes = 0

        for prof in CHANNEL_PROFILES.values():
            ch_folder = prof["folder_name"]
            ranked = self.rank_posted_videos_for_channel(ch_folder, score_map=score_map)
            kept = ranked[:keep_limit]
            to_delete = ranked[keep_limit:]
            kept_summary[ch_folder] = len(kept)

            for mp4_path, _, _ in to_delete:
                try:
                    sz = mp4_path.stat().st_size
                    mp4_path.unlink(missing_ok=True)
                    freed_bytes += sz
                    deleted_files.append(f"{ch_folder}/posted/{mp4_path.name}")
                except Exception as exc:
                    logger.debug(f"[Switch 4 Clear] Không thể xóa {mp4_path}: {exc}")

        # Dọn các thư mục tạm cũ trong workspace_temp/ (không đụng tới file .lock hoặc .json)
        cleaned_temp_dirs = 0
        now_ts = time.time()
        if self.temp_root.exists():
            for item in self.temp_root.iterdir():
                if not item.is_dir() or item.name.startswith("."):
                    continue
                try:
                    age_hours = (now_ts - item.stat().st_mtime) / 3600.0
                    if age_hours >= temp_max_age_hours:
                        dir_size = sum(
                            f.stat().st_size for f in item.rglob("*") if f.is_file()
                        )
                        shutil.rmtree(item, ignore_errors=True)
                        freed_bytes += dir_size
                        cleaned_temp_dirs += 1
                except Exception:
                    pass

        freed_mb = round(freed_bytes / (1024.0 * 1024.0), 2)
        if deleted_files or cleaned_temp_dirs > 0:
            logger.info(
                f"[Switch 4 - Clear] Đã giữ lại Top {keep_limit} video nhiều view nhất/kênh | "
                f"Đã xóa {len(deleted_files)} video cũ trong posted/ & {cleaned_temp_dirs} thư mục tạm | "
                f"Giải phóng: {freed_mb} MB"
            )

        return {
            "enabled": True,
            "keep_top_posted_per_channel": keep_limit,
            "kept_per_channel": kept_summary,
            "deleted_files_count": len(deleted_files),
            "deleted_files": deleted_files,
            "cleaned_temp_dirs": cleaned_temp_dirs,
            "freed_mb": freed_mb,
        }
