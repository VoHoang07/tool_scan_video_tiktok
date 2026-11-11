import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from PIL import Image, ImageDraw, ImageFont

from .utils import ensure_ffmpeg, get_media_duration, logger

# ==============================================================================
# CẤU HÌNH NGUỒN YOUTUBE @GauSoHaiTV CHO KÊNH `video_AI_hay`
# - Thể loại: Hoạt hình Gấu Trúc Đen (Black Panda 2D Mystery / Comedy Storytelling)
# - Quy tắc bảo mật nguồn: XÓA SẠCH 100% khung viền/logo/tên kênh "Gấu Sợ Hãi TV / GSH"
# - Quy tắc lịch đăng: Cắt thành nhiều phần đều nhau và ĐĂNG XEN KẼ THEO SÓNG ĐA TẬP
#   (4-5 tập xoay vòng + tỷ lệ 4 Phần Tiếp Theo : 1 Tập Mới Phần 1, TUYỆT ĐỐI KHÔNG SO LE 1:1)
# ==============================================================================
GAUSOHAI_YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@GauSoHaiTV/videos"

# Tốc độ tua chuẩn cho hoạt hình Gấu Trúc Đen (1.20x: giữ nguyên phụ đề & nhịp thoại tự nhiên)
GAUSOHAI_SPEED_FACTOR = 1.20

# Thời lượng mục tiêu mỗi phần trên TikTok (~210s = 3 phút 30 giây sau khi tua 1.20x)
GAUSOHAI_TARGET_PART_SECONDS = 210.0

# Ngưỡng cảnh báo khi kho phần video còn ít
GAUSOHAI_LOW_QUEUE_WARNING_THRESHOLD = 15

# Danh mục dự phòng (Fallback Catalog) gồm các tập tiêu biểu trên @GauSoHaiTV (đã làm sạch 100% tên kênh)
GAUSOHAI_DEFAULT_CATALOG: List[Dict[str, Any]] = [
    {
        "ep_num": 363,
        "yt_id": "5fqJS-jxGXI",
        "title": "Quán Đậu Phụ Thối Trước Cổng Trường",
        "duration": 2417.0,
        "view_count": 12000,
    },
    {
        "ep_num": 362,
        "yt_id": "3LY78YVIMxo",
        "title": "Lạc Vào Nhóm Chat Toàn Ma",
        "duration": 1883.0,
        "view_count": 23000,
    },
    {
        "ep_num": 361,
        "yt_id": "slDFPrkXwag",
        "title": "Tránh Xa Xe Tải Chở Hình Nhân Giấy",
        "duration": 1626.0,
        "view_count": 66000,
    },
    {
        "ep_num": 360,
        "yt_id": "gxNNlRu3_Wg",
        "title": "Phật Ra Tay Diệt Trừ Kẻ Ác",
        "duration": 1950.0,
        "view_count": 45000,
    },
    {
        "ep_num": 359,
        "yt_id": "pFFpAKfH9Ac",
        "title": "Đừng Để Dê Hát Chèo",
        "duration": 2120.0,
        "view_count": 38000,
    },
    {
        "ep_num": 358,
        "yt_id": "RP1a236NwdI",
        "title": "Điều Lệ Chơi Kéo Búa Bao",
        "duration": 1840.0,
        "view_count": 52000,
    },
    {
        "ep_num": 357,
        "yt_id": "Rzmnt4yldvs",
        "title": "Tìm Đầu Cho Ma",
        "duration": 2050.0,
        "view_count": 41000,
    },
    {
        "ep_num": 356,
        "yt_id": "8zSR7mEwDcc",
        "title": "Hồn Ma Nuôi Con",
        "duration": 2190.0,
        "view_count": 49000,
    },
    {
        "ep_num": 355,
        "yt_id": "QGkjbLzZyuA",
        "title": "Giếng Ma Hồi Sinh Người",
        "duration": 2310.0,
        "view_count": 61000,
    },
    {
        "ep_num": 354,
        "yt_id": "J5ruvkiIrH0",
        "title": "Bí Mật Cao Trường Sinh",
        "duration": 2994.0,
        "view_count": 74000,
    },
    {
        "ep_num": 353,
        "yt_id": "8w8ykeJoE2U",
        "title": "Chuyến Tàu Có Quái Vật Ăn Thịt Người",
        "duration": 2237.0,
        "view_count": 31000,
    },
    {
        "ep_num": 352,
        "yt_id": "FRBVrL_DYIs",
        "title": "Ngọn Đèn Trấn Hồn Dưới Đáy Sông",
        "duration": 2180.0,
        "view_count": 58000,
    },
]


def make_gausohai_story_id(ep_num: int, part_num: int) -> str:
    """Tạo mã story_id số duy nhất cho từng phần Gấu Trúc Đen (ví dụ: Tập 361 Phần 2 -> '880036102')."""
    return f"8800{int(ep_num):03d}{int(part_num):02d}"


def parse_gausohai_ep_and_part(filename_or_id: str) -> Optional[Tuple[int, int]]:
    """
    Trích xuất `(ep_num, part_num)` từ tên file hoặc story_id của kênh `video_AI_hay`:
    - Khớp `gautrucden_v1_s361_p2`, `gautrucden_s361_p2` hoặc `880036102`.
    """
    text = str(filename_or_id or "")
    m = re.search(r"gautrucden_(?:v\d+_)?s(\d+)_p(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    m2 = re.search(r"\b8800(\d{3})(\d{2})\b", text)
    if m2:
        return int(m2.group(1)), int(m2.group(2))
    return None


class GauTrucDenYouTubeSplitter:
    """
    Bộ tải, xóa nguồn (De-Branding) & cắt nhiều phần tự động cho kênh `video_AI_hay`
    từ nguồn YouTube `https://www.youtube.com/@GauSoHaiTV` (thể loại Hoạt hình Gấu Trúc Đen):
    1. Cắt bỏ hoàn toàn khung viền/banner "Gấu Sợ Hãi TV | GSH | FOLLOW ME ON MY SOCIAL MEDIA!"
       ở phía trên (`19.8%`), hai bên (`4.8%`) và viền dưới (`1.6%`), giữ trọn vẹn 100% khung hoạt hình
       và phụ đề tiếng Việt gốc (KHÔNG lật ngược ngang `hflip` phần hình chính để không bị ngược chữ phụ đề).
    2. Làm sạch 100% tiêu đề, caption, hashtag và overlay trên video — tuyệt đối không để bất kỳ
       thông tin nào liên quan tới tên kênh "Gấu Sợ Hãi", "GauSoHaiTV", "GSH".
    3. Cắt mỗi tập thành các phần đều nhau (`~210s` = 3m30s trên TikTok) và sắp xếp lịch đăng
       **XEN KẼ ĐA TẬP (KHÔNG SO LE 1:1)**:
       - Xoay vòng đồng thời trên nhóm 4-5 tập đang chiếu (`Tập A -> Tập B -> Tập C -> Tập D...`),
         khóa cooldown 3 bài liên tiếp không lặp lại cùng 1 tập (chặn đứng hiện tượng so le 1:1 `A - B - A - B`).
       - Đan xen theo sóng `4 Phần Tiếp Theo : 1 Tập Mới Phần 1` (chặn đứng hiện tượng so le 1:1 giữa phần mới và phần tiếp theo).
    """

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.cache_dir = self.project_root / "workspace_temp" / "gausohai_yt_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.meta_cache_path = self.cache_dir / "episodes_duration_cache.json"
        self.catalog_path = self.project_root / "data" / "gausohai_full_catalog.json"
        self.ffmpeg_bin, _ = ensure_ffmpeg()

    @staticmethod
    def clean_story_title_no_source(raw_title: str) -> str:
        """
        Làm sạch tiêu đề tập phim hoạt hình Gấu Trúc Đen, xóa tuyệt đối mọi dấu vết
        của kênh nguồn ('Gấu Sợ Hãi', 'Gấu Sợ Hãi TV', 'GauSoHai', 'GSH', 'Phim kinh dị...', 'Cực Bánh Cuốn').
        """
        t = str(raw_title or "").strip()
        parts = [p.strip() for p in re.split(r"\s*\|\s*", t) if p.strip()]
        kept: List[str] = []
        for p in parts:
            if re.search(
                r"(?i)(?:gấu\s*sợ\s*hãi|gausohai|\bgsh\b|phim\s*kinh\s*dị|hoạt\s*hình\s*kinh\s*dị|kể\s*chuyện\s*ma)",
                p,
            ):
                sub = re.sub(
                    r"(?i)(?:phim\s*kinh\s*dị\s*|hoạt\s*hình\s*kinh\s*dị\s*)?(?:gấu\s*sợ\s*hãi(?:\s*tv)?|gausohai(?:\s*tv)?|\bgsh\b)(?:\s*cực\s*bánh\s*cuốn)?",
                    "",
                    p,
                ).strip(" -—|:.,!")
                if (
                    sub
                    and len(sub) > 6
                    and not re.match(
                        r"(?i)^(?:full|tập\s*\d+|phần\s*\d+|cực\s*bánh\s*cuốn)$", sub
                    )
                ):
                    kept.append(sub)
            else:
                kept.append(p)
        res = " - ".join(kept) if kept else t
        res = re.sub(
            r"(?i)(?:phim\s*kinh\s*dị\s*|hoạt\s*hình\s*kinh\s*dị\s*)?(?:gấu\s*sợ\s*hãi(?:\s*tv)?|gausohai(?:\s*tv)?|\bgsh\b)(?:\s*cực\s*bánh\s*cuốn)?",
            "",
            res,
        )
        res = re.sub(r"\s{2,}", " ", res).strip(" -—|:.,!")
        try:
            from .community_guidelines_guard import TikTokCommunityGuidelinesGuard

            res = TikTokCommunityGuidelinesGuard.sanitize_text(res)
        except Exception:
            pass
        return res or "Chuyện Kỳ Bí Gấu Trúc Đen"

    @staticmethod
    def _slugify_vietnamese_hashtag(text: str) -> str:
        """Chuyển tên câu chuyện thành hashtag viết liền không dấu."""
        s = str(text or "").strip().lower()
        s = s.replace("đ", "d").replace("Đ", "d")
        s = unicodedata.normalize("NFD", s)
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
        s = re.sub(r"[^a-z0-9]+", "", s)
        if not s or "gausohai" in s:
            return "#gautrucden"
        return f"#{s[:34]}"

    def get_full_catalog(self) -> List[Dict[str, Any]]:
        """Đọc danh mục đầy đủ các tập từ `data/gausohai_full_catalog.json` (hoặc fallback mặc định)."""
        from .community_guidelines_guard import TikTokCommunityGuidelinesGuard

        if self.catalog_path.exists():
            try:
                data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) >= 5:
                    cleaned_list: List[Dict[str, Any]] = []
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        ep_no = int(item.get("ep_num") or 0)
                        if TikTokCommunityGuidelinesGuard.is_episode_blocked("video_AI_hay", ep_no):
                            continue
                        cp = dict(item)
                        cp["title"] = self.clean_story_title_no_source(str(cp.get("title") or ""))
                        cleaned_list.append(cp)
                    return cleaned_list
            except Exception:
                pass
        return list(GAUSOHAI_DEFAULT_CATALOG)

    def sync_new_episodes_from_youtube(self) -> int:
        """Tự động đồng bộ các tập mới từ kênh YouTube `@GauSoHaiTV` vào `data/gausohai_full_catalog.json`."""
        try:
            import yt_dlp

            current_cat = self.get_full_catalog()
            known_yt_ids = {str(item.get("yt_id")) for item in current_cat}
            max_ep_num = max((int(item.get("ep_num") or 0) for item in current_cat), default=363)

            with yt_dlp.YoutubeDL(
                {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
            ) as ydl:
                info = ydl.extract_info(GAUSOHAI_YOUTUBE_CHANNEL_URL, download=False)
                entries = (info or {}).get("entries") or []

            new_entries: List[Dict[str, Any]] = []
            for e in entries:
                vid_id = str(e.get("id") or "").strip()
                raw_title = str(e.get("title") or "").strip()
                if not vid_id or not raw_title or vid_id in known_yt_ids:
                    continue
                new_entries.append(e)
                known_yt_ids.add(vid_id)

            added = 0
            for e in reversed(new_entries):
                max_ep_num += 1
                clean_t = self.clean_story_title_no_source(str(e.get("title") or ""))
                current_cat.insert(
                    0,
                    {
                        "ep_num": max_ep_num,
                        "yt_id": str(e.get("id")),
                        "title": clean_t,
                        "duration": float(e.get("duration") or 1800.0),
                        "view_count": int(e.get("view_count") or 25000),
                    },
                )
                added += 1

            if added > 0:
                self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
                self.catalog_path.write_text(
                    json.dumps(current_cat, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                logger.info(
                    f"[GauTrucDenSplitter] Đã đồng bộ thêm {added} tập mới cho kênh video_AI_hay (Tổng: {len(current_cat)} tập)."
                )
            return added
        except Exception as exc:
            logger.debug(f"[GauTrucDenSplitter] Bỏ qua quét tập mới: {exc}")
            return 0

    def _load_duration_cache(self) -> Dict[str, float]:
        if self.meta_cache_path.exists():
            try:
                loaded = json.loads(self.meta_cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    return {str(k): float(v) for k, v in loaded.items() if v}
            except Exception:
                pass
        return {}

    def _save_duration_cache(self, data: Dict[str, float]) -> None:
        try:
            self.meta_cache_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def get_youtube_video_duration(self, yt_id: str, allow_network: bool = True) -> float:
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
            return 1800.0

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
        except Exception:
            pass
        return 1800.0

    def compute_episode_parts_plan(
        self, ep_info: Dict[str, Any], allow_network: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Chia một tập hoạt hình Gấu Trúc Đen thành `M` phần bằng nhau (~210s trên TikTok sau khi tua 1.20x):
        - Cắt bỏ `1.5s` đầu tiên và `2.0s` cuối cùng của tập để loại bỏ mọi hiệu ứng chuyển cảnh thừa.
        - Lùi nhẹ `1.5s` ở đầu mỗi phần (từ Phần 2 trở đi) để lời thoại và phụ đề nối tiếp mượt mà.
        """
        ep_num = int(ep_info["ep_num"])
        yt_id = str(ep_info["yt_id"])
        title = self.clean_story_title_no_source(str(ep_info.get("title") or ""))
        view_count = int(ep_info.get("view_count") or 0)

        cat_dur = float(ep_info.get("duration") or 0.0)
        if cat_dur > 60.0:
            total_dur = cat_dur
        else:
            total_dur = self.get_youtube_video_duration(yt_id, allow_network=allow_network)

        target_orig_chunk = GAUSOHAI_TARGET_PART_SECONDS * GAUSOHAI_SPEED_FACTOR  # ~252.0s
        start_offset = 1.5
        usable_end = max(start_offset + 90.0, total_dur - 2.0)
        rem_dur = max(90.0, usable_end - start_offset)
        total_parts = max(2, round(rem_dur / target_orig_chunk))
        slice_orig_dur = rem_dur / total_parts

        parts_plan: List[Dict[str, Any]] = []
        for idx in range(total_parts):
            part_num = idx + 1
            seg_start = start_offset + idx * slice_orig_dur
            if idx > 0:
                seg_start = max(0.0, seg_start - 1.5)
            seg_end = min(usable_end, start_offset + (idx + 1) * slice_orig_dur)
            seg_dur_orig = max(30.0, seg_end - seg_start)
            seg_dur_tiktok = round(seg_dur_orig / GAUSOHAI_SPEED_FACTOR, 1)
            story_id = make_gausohai_story_id(ep_num, part_num)

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

    def build_interleaved_queue(
        self, known_posted_or_ready_ids: Set[str]
    ) -> List[Dict[str, Any]]:
        """
        Xây dựng hàng đợi các phần hoạt hình Gấu Trúc Đen cho kênh `video_AI_hay`
        theo nguyên tắc **ĐĂNG XEN KẼ ĐA TẬP — TUYỆT ĐỐI KHÔNG SO LE 1:1**:
        1. Duy trì nhóm sóng gồm **4-5 tập hoạt hình đang chiếu** (`active_pool_size = 5`) thay vì chỉ 2 tập,
           đảm bảo không bao giờ rơi vào trạng thái so le 1:1 (`Tập A - Tập B - Tập A - Tập B`).
        2. Cứ mỗi **4 phần tiếp theo** (`Phần 2, Phần 3...` của các tập đang chiếu) mới chèn **1 Tập Mới Phần 1**
           (tỷ lệ `4:1`, tuyệt đối không so le `1:1` giữa Phần 1 mới và Phần tiếp theo).
        3. Khi kênh mới bắt đầu (chưa có tập nào đang chiếu dở), mở sóng 4 tập đầu tiên
           (`Tập A P1 -> Tập B P1 -> Tập C P1 -> Tập D P1`), sau đó chạy tiếp sóng `Phần 2`, `Phần 3`...
           xoay vòng qua các tập đó.
        """
        catalog = self.get_full_catalog()
        effective_known_ids = set(known_posted_or_ready_ids)

        active_cont_eps: List[Dict[str, Any]] = []
        brand_new_eps: List[Dict[str, Any]] = []

        for ep in catalog:
            ep_num = int(ep.get("ep_num") or 0)
            dur = float(ep.get("duration") or 0.0)
            if dur < 360.0:
                # Bỏ qua các video quá ngắn (< 6 phút) để tập trung vào các tập truyện nhiều phần
                continue
            has_any_part_known = any(
                sid.startswith(f"8800{ep_num:03d}") for sid in effective_known_ids
            )
            if has_any_part_known:
                active_cont_eps.append(ep)
            else:
                brand_new_eps.append(ep)

        # Ưu tiên các tập có thời lượng đẹp (15 - 42 phút ~ 4 đến 10 phần) & kết hợp tập mới + tập nhiều view
        def _ep_priority_score(item: Dict[str, Any]) -> Tuple[int, int, int]:
            d = float(item.get("duration") or 1800.0)
            ep_n = int(item.get("ep_num") or 0)
            vw = int(item.get("view_count") or 0)
            optimal_len = 1 if 900.0 <= d <= 2650.0 else 0
            # Kết hợp các tập mới nhất (ep_num >= 335) và các tập triệu/trăm nghìn view
            recency_boost = 1 if ep_n >= 335 else 0
            return (optimal_len, recency_boost, vw)

        active_cont_eps.sort(key=_ep_priority_score, reverse=True)
        brand_new_eps.sort(key=_ep_priority_score, reverse=True)

        cont_per_ep_lists: List[List[Dict[str, Any]]] = []
        fresh_part1_specs: List[Dict[str, Any]] = []
        new_eps_followup_lists: List[List[Dict[str, Any]]] = []

        for ep_info in active_cont_eps:
            plan = self.compute_episode_parts_plan(ep_info, allow_network=False)
            pending = [p for p in plan if str(p["story_id"]) not in effective_known_ids]
            if pending:
                cont_per_ep_lists.append(pending)

        for ep_info in brand_new_eps:
            plan = self.compute_episode_parts_plan(ep_info, allow_network=False)
            pending = [p for p in plan if str(p["story_id"]) not in effective_known_ids]
            if not pending:
                continue
            if int(pending[0]["part_num"]) == 1:
                fresh_part1_specs.append(pending[0])
                if len(pending) > 1:
                    new_eps_followup_lists.append(pending[1:])
            else:
                cont_per_ep_lists.append(pending)

        # Nếu chưa có đủ 4 tập đang chiếu dở, lấy ngay 4 tập mới đầu tiên đưa vào nhóm xoay vòng chủ lực
        bootstrap_initial_p1: List[Dict[str, Any]] = []
        while len(cont_per_ep_lists) < 4 and fresh_part1_specs and new_eps_followup_lists:
            bootstrap_initial_p1.append(fresh_part1_specs.pop(0))
            cont_per_ep_lists.append(new_eps_followup_lists.pop(0))

        # Sắp xếp các phần tiếp theo theo SÓNG CẤP ĐỘ PHẦN (Phần 2 của 4-5 tập -> Phần 3 của 4-5 tập...)
        continuation_specs: List[Dict[str, Any]] = []
        parts_by_level: Dict[int, List[Dict[str, Any]]] = {}
        for ep_list in cont_per_ep_lists:
            for p_spec in ep_list:
                pt_num = int(p_spec["part_num"])
                parts_by_level.setdefault(pt_num, []).append(p_spec)
        for pt_level in sorted(parts_by_level.keys()):
            continuation_specs.extend(parts_by_level[pt_level])

        # Xếp tiếp các đợt tập mới phía sau theo từng cụm 4 tập (Wave Batch = 4 tập, KHÔNG phải 1:1)
        wave_batch_size = 4
        for b_start in range(0, len(new_eps_followup_lists), wave_batch_size):
            sub_lists = new_eps_followup_lists[b_start : b_start + wave_batch_size]
            sub_by_level: Dict[int, List[Dict[str, Any]]] = {}
            for ep_list in sub_lists:
                for p_spec in ep_list:
                    sub_by_level.setdefault(int(p_spec["part_num"]), []).append(p_spec)
            for pt_level in sorted(sub_by_level.keys()):
                continuation_specs.extend(sub_by_level[pt_level])

        # Ghép hàng đợi XEN KẼ ĐA TẬP (Tỷ lệ 4 Phần Tiếp Theo : 1 Tập Mới Phần 1, KHÔNG SO LE 1:1):
        interleaved: List[Dict[str, Any]] = list(bootstrap_initial_p1)
        cont_idx = 0
        p1_idx = 0
        while cont_idx < len(continuation_specs) or p1_idx < len(fresh_part1_specs):
            # Đẩy 4 phần tiếp theo từ các tập đang chiếu khác nhau
            for _ in range(4):
                if cont_idx < len(continuation_specs):
                    interleaved.append(dict(continuation_specs[cont_idx]))
                    cont_idx += 1
            # Sau mỗi 4 phần tiếp theo mới mở thêm 1 Tập Mới Phần 1
            if p1_idx < len(fresh_part1_specs):
                interleaved.append(dict(fresh_part1_specs[p1_idx]))
                p1_idx += 1

        # Hậu kiểm chống so le 1:1 (Anti-1:1 Ping-Pong Post-Filter):
        # Đảm bảo không có 2 bài liên tiếp cùng 1 tập VÀ không có chuỗi A -> B -> A lặp lại
        cleaned_queue: List[Dict[str, Any]] = []
        deferred: List[Dict[str, Any]] = []
        for item in interleaved:
            ep_c = int(item["ep_num"])
            recent_2 = [int(x["ep_num"]) for x in cleaned_queue[-2:]]
            if ep_c in recent_2:
                deferred.append(item)
            else:
                cleaned_queue.append(item)
                # Thử chèn lại các phần đang chờ nếu đã qua khoảng cách an toàn >= 2 tập khác
                still_deferred: List[Dict[str, Any]] = []
                for d_item in deferred:
                    r2 = [int(x["ep_num"]) for x in cleaned_queue[-2:]]
                    if int(d_item["ep_num"]) not in r2:
                        cleaned_queue.append(d_item)
                    else:
                        still_deferred.append(d_item)
                deferred = still_deferred
        cleaned_queue.extend(deferred)
        return cleaned_queue

    def ensure_youtube_full_video(self, yt_id: str) -> Path:
        """
        Tải video gốc từ YouTube về `workspace_temp/gausohai_yt_cache/yt_<yt_id>.mp4`
        để cắt tất cả các phần của tập đó.
        """
        target_mp4 = self.cache_dir / f"yt_{yt_id}.mp4"
        if target_mp4.exists() and target_mp4.stat().st_size > 2_000_000:
            return target_mp4

        import yt_dlp

        tmp_out = self.cache_dir / f"yt_{yt_id}.part.mp4"
        tmp_out.unlink(missing_ok=True)
        url = f"https://www.youtube.com/watch?v={yt_id}"
        logger.info(
            f"[GauTrucDenSplitter] Đang tải tập phim hoạt hình Gấu Trúc Đen ({yt_id})..."
        )
        ydl_opts = {
            "format": "22/18/best[ext=mp4]/best",
            "outtmpl": str(tmp_out),
            "nopart": True,
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
            f"[GauTrucDenSplitter] Đã tải xong {yt_id} ({target_mp4.stat().st_size / 1e6:.1f} MB, thời lượng={actual_dur:.1f}s)."
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
        Tạo lớp phủ PNG `1080x1920` cho kênh `video_AI_hay` (Thể loại Gấu Trúc Đen):
        - TUYỆT ĐỐI KHÔNG chứa bất kỳ chữ nào liên quan tới kênh nguồn ("Gấu Sợ Hãi", "GSH"...).
        - Phía trên: Thẻ tiêu đề Gấu Trúc Đen + TÊN CÂU CHUYỆN IN HOA nổi bật.
        - Phía dưới: Huy hiệu hiển thị `PHẦN <part_num> / <total_parts>` rõ ràng để giữ chân người xem.
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

        clean_title = GauTrucDenYouTubeSplitter.clean_story_title_no_source(title).upper()
        header_badges = [
            "HOẠT HÌNH GẤU TRÚC ĐEN • CHUYỆN KỲ BÍ",
            "GẤU TRÚC ĐEN • BÍ ẨN ĐÊM KHUYA",
            "SERIES GẤU TRÚC ĐEN • QUY TẮC SINH TỒN",
            "CHUYỆN KỂ GẤU TRÚC ĐEN • CỰC CUỐN",
            "HOẠT HÌNH KỲ BÍ • GẤU TRÚC ĐEN",
        ]
        t_seed = ep_num * 19 + part_num * 37
        badge_text = header_badges[t_seed % len(header_badges)]
        if part_num == total_parts:
            part_text = f"PHẦN {part_num} / {total_parts} (TẬP KẾT)"
        else:
            part_text = f"PHẦN {part_num} / {total_parts} • XEM TIẾP TRÊN KÊNH"

        theme_palettes = [
            ((0, 235, 255, 255), (255, 255, 255, 255)),    # Xanh Cyan Neon & Trắng
            ((255, 210, 60, 255), (255, 255, 255, 255)),   # Vàng Kim & Trắng
            ((130, 255, 190, 255), (255, 250, 230, 255)),  # Xanh Ngọc Lục Bảo & Kem
            ((255, 150, 90, 255), (255, 255, 255, 255)),   # Cam Rực & Trắng
        ]
        accent_color, text_color = theme_palettes[t_seed % len(theme_palettes)]
        y_jitter = ((t_seed % 7) - 3) * 3

        def _draw_centered(
            y_pos: int,
            text: str,
            font: Any,
            fill_rgba: Tuple[int, int, int, int],
            stroke_w: int = 4,
        ) -> int:
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

        # Thẻ Tiêu Đề Phía Trên
        card_top = 250 + y_jitter
        card_bottom = card_top + (225 if len(title_lines) > 1 else 175)
        draw.rounded_rectangle(
            [48, card_top, 1032, card_bottom],
            radius=28,
            fill=(10, 14, 24, 215),
            outline=accent_color,
            width=3,
        )
        _draw_centered(card_top + 22, badge_text, font_tag, accent_color, stroke_w=3)
        y_cursor = card_top + 74
        for tl in title_lines:
            h_used = _draw_centered(y_cursor, tl, font_title, text_color, stroke_w=4)
            y_cursor += h_used + 12

        # Khung Pill Hiển Thị Phần Phía Dưới
        pill_top = 1385 - y_jitter
        pill_bottom = pill_top + 96
        draw.rounded_rectangle(
            [95, pill_top, 985, pill_bottom],
            radius=48,
            fill=(12, 16, 28, 225),
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
        Tạo Caption và Hashtag cho kênh `video_AI_hay`:
        - Tuyệt đối không chứa bất kỳ thông tin gì liên quan tới kênh nguồn (`Gấu Sợ Hãi`, `GauSoHai`, `GSH`).
        - Bám sát thể loại hoạt hình Gấu Trúc Đen + Tên câu chuyện + `[Phần X/Y]`.
        """
        ep_num = int(part_spec["ep_num"])
        part_num = int(part_spec["part_num"])
        total_parts = int(part_spec["total_parts"])
        raw_title = str(part_spec.get("title") or "").strip()
        title = GauTrucDenYouTubeSplitter.clean_story_title_no_source(raw_title)
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
            f"🐼 {title} [{part_label}] — Tình tiết ngày càng gay cấn, xem đến cuối để không bỏ lỡ manh mối quan trọng nhé!",
            f"🔥 {title} ({part_label}) — Series hoạt hình Gấu Trúc Đen cực cuốn, bạn đoán được chuyện gì sắp xảy ra chưa?",
            f"🎬 {title} [{part_label}] — Một quy tắc kỳ bí vừa xuất hiện, ai đang theo dõi bộ này điểm danh ngay nhé!",
            f"⚡ {title} ({part_label}) — Pha xử lý bất ngờ của nhân vật chính, xem tiếp diễn biến ngay sau đây!",
            f"🌙 {title} [{part_label}] — Câu chuyện hoạt hình cuốn không rời mắt từ giây đầu tiên!",
            f"👀 {title} ({part_label}) — Cú bẻ lái không ai ngờ tới trong phần này, vào xem ngay nhé!",
        ]
        seed_idx = ep_num * 11 + part_num * 17
        caption = hooks[seed_idx % len(hooks)]
        try:
            from .translator import SubtitleTranslator

            caption = SubtitleTranslator._apply_commercial_safeguard(caption)
        except Exception:
            pass

        title_slug_tag = GauTrucDenYouTubeSplitter._slugify_vietnamese_hashtag(title)
        part_tag = f"#phan{part_num}"
        base_tags = [
            "#gautrucden",
            "#hoathinhgautrucden",
            "#hoathinh",
            "#chuyenke",
            "#phimhoathinh",
            "#cotichkybi",
            "#xuhuong",
            "#fyp",
        ]
        picked_tags = [title_slug_tag, part_tag]
        for offset in range(6):
            tag_cand = base_tags[(seed_idx + offset * 3) % len(base_tags)]
            if tag_cand not in picked_tags:
                picked_tags.append(tag_cand)
        hashtags = " ".join(picked_tags)
        try:
            from .community_guidelines_guard import TikTokCommunityGuidelinesGuard

            caption, hashtags = TikTokCommunityGuidelinesGuard.sanitize_caption_and_hashtags(
                caption, hashtags, "video_AI_hay"
            )
        except Exception:
            pass
        return caption, hashtags

    def render_single_part(
        self,
        part_spec: Dict[str, Any],
        target_ready_dir: Path,
        seq_offset_seconds: int = 0,
    ) -> Dict[str, Any]:
        """
        Cắt và xuất bản 1 phần video Hoạt hình Gấu Trúc Đen cho kênh `video_AI_hay`:
        1. Cắt bỏ 100% khung viền/banner "Gấu Sợ Hãi TV | GSH | FOLLOW ME ON MY SOCIAL MEDIA!"
           bằng bộ lọc `crop=iw*0.904:ih*0.786:iw*0.048:ih*0.198`.
        2. Giữ nguyên chiều thuận (KHÔNG `hflip` trên `[fg]`) để phụ đề tiếng Việt trong khung hoạt hình
           hiển thị rõ nét, dễ đọc 100%, đồng thời `hflip` + làm mờ lớp nền `[bg]` phía sau.
        3. Đóng khung dọc `1080x1920` với tiêu đề câu chuyện + huy hiệu `PHẦN X / Y` + thanh tiến trình động.
        4. Biến đổi âm sắc & tốc độ `~1.20x` đồng bộ hoàn toàn giữa hình và tiếng.
        """
        ep_num = int(part_spec["ep_num"])
        part_num = int(part_spec["part_num"])
        total_parts = int(part_spec["total_parts"])
        yt_id = str(part_spec["yt_id"])
        title = self.clean_story_title_no_source(str(part_spec["title"]))
        story_id = str(part_spec["story_id"])
        start_sec = float(part_spec["start_sec"])
        orig_dur = float(part_spec["orig_duration"])

        try:
            from .translator import SubtitleTranslator

            title = SubtitleTranslator._apply_commercial_safeguard(title)
        except Exception:
            pass

        seed_val = ep_num * 29 + part_num * 19
        full_mp4 = self.ensure_youtube_full_video(yt_id)

        overlay_png = self.cache_dir / f"ov_gtd_v1_s{ep_num}_p{part_num}of{total_parts}.png"
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
            f"{ts_val}_tiktok_story_{story_id}_gautrucden_v1_s{ep_num}_p{part_num}.mp4"
        )
        out_path = target_ready_dir / out_name
        tmp_render = self.cache_dir / f"rendering_gtd_v1_{story_id}.mp4"
        tmp_render.unlink(missing_ok=True)

        # Tốc độ đồng bộ tuyệt đối giữa Video & Audio (1.185x .. 1.215x) để giữ khớp phụ đề & miệng nhân vật
        speed_factor = round(1.185 + (seed_val % 16) * 0.002, 3)
        target_out_dur = max(30.0, round(orig_dur / speed_factor, 2))

        # Đổi nhẹ cao độ & âm sắc (0.955..0.970 hoặc 1.035..1.050) để phá AudioPrint nhưng giọng vẫn tự nhiên
        if seed_val % 2 == 0:
            pitch_factor = round(0.956 + (seed_val % 7) * 0.002, 4)
        else:
            pitch_factor = round(1.036 + (seed_val % 7) * 0.002, 4)
        atempo_comp = round(speed_factor / pitch_factor, 5)

        gop_val = 48 + (seed_val % 17)
        crf_val = 19 + (seed_val % 2)
        audio_br = "192k" if (seed_val % 2 == 0) else "160k"
        creation_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000000Z")

        # Cắt bỏ sạch 100% khung viền/logo Gấu Sợ Hãi TV (top 19.8%, left/right 4.8%, bottom 1.6%)
        # Dịch chuyển vi mô +-0.002 theo từng phần để đổi pHash/vHash mà không bao giờ lộ viền
        crop_x = round(0.048 + (seed_val % 3) * 0.001, 4)
        crop_y = round(0.198 + (seed_val % 3) * 0.001, 4)
        crop_w = round(1.0 - crop_x * 2.0, 4)
        crop_h = round(0.785 - (seed_val % 3) * 0.001, 4)

        visual_chain = (
            f"[0:v]setpts=PTS/{speed_factor},fps=30,trim=duration={target_out_dur:.2f},setpts=PTS-STARTPTS,"
            f"crop=iw*{crop_w}:ih*{crop_h}:iw*{crop_x}:ih*{crop_y},split=2[bg][fg];"
            "[bg]scale=240:426:force_original_aspect_ratio=increase,crop=240:426,hflip,"
            "boxblur=12:2,colorbalance=rs=-0.03:gs=-0.01:bs=0.07,"
            "eq=brightness=-0.24:saturation=0.86:contrast=1.08,vignette=PI/5,"
            "scale=1080:1920:flags=fast_bilinear[bg_out];"
            "[fg]scale=w=1044:h=680:force_original_aspect_ratio=decrease:flags=bicubic,"
            "eq=contrast=1.05:brightness=0.012:saturation=1.08:gamma=1.02,"
            "pad=w=iw+12:h=ih+12:x=6:y=6:color=0x00E5FF@0.85,"
            f"drawbox=x=6:y=ih-12:w='(iw-12)*(t/{target_out_dur:.2f})':h=6:color=0xFFD600@0.95:t=fill[fg_out];"
            "[bg_out][fg_out]overlay=(W-w)/2:(H-h)/2+28[v1];"
            "[v1][1:v]overlay=0:0[vout];"
        )

        audio_chain = (
            f"[0:a]aresample=44100,highpass=f=65,lowpass=f=13800,"
            f"asetrate=44100*{pitch_factor},aresample=44100,atempo={atempo_comp},"
            "equalizer=f=210:t=q:w=1.2:g=2.0,"
            "equalizer=f=1200:t=q:w=1.3:g=-1.5,"
            "equalizer=f=3800:t=q:w=1.2:g=2.2,"
            "extrastereo=m=1.12,"
            "acompressor=threshold=-18dB:ratio=2.2:attack=12:release=160:makeup=2.0dB,"
            "volume=1.15,alimiter=limit=0.95,aformat=sample_rates=44100:channel_layouts=stereo[aout]"
        )

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
            "encoder=CapCut 12.4.0 (iOS)",
            "-metadata",
            "comment=CapCut 12.4.0 (iOS)",
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
            f"[GauTrucDenSplitter] Đang cắt & xóa nguồn [{title} - Phần {part_num}/{total_parts}] "
            f"(speed={speed_factor}x, pitch={pitch_factor}x, crop={crop_w}x{crop_h})..."
        )
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)
        if res.returncode != 0 or not tmp_render.exists() or tmp_render.stat().st_size < 500_000:
            raise RuntimeError(
                f"Lỗi ffmpeg khi dựng Gấu Trúc Đen {title} Phần {part_num}: {res.stderr[-400:]}"
            )

        shutil.move(str(tmp_render), str(out_path))
        actual_out_dur = get_media_duration(out_path) or target_out_dur
        caption, hashtags = self.build_part_caption_and_hashtags(part_spec)

        logger.info(
            f"[GauTrucDenSplitter] Hoàn tất xuất bản cho [video_AI_hay]: {out_path.name} "
            f"(thời lượng={actual_out_dur:.1f}s, size={out_path.stat().st_size / 1e6:.1f}MB)"
        )
        return {
            "channel": "video_AI_hay",
            "platform": "tiktok",
            "video_id": story_id,
            "sound_id": f"gautrucden_yt_{yt_id}_p{part_num}",
            "source_url": part_spec["source_url"],
            "detected_language": "vi",
            "segment_count": 12,
            "raw_video": str(full_mp4),
            "extracted_audio": str(full_mp4),
            "final_video": str(out_path),
            "filename": f"video_AI_hay/ready/{out_name}",
            "caption": caption,
            "hashtags": hashtags,
            "status": "READY",
            "posted_at": "",
            "speech_ratio": 0.92,
            "spoken_seconds": round(actual_out_dur, 1),
            "ep_num": ep_num,
            "part_num": part_num,
            "total_parts": total_parts,
            "view_count": int(part_spec.get("view_count") or 0),
        }
