from datetime import datetime
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class TikTokCommunityGuidelinesGuard:
    """
    Lá Chắn Quy Tắc Cộng Đồng TikTok Toàn Diện (2025-2026) cho 10 kênh:
    Thực thi tự động 6 trụ cột Quy tắc Cộng đồng TikTok:
      1. Safety & Civility (An toàn & Văn minh)
      2. Mental & Behavioral Health (Sức khỏe Tâm thần & Hành vi)
      3. Sensitive & Mature Themes (Chủ đề Nhạy cảm & Người lớn)
      4. Youth Safety & Well-Being (An toàn Thanh thiếu niên)
      5. Integrity, Authenticity & Originality (Tính Nguyên bản, Chống Re-up & Câu dẫn tương tác)
      6. Regulated Goods & Commercial Compliance (Hàng hóa bị kiểm soát & Thương mại)
    """

    # Danh sách tập truyện bị khóa vĩnh viễn do nội dung gốc có mật độ từ nhạy cảm cao
    BLOCKED_BONGCUC_EPISODES: Set[int] = {145}
    BLOCKED_GAUSOHAI_EPISODES: Set[int] = {84, 89, 169, 178, 212, 220, 221, 240, 250, 272}

    # Các mẫu thay thế làm mềm tiêu đề & caption (dùng mã Unicode escape để an toàn tuyệt đối)
    _SENSITIVE_REPLACEMENTS: List[Tuple[str, str]] = [
        # Cụm từ nguồn / hậu tố thừa
        (r"(?i)\(\s*full(?:\s*version)?\s*\)", ""),
        (r"(?i)\bfull\s*hd\b", ""),
        (r"(?i)\bg\u1ea5u\s*kinh\s*d\u1ecb(?:\s*s\u1ee3\s*h\u00e3i(?:\s*tv)?)?(?:\s*h\u00e0i\s*h\u01b0\u1edbc)?\b", ""),
        (r"(?i)\b(?:phim|ho\u1ea1t\s*h\u00ecnh|truy\u1ec7n(?:\s*ma)?)\s*kinh\s*d\u1ecb(?:\s*d\u00e2n\s*gian)?(?:\s*hay|\s*n\u1eeda\s*\u0111\u00eam)?\b", "Chuy\u1ec7n K\u1ef3 B\u00ed"),
        (r"(?i)\bkinh\s*d\u1ecb\b", "k\u1ef3 b\u00ed"),
        (r"(?i)\bs\u1ee3\s*h\u00e3i(?:\s*tv)?\b", "ly k\u1ef3"),
        # Chuyển hóa từ ngữ nhạy cảm sang từ vựng văn học / trinh thám / tâm lý an toàn
        (r"(?i)\b\u00e1n\s*m\u1ea1ng\b", "B\u00ed \u1ea8n"),
        (r"(?i)\bcon\s*ma\b", "B\u00f3ng Ng\u01b0\u1eddi B\u00ed \u1ea8n"),
        (r"(?i)\bl\u1ec7\s*qu\u1ef7\b", "B\u00f3ng \u0110\u00eam"),
        (r"(?i)\bx\u00e1c\s*ch\u1ebft\b", "d\u1ea5u v\u1ebft"),
        (r"(?i)\bthi\s*th\u1ec3\b", "d\u1ea5u v\u1ebft"),
        (r"(?i)\bn\u1eef\s*thi\b", "ng\u01b0\u1eddi x\u01b0a"),
        (r"(?i)\bch\u1ebft\s*th\u1ea3m\b", "g\u1eb7p bi\u1ebfn c\u1ed1"),
        (r"(?i)\bng\u01b0\u1eddi\s*ch\u1edbt\b", "ng\u01b0\u1eddi x\u01b0a"),
        (r"(?i)\bquan\s*t\u00e0i\s*m\u00e1u\b", "Chi\u1ebfc R\u01b0\u01a1ng C\u1ed5"),
        (r"(?i)\bm\u00e1u\s*me\b", "h\u1ed7n lo\u1ea1n"),
        (r"(?i)\bd\u1ea5u\s*tay\s*m\u00e1u\b", "D\u1ea5u \u1ea4n B\u00ed \u1ea8n"),
        (r"(?i)\bm\u00e1u\s*ch\u00f3\b", "Chu Sa"),
        (r"(?i)\b\u0111\u00f2i\s*m\u1ea1ng\b", "t\u00ecm l\u1ea1i s\u1ef1 th\u1eadt"),
        (r"(?i)\bt\u1ef1\s*t\u1eed\b", "b\u1ebf t\u1eafc"),
        (r"(?i)\bt\u1ef1\s*s\u00e1t\b", "b\u1ebf t\u1eafc"),
        (r"(?i)\bgi\u1ebft\s*ng\u01b0\u1eddi\b", "g\u00e2y t\u1ed9i"),
        (r"(?i)\bl\u1ed9t\s*da\b", "bi\u1ebfn d\u1ea1ng"),
        (r"(?i)\b\u0103n\s*th\u1ecbt\b", "truy \u0111u\u1ed5i"),
        # Chống câu dẫn tương tác giả tạo (Fake Engagement)
        (r"(?i)\b(?:b\u1ea5m|nh\u1ea5n)\s*(?:like|tim|follow|theo\s*d\u00f5i)\s*\u0111\u1ec3\b", "c\u00f9ng theo d\u00f5i \u0111\u1ec3"),
        (r"(?i)\bcomment\s*ch\u1ea5m\s*h\u00f3ng\b", "\u0111\u1ec3 l\u1ea1i c\u1ea3m nh\u1eadn"),
    ]

    _FORBIDDEN_HASHTAG_PATTERNS: Tuple[str, ...] = (
        "conma",
        "anmang",
        "kinhdi",
        "maquy",
        "xacchet",
        "tutu",
        "tusat",
        "maume",
        "bongcuc",
        "gausohai",
        "gsh",
    )

    @classmethod
    def is_episode_blocked(cls, channel_folder: str, ep_num: int) -> bool:
        if channel_folder == "audio_dem_khuyen":
            return int(ep_num) in cls.BLOCKED_BONGCUC_EPISODES
        if channel_folder == "video_AI_hay":
            return int(ep_num) in cls.BLOCKED_GAUSOHAI_EPISODES
        return False

    @classmethod
    def is_video_or_story_blocked(cls, name_or_sid: str, channel_folder: str = "") -> bool:
        s = str(name_or_sid or "").lower()
        for ep in cls.BLOCKED_BONGCUC_EPISODES:
            if f"9900{ep:03d}" in s or f"_s{ep}_" in s:
                return True
        for ep in cls.BLOCKED_GAUSOHAI_EPISODES:
            if f"8800{ep:03d}" in s or (channel_folder == "video_AI_hay" and f"_s{ep}_" in s):
                return True
        return False

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        if not text:
            return ""
        out = str(text).strip()
        for pat, rep in cls._SENSITIVE_REPLACEMENTS:
            out = re.sub(pat, rep, out)
        out = re.sub(r"\s+\-\s*$", "", out)
        out = re.sub(r"^\s*\-\s+", "", out)
        out = re.sub(r"\s{2,}", " ", out).strip(" -—|:.")
        return out or "Chuyện Đêm Khuya Đặc Sắc"

    _DAYTIME_HASHTAG_MAP: Dict[str, str] = {
        "audiodemkhuya": "#truyenaudio",
        "kechuyendemkhuya": "#kechuyen",
        "nghetruyendemkhuya": "#nghetruyen",
        "truyendemkhuya": "#truyenngan",
        "chuyendemkhuya": "#chuyenke",
        "gocnhinkhuya": "#gocnhincuocsong",
        "mukbangdemkhuya": "#mukbang",
        "chuyendemsau": "#chuyengiadinh",
        "tamtridemkhuya": "#tamlyhonnhan",
        "demkhuya": "#truyenaudio",
    }

    @classmethod
    def is_daytime_now(cls, hour: Optional[int] = None) -> bool:
        """Kiểm tra khung giờ ban ngày (05:00 đến 17:59)."""
        h = datetime.now().hour if hour is None else int(hour)
        return 5 <= h < 18

    @classmethod
    def sanitize_caption_and_hashtags(
        cls,
        caption: str,
        hashtags: str,
        channel_folder: str = "",
        hour: Optional[int] = None,
    ) -> Tuple[str, str]:
        safe_cap = cls.sanitize_text(caption)
        is_daytime = cls.is_daytime_now(hour)
        if channel_folder == "audio_dem_khuyen" and is_daytime:
            safe_cap = re.sub(r"(?i)\bchuyện\s+đêm\s+khuya\b", "Chuyện tâm lý", safe_cap)
            safe_cap = re.sub(r"(?i)\bđêm\s+khuya\b", "mỗi ngày", safe_cap)
            safe_cap = safe_cap.replace("🌙", "🎧").replace("🌌", "✨").replace("🕯️", "📖")

        raw_tags = [t.strip() for t in str(hashtags or "").split() if t.strip()]
        safe_tags: List[str] = []
        for tag in raw_tags:
            low_t = tag.lower().lstrip("#")
            if any(bad in low_t for bad in cls._FORBIDDEN_HASHTAG_PATTERNS):
                continue
            if channel_folder == "audio_dem_khuyen" and is_daytime:
                if low_t in cls._DAYTIME_HASHTAG_MAP:
                    tag = cls._DAYTIME_HASHTAG_MAP[low_t]
                    low_t = tag.lower().lstrip("#")
                elif any(nw in low_t for nw in ("demkhuya", "dem_khuya", "chuyendem", "demsau", "khuya", "bandem", "ban_dem")):
                    continue
            if tag not in safe_tags:
                safe_tags.append(tag)
        if not safe_tags:
            if channel_folder == "audio_dem_khuyen":
                if is_daytime:
                    safe_tags = ["#truyenaudio", "#kechuyen", "#truyenngan", "#chuyengiadinh", "#xuhuong", "#fyp"]
                else:
                    safe_tags = ["#audiodemkhuya", "#kechuyendemkhuya", "#truyenaudio", "#xuhuong", "#fyp"]
            elif channel_folder == "video_AI_hay":
                safe_tags = ["#gautrucden", "#hoathinhgautrucden", "#chuyenke", "#xuhuong", "#fyp"]
            else:
                safe_tags = ["#xuhuong", "#fyp"]
        return safe_cap, " ".join(safe_tags)
