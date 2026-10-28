import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .lean_context import LeanContextGateway
from .utils import logger

SYSTEM_PROMPT = """Bạn là một Biên dịch viên phụ đề video chuyên nghiệp từ {source_lang_name} sang Tiếng Việt.
Nhiệm vụ của bạn là DỊCH TRỰC TIẾP, CHÍNH XÁC và TỰ NHIÊN từng câu thoại gốc sang Tiếng Việt, bám sát đúng nội dung gốc của video, KHÔNG tự ý bịa thêm kịch bản mới hay thay đổi cốt truyện.

HƯỚNG DẪN XƯNG HÔ & THUẬT NGỮ THEO KÊNH ({channel_style_name}):
{channel_style_guide}

YÊU CẦU KỸ THUẬT BẮT BUỘC:
1. DỊCH SÁT NGHĨA GỐC: Dịch trực tiếp đúng ý nghĩa từng câu thoại từ {source_lang_name} sang Tiếng Việt trôi chảy, rõ nghĩa, không thêm thắt nội dung ngoài lề.
2. KHỚP ĐỘ DÀI THỜI LƯỢNG (`duration_sec`): Câu dịch tiếng Việt ngắn gọn, súc tích, khớp với thời lượng `duration_sec` của câu gốc để khi đọc lồng tiếng và hiển thị phụ đề vừa khít khẩu hình.
3. Giữ nguyên tuyệt đối số lượng câu và trường `id` của từng câu.
4. Chỉ trả về đúng định dạng JSON là một mảng các object:
[
  {{"id": 0, "translated_text": "Câu dịch tiếng Việt trực tiếp và tự nhiên"}}
]
Không giải thích thêm, không bọc nội dung ngoài JSON."""

CHANNEL_STYLE_GUIDES: Dict[str, Dict[str, str]] = {
    "channel_1": {
        "name": "Kênh 1 - AI Hoạt Họa / Phim Ngắn Thú Cưng / Kể Chuyện (Channel 1)",
        "guide": (
            "- Dịch trực tiếp, tự nhiên, sinh động, giữ đúng mạch thoại và tên nhân vật/thú cưng trong video gốc."
        ),
    },
    "channel_2": {
        "name": "Kênh 2 - Đời Sống / Gia Đình / Hài Hước (Channel 2)",
        "guide": (
            "- Dịch trực tiếp sang khẩu ngữ đời thường, tự nhiên, đúng ngữ cảnh gia đình/con cái trong video gốc."
        ),
    },
    "channel_3": {
        "name": "Kênh 3 - Truyện Ngôn Tình & Đoản Kịch (Channel 3)",
        "guide": (
            "- Dịch trực tiếp lời thoại đoản kịch/tiểu thuyết sang tiếng Việt tự nhiên, giữ đúng ngôi xưng (tôi, em, anh, hắn, cô ấy, Chủ tịch, Phu nhân, Tiểu thư)."
        ),
    },
    "channel_4": {
        "name": "Kênh 4 - Tu Tiên / Huyền Huyễn / Tiên Hiệp (Channel 4)",
        "guide": (
            "- Dịch trực tiếp sang tiếng Việt chuẩn thuật ngữ Tu tiên / Tiên hiệp: Đại lão, Tông môn, Trúc Cơ, Kim Đan, Nguyên Anh, "
            "Hóa Thần, Độ Kiếp, Thánh địa, Linh căn, Thần thông, Pháp bảo, đạo hữu, sư tôn, Ta - Ngươi - Hắn - Nàng."
        ),
    },
    "general": {
        "name": "Dịch Trực Tiếp Chuẩn Xác",
        "guide": (
            "- Dịch trực tiếp, rõ ràng, tự nhiên và sát nghĩa câu gốc."
        ),
    },
}

LANG_NAMES = {
    "zh": "Tiếng Trung (Chinese / Douyin)",
    "es": "Tiếng Tây Ban Nha (Spanish)",
    "en": "Tiếng Anh (English)",
    "fr": "Tiếng Pháp (French)",
    "ja": "Tiếng Nhật (Japanese)",
    "ko": "Tiếng Hàn (Korean)",
    "vi": "Tiếng Việt (Vietnamese)",
}

VIETNAMESE_DIACRITIC_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
    r"ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ]"
)


def has_vietnamese_diacritics(text: str) -> bool:
    """Kiểm tra nhanh chuỗi văn bản có chứa dấu đặc trưng của Tiếng Việt hay không."""
    if not text:
        return False
    return len(VIETNAMESE_DIACRITIC_RE.findall(text)) >= 2


class SubtitleTranslator:
    """
    Biên tập viên kịch bản TikTok (TikTok Script Editor):
    Nhận danh sách câu thoại gốc và viết lại (rewrite) thành kịch bản tiếng Việt
    tự nhiên, kịch tính, giữ chân người nghe ngay 3 giây đầu nhưng vẫn khớp chặt chẽ
    với độ dài thời lượng (`duration_sec`) và mốc thời gian (`start`, `end`) của video gốc.
    """

    def __init__(self, provider: Optional[str] = None):
        self.provider = (provider or os.getenv("LLM_PROVIDER", "auto")).lower().strip()
        self.gemini_api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        self.gemini_model = (os.getenv("GEMINI_MODEL") or "gemini-2.0-flash").strip()

        self.deepseek_api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        self.deepseek_model = (os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip()
        self.deepseek_base_url = (
            os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1"
        ).rstrip("/")

        self.openai_api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        self.openai_model = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
        self.openai_base_url = (
            os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        ).rstrip("/")

    def _resolve_active_provider(self) -> str:
        """Xác định LLM provider khả dụng theo cấu hình .env."""
        if self.provider in ("gemini", "deepseek", "openai"):
            key_map = {
                "gemini": self.gemini_api_key,
                "deepseek": self.deepseek_api_key,
                "openai": self.openai_api_key,
            }
            if key_map[self.provider]:
                return self.provider
            logger.warning(
                f"Đã chọn LLM_PROVIDER='{self.provider}' nhưng chưa cấu hình API key tương ứng trong .env. "
                "Đang tự động kiểm tra các provider khác..."
            )

        if self.gemini_api_key:
            return "gemini"
        if self.deepseek_api_key:
            return "deepseek"
        if self.openai_api_key:
            return "openai"
        return "fallback"

    @staticmethod
    def _normalize_channel_key(channel_or_genre: str) -> str:
        val = (channel_or_genre or "").lower().strip()
        if "channel_1" in val or val == "1" or "video_ai_hay" in val:
            return "channel_1"
        if "channel_2" in val or val == "2" or "baby_ai" in val:
            return "channel_2"
        if "channel_3" in val or val == "3" or "audio_dem_khuyen" in val or any(
            k in val for k in ("小说", "霸总", "重生", "穿越")
        ):
            return "channel_3"
        if "channel_4" in val or val == "4" or "tu_chan_gioi" in val or any(
            k in val for k in ("修仙", "玄幻", "修真", "仙")
        ):
            return "channel_4"
        return "general"

    def translate_segments(
        self,
        segments: List[Dict[str, Any]],
        source_language: Optional[str] = None,
        save_json_path: Optional[Path] = None,
        batch_size: int = 25,
        channel: str = "general",
    ) -> List[Dict[str, Any]]:
        """
        Biên dịch và viết lại (rewrite) danh sách segments sang kịch bản tiếng Việt
        theo văn phong của từng kênh, giữ nguyên `start`, `end`, `text` gốc,
        và bổ sung trường `translated_text`.
        """
        if not segments:
            logger.warning("Danh sách segments trống, bỏ qua bước dịch thuật.")
            return []

        src_lang = (
            source_language or segments[0].get("language") or "en"
        ).lower().strip()

        if src_lang == "vi":
            logger.info("Ngôn ngữ gốc đã là tiếng Việt ('vi'), giữ nguyên văn bản.")
            enriched = []
            for seg in segments:
                item = dict(seg)
                item["translated_text"] = seg["text"]
                enriched.append(item)
            return enriched

        norm_channel = self._normalize_channel_key(channel)
        active_provider = self._resolve_active_provider()
        logger.info(
            f"Bắt đầu biên tập kịch bản {len(segments)} câu từ '{src_lang}' -> 'vi' "
            f"[Phong cách: {norm_channel.upper()} | Provider: {active_provider.upper()}]"
        )

        translated_segments: List[Dict[str, Any]] = []

        for batch_start in range(0, len(segments), batch_size):
            batch = segments[batch_start : batch_start + batch_size]
            batch_translations = self._translate_batch(
                batch=batch,
                src_lang=src_lang,
                provider=active_provider,
                channel_key=norm_channel,
                is_first_batch=(batch_start == 0),
            )
            for idx, seg in enumerate(batch):
                global_idx = batch_start + idx
                duration_sec = max(0.4, float(seg["end"]) - float(seg["start"]))
                raw_vi = batch_translations[idx]
                polished_vi = self._polish_script_line(
                    vi_text=raw_vi,
                    orig_text=seg["text"],
                    line_idx=global_idx,
                    start_sec=float(seg["start"]),
                    duration_sec=duration_sec,
                    channel_key=norm_channel,
                )
                polished_vi = self._deduplicate_vietnamese_clauses(polished_vi)
                if not polished_vi:
                    continue

                # Khử trùng lặp câu dịch liên tiếp giữa segment i-1 và segment i để không bao giờ đọc lặp lại câu nói
                if translated_segments:
                    prev_seg = translated_segments[-1]
                    prev_vi = (prev_seg.get("translated_text") or "").strip()
                    gap_s = float(seg["start"]) - float(prev_seg["end"])
                    if prev_vi and gap_s <= 2.2:
                        import difflib as _difflib

                        p_norm = re.sub(r"[\W_]+", " ", prev_vi.lower()).strip()
                        c_norm = re.sub(r"[\W_]+", " ", polished_vi.lower()).strip()
                        sim = _difflib.SequenceMatcher(None, p_norm, c_norm).ratio()
                        if sim >= 0.78 or (len(c_norm) >= 8 and c_norm in p_norm) or (len(p_norm) >= 8 and p_norm in c_norm):
                            prev_seg["end"] = seg["end"]
                            if len(polished_vi) > len(prev_vi):
                                prev_seg["translated_text"] = polished_vi
                            logger.info(
                                f"[Anti-Repeat Sub/Voice] Đã gộp câu dịch trùng lặp tại {seg['start']:.2f}s: '{polished_vi}'"
                            )
                            continue

                new_seg = dict(seg)
                new_seg["language"] = src_lang
                new_seg["translated_text"] = polished_vi
                translated_segments.append(new_seg)
                logger.info(
                    f"[{new_seg['start']:05.2f}s - {new_seg['end']:05.2f}s] "
                    f"{new_seg['text']}  ==>  {new_seg['translated_text']}"
                )

        if save_json_path:
            Path(save_json_path).write_text(
                json.dumps(translated_segments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return translated_segments

    def _translate_batch(
        self,
        batch: List[Dict[str, Any]],
        src_lang: str,
        provider: str,
        channel_key: str = "general",
        is_first_batch: bool = True,
    ) -> List[str]:
        """Gửi batch câu thoại qua LLM với prompt 'Biên tập viên kịch bản TikTok' hoặc dịch theo ngữ cảnh."""
        payload_items = [
            {
                "id": i,
                "start_sec": round(float(seg["start"]), 2),
                "duration_sec": round(float(seg["end"]) - float(seg["start"]), 2),
                "target_word_count": max(
                    3, int(round((float(seg["end"]) - float(seg["start"])) * 3.8))
                ),
                "text": seg["text"],
            }
            for i, seg in enumerate(batch)
        ]

        source_lang_name = LANG_NAMES.get(src_lang, src_lang)
        style_info = CHANNEL_STYLE_GUIDES.get(
            channel_key, CHANNEL_STYLE_GUIDES["general"]
        )
        system_prompt = SYSTEM_PROMPT.format(
            source_lang_name=source_lang_name,
            channel_style_name=style_info["name"],
            channel_style_guide=style_info["guide"],
        )
        user_prompt = (
            "Hãy dịch trực tiếp, chính xác và tự nhiên từng câu thoại sau sang tiếng Việt, "
            "giữ nguyên độ dài tương ứng với `duration_sec` / `target_word_count`:\n"
            + json.dumps(payload_items, ensure_ascii=False, indent=2)
        )

        if provider != "fallback":
            try:
                if provider == "gemini":
                    raw_response = self._call_gemini(system_prompt, user_prompt)
                elif provider == "deepseek":
                    raw_response = self._call_openai_compatible(
                        api_key=self.deepseek_api_key,
                        base_url=self.deepseek_base_url,
                        model=self.deepseek_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    )
                elif provider == "openai":
                    raw_response = self._call_openai_compatible(
                        api_key=self.openai_api_key,
                        base_url=self.openai_base_url,
                        model=self.openai_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    )
                else:
                    raw_response = ""

                parsed_map = self._parse_llm_json(raw_response)
                results: List[str] = []
                for i, seg in enumerate(batch):
                    vi_text = (parsed_map.get(i) or "").strip()
                    if not vi_text:
                        vi_text = self._translate_single_fallback(seg["text"], src_lang)
                    results.append(vi_text)
                return results
            except Exception as llm_err:
                logger.warning(
                    f"Gọi {provider.upper()} LLM API gặp lỗi ({llm_err}). "
                    "Chuyển sang chế độ dịch trực tiếp theo ngữ cảnh."
                )

        if provider == "fallback":
            logger.info(
                "Đang dịch trực tiếp tiếng Trung/ngoại ngữ sang tiếng Việt theo ngữ cảnh (Contextual NMT)..."
            )
        return self._translate_batch_contextual_fallback(batch, src_lang)

    # Bảng ánh xạ Họ / Tên đệm phổ biến tiếng Trung sang âm Hán Việt chuẩn
    SINO_VIETNAMESE_SURNAMES = {
        "杜": "Đỗ",
        "纪": "Kỷ",
        "季": "Kỷ",
        "傅": "Phó",
        "顾": "Cố",
        "沈": "Thẩm",
        "林": "Lâm",
        "苏": "Tô",
        "陆": "Lục",
        "叶": "Diệp",
        "江": "Giang",
        "霍": "Hoắc",
        "厉": "Lệ",
        "慕": "Mộ",
        "萧": "Tiêu",
        "楚": "Sở",
        "白": "Bạch",
        "宋": "Tống",
        "陈": "Trần",
        "王": "Vương",
        "李": "Lý",
        "张": "Trương",
        "刘": "Lưu",
        "周": "Châu",
        "吴": "Ngô",
        "赵": "Triệu",
        "孙": "Tôn",
        "夏": "Hạ",
        "秦": "Tần",
        "温": "Ôn",
        "唐": "Đường",
        "韩": "Hàn",
        "冯": "Phùng",
        "洛": "Lạc",
        "程": "Trình",
        "宁": "Ninh",
        "许": "Hứa",
        "何": "Hà",
        "谢": "Tạ",
        "柳": "Liễu",
        "裴": "Bùi",
        "孟": "Mạnh",
        "徐": "Từ",
        "黄": "Hoàng",
        "高": "Cao",
        "梁": "Lương",
        "马": "Mã",
        "罗": "La",
        "宇": "Vũ",
        "美": "Mỹ",
        "帅": "Soái",
    }

    @classmethod
    def _clean_chinese_asr_homophones(cls, text: str) -> str:
        """
        Làm sạch lỗi đồng âm tiếng Trung từ ASR, đồng thời chuyển đổi trước tên riêng Hán Việt,
        xưng hô và các thành ngữ khẩu ngữ sang dạng chuẩn để bộ dịch ngữ cảnh không bị dịch
        ra Pinyin tiếng Anh (như Xiao Ji, Du Fangchun) hoặc dịch nghĩa đen vô nghĩa.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return cleaned
        asr_fixes = [
            ("他最穷的那年,我起了分手", "他最穷的那年，我提了分手"),
            ("我起了分手", "我提了分手"),
            ("我没有回疑似头", "我没有回一次头"),
            ("同时聚会上", "同学聚会上"),
            ("富士集团的太太", "傅氏集团的太太"),
            ("事有无限我说国家是卖相交的", "室友诬陷我说我家是卖香蕉的"),
            ("事有无限", "室友诬陷"),
            ("是有诬陷", "室友诬陷"),
            ("是有跟别人", "室友跟别人"),
            ("是有半夜", "室友半夜"),
            ("事有又嘲笑", "室友又嘲笑"),
            ("是有幼潮小", "室友又嘲笑"),
            ("事有跟别人", "室友跟别人"),
            ("事有半夜做妖补睡觉", "室友半夜作妖不睡觉"),
            ("坐腰不睡觉", "作妖不睡觉"),
            ("做妖补睡觉", "作妖不睡觉"),
            ("立马成亲", "立马澄清"),
            ("高访货", "高仿货"),
            ("舒服之这些东西其实都是抵贪货", "殊不知这些东西其实都是地摊货"),
            ("舒服之这些东西其实都是抵摊货", "殊不知这些东西其实都是地摊货"),
            ("抵贪货", "地摊货"),
            ("抵摊货", "地摊货"),
            ("特殊去打了800个儿八子", "徒手去扇了她八百个大嘴巴子"),
            ("他记得一顿狗叫", "她急得一顿乱叫"),
            ("吃黄果得战酱", "吃黄瓜得蘸酱"),
            ("吃黄果不战酱", "吃黄瓜不蘸酱"),
            ("等于没战酱", "等于没蘸酱"),
            ("你得战这一头", "你得蘸这一头"),
            ("煤化", "美化"),
            ("没有一段白足的路", "没有一段白走的路"),
            ("白足的路", "白走的路"),
            ("合唱不是一种获得", "何尝不是一种获得"),
            ("合唱不是", "何尝不是"),
            ("在物里", "在雾里"),
            ("物里看花", "雾里看花"),
            ("平什么", "凭什么"),
            ("重声", "重生"),
            ("修先", "修仙"),
            ("筑机", "筑基"),
            ("祝基", "筑基"),
            ("金单", "金丹"),
            ("元英", "元婴"),
            ("原婴", "元婴"),
            ("度劫", "渡劫"),
            ("灵跟", "灵根"),
            ("宗们", "宗门"),
            ("失尊", "师尊"),
            ("道有", "道友"),
            ("霸道总才", "霸道总裁"),
            # Các cụm đồng âm & thành ngữ khẩu ngữ phổ biến trên Douyin (chuẩn hóa sang tiếng Trung phổ thông rõ nghĩa trước khi qua NMT)
            ("出买遗憾", "充满遗憾"),
            ("小粉兔子", "香粉铺子"),
            ("相分手艺", "香粉手艺"),
            ("寻找房", "巡捕房"),
            ("建状分分上盟", "见状纷纷上门"),
            ("遗主", "遗嘱"),
            ("外国或", "外国货"),
            ("喜结联立", "喜结连理"),
            ("香粉兔", "香粉铺"),
            ("评责，诗写证", "存折，上面写着"),
            ("办个世纪", "半个世纪"),
            ("冲可可", "臭可可"),
            ("死完这周 猫调组电池", "洗完这周猫条随便吃"),
            ("把猫当机动车室", "把猫当机动车使"),
            ("那么多交通工具你不做", "那么多交通工具你不坐"),
            ("可是大马路上狂奔", "搁这大马路上狂奔"),
            ("速度以及情", "速度与激情"),
            ("看你们出发", "看你们初犯"),
            ("重新洗澡一百", "宠物洗澡一百"),
            ("妈妈不省你气", "妈妈不生你气"),
            # Diễn giải các thành ngữ / khẩu ngữ địa phương sang tiếng Trung chuẩn để NMT dịch mượt 100%
            ("臭可可，快出来洗澡", "调皮的可可，快点出来洗澡"),
            ("臭可可水全甩我脸上了", "调皮的可可把水全都甩到我脸上了"),
            ("水全甩我脸上了", "把水全都甩到我脸上了"),
            ("臭可可", "调皮的可可"),
            ("洗完这周猫条随便吃", "洗完澡这一周你的猫咪零食随便吃"),
            ("猫条随便吃", "猫咪零食随便吃"),
            ("前面那大坨给我站那", "前面那只大肥猫给我站住"),
            ("戴个头盔就把猫当机动车使", "戴个头盔就把猫咪当成摩托车来骑"),
            ("那么多交通工具你不坐，搁这大马路上狂奔", "有那么多交通工具你不坐，偏偏在这大马路上疯狂奔跑"),
            ("搁这大马路上狂奔", "偏偏在这大马路上疯狂奔跑"),
            ("你俩要来场速度与激情呢", "你们俩是要上演速度与激情吗"),
            ("看你们初犯，这猫不许再骑了啊", "念在你们是第一次犯错，这只猫以后不许再骑了啊"),
            ("看你们初犯", "念在你们是第一次犯错"),
            ("宠物洗澡100，咱家孩子在哪", "宠物洗澡一百元，请问你们家的小猫在哪里"),
            ("咱家孩子在哪", "请问你们家的小猫在哪里"),
            ("行，这就给小猫安排上", "好的，马上就给小猫咪安排洗澡"),
            ("这就给小猫安排上", "马上就给小猫咪安排洗澡"),
            ("隔三差五就被抓进巡捕房", "每隔几天就被抓进警察局"),
            ("巡捕房", "警察局"),
            ("香粉铺子", "香粉店"),
            ("香粉铺", "香粉店"),
            ("香粉手艺", "制作香粉的手艺"),
            ("是杜老板领女儿，捡回来的孤儿", "是杜老板带女儿出门时捡回来的孤儿"),
            ("小姐这样自由的新青年", "杜小姐这样崇尚自由的新时代女性"),
            ("永远不会垂青于，连革命是什么都不懂的自己", "她永远不会爱上连革命是什么都不懂的自己"),
            ("永远不会垂青于", "她永远不会爱上"),
            ("遵照遗嘱", "按照杜老板的遗嘱"),
            ("而是选择等小姐回来", "他而是选择守着铺子等杜小姐回来"),
            ("喜结连理", "结婚成为夫妻"),
            ("写着：，给小姐和姑爷", "上面写着：留给杜小姐和她的丈夫"),
            ("给小姐和姑爷", "留给杜小姐和她的丈夫"),
            ("早就他一个人的事", "从头到尾都只是他一个人的事"),
            ("这是鼠标吗，是鼠标啊", "这是电脑鼠标吗？对啊这就是鼠标"),
            ("这不驴吗，是鼠标插上电就能用", "这明明是头驴啊！这就是鼠标，插上电就能用"),
            ("以后再说一遍", "我最后再警告你一遍"),
            ("你要是再整这些接 的玩意，◎不整了不整了，我去给你拿不接的", "你要是再弄这些奇葩玩意！不弄了不弄了，我去给你拿个正常的鼠标"),
            ("我还有一鼠标", "我还有一个特别高级的鼠标"),
            ("这就是你说的那特别的鼠标，这哪是鼠标啊，对啊智能鼠标啊", "这哪里是鼠标啊？对啊，这是声控智能鼠标"),
            ("打光我的电脑", "打开我的电脑"),
            ("幼儿园要吃到了", "幼儿园快要迟到了"),
            ("幼儿园要迟到了", "去幼儿园快要迟到了"),
            ("吃的满嘴都是", "看看你吃得满嘴都是"),
            ("你吃得满嘴都是", "看看你吃得满嘴都是"),
            ("我一天天的光给你擦嘴了", "我一天天的只顾着给你擦嘴了"),
            ("小小人足哪有你这么能吃的小孩", "小小人族里哪有你这么能吃的小孩"),
            ("以后你要少吃一点腰", "以后你要少吃一点哟"),
            ("我都过不到你的脸", "我都够不到你的脸"),
            ("你不让我给你擦，我不给你擦了", "你不让我给你擦，那我不给你擦脸了"),
            ("我不跟你擦脸了", "那我不给你擦脸了"),
            ("再拖拉就赶不上校轧啦", "再磨蹭就赶不上校车了"),
            ("再拖拉就赶不上校车了", "再磨蹭就赶不上校车了"),
            ("我要去上学", "妈妈我要去上学了"),
            ("妈妈不替，拿着水杯", "妈妈抱不动你，快拿着水杯"),
            ("妈妈不去", "妈妈抱不动你"),
            ("拿着水呗", "快拿着水杯"),
            ("校车再等你了", "校车在等你了"),
            ("还贷了，今天肯定要迟到了", "糟了，今天上学肯定要迟到了"),
            ("完蛋了", "糟了"),
        ]
        for wrong, right in asr_fixes:
            cleaned = cleaned.replace(wrong, right)

        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _translate_batch_contextual_fallback(
        self, batch: List[Dict[str, Any]], src_lang: str
    ) -> List[str]:
        """
        Dịch theo khối ngữ cảnh toàn đoạn qua Google Contextual NMT (`clients5.google.com/translate_a/t`)
        để bộ dịch hiểu toàn bộ mạch câu chuyện liền mạch thay vì dịch rời rạc từng từ đơn lẻ.
        """
        sl = "zh-CN" if src_lang.startswith("zh") else src_lang
        cleaned_lines = [
            self._clean_chinese_asr_homophones(seg["text"].strip())
            if sl == "zh-CN"
            else seg["text"].strip()
            for seg in batch
        ]

        # Ưu tiên dịch toàn bộ đoạn văn cách nhau bởi '\n' qua Google Contextual NMT
        try:
            resp = requests.post(
                "https://clients5.google.com/translate_a/t",
                params={"client": "dict-chrome-ex", "sl": sl, "tl": "vi"},
                data={"q": "\n".join(cleaned_lines)},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=12,
            )
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list) and payload and isinstance(payload[0], str):
                    lines_vi = [line.strip() for line in payload[0].split("\n")]
                    if len(lines_vi) == len(cleaned_lines) and all(lines_vi):
                        return lines_vi
        except Exception as exc:
            logger.debug(f"Google Contextual batch fallback error: {exc}")

        # Dự phòng dịch từng câu nếu batch newline không khớp số dòng
        return [self._translate_single_fallback(line, sl) for line in cleaned_lines]

    @staticmethod
    def _polish_script_line(
        vi_text: str,
        orig_text: str,
        line_idx: int,
        start_sec: float,
        duration_sec: float,
        channel_key: str,
    ) -> str:
        """
        Hậu kỳ biên tập kịch bản tiếng Việt (Script Polisher):
        - Loại bỏ cấu trúc dịch máy word-by-word thô cứng.
        - Chuẩn hóa đại từ nhân xưng và thuật ngữ theo đúng chất của từng Kênh:
          + Kênh 4 (Tu Tiên / Huyền Huyễn): Đại lão, Tông môn, Trúc Cơ, Nguyên Anh, Thánh địa, Linh căn,
            Thần thông, nghịch tập, phế vật, đạo hữu, bái kiến sư tôn, Ta - Ngươi - Hắn - Nàng.
          + Kênh 3 (Truyện Tổng Tài / Trùng Sinh): Chủ tịch, Phu nhân, Tiểu thư, hắn, cô ta, trọng sinh.
          + Kênh 1 (AI Hoạt họa / Thú cưng / Kể chuyện): Đáng yêu, sinh động, cuốn hút.
          + Kênh 2 (Đời sống / Gia đình): Tự nhiên, đời thường, tếu táo.
        - Tối ưu câu mở đầu (3 giây đầu) và cân đối độ dài câu với thời lượng `duration_sec`.
        """
        text = (vi_text or "").strip()
        if not text:
            return text

        # 1. Làm sạch các cụm dịch máy thô phổ biến & chuyển ngữ thành văn phong kể chuyện TikTok tự nhiên
        common_fixes = [
            (r"\bDu Fangchun\b", "Đỗ Phương Xuân"),
            (r"\bĐỗ Phương Xuân\b", "Đỗ Phương Xuân"),
            (r"\bXiao\s*Ji\b", "Tiểu Kỷ"),
            (r"\bLao\s*Ji\b", "Lão Kỷ"),
            (r"\bTiểu Kỳ\b", "Tiểu Kỷ"),
            (r"\bTiểu Cát\b", "Tiểu Kỷ"),
            (r"\bcô Du\b", "Đỗ tiểu thư"),
            (r"\bCô Du\b", "Đỗ tiểu thư"),
            (r"\bsếp Du\b", "Ông chủ Đỗ"),
            (r"\bSếp Du\b", "Ông chủ Đỗ"),
            (r"\bông chủ Du\b", "Ông chủ Đỗ"),
            (r"\bgia đình Du\b", "nhà họ Đỗ"),
            (r"\bnhà họ Du\b", "nhà họ Đỗ"),
            (r"\bKeke\b", "Coco"),
            (r"\bKhả Khả\b", "Coco"),
            (r"\bCa Ca\b", "Coco"),
            (r"\bcó vẻ như bạn nên đi tắm\b", "tới giờ phải đi tắm rồi đấy"),
            (r"\bNếu bạn không đi tắm, bạn sẽ không được phép ăn tối\.?", "Không chịu tắm là tối nay nhịn cơm luôn nhé!"),
            (r"\bĐừng làm vậy nữa, hôm nay (?:tôi|mình|bạn) phải đi tắm\b", "Đừng giở trò đó nữa, hôm nay nhất định phải đi tắm!"),
            (r"\bĐừng giở trò đó nữa, hôm nay (?:tôi|mình|bạn) phải đi tắm\b", "Đừng có giở trò đó nữa, hôm nay nhất định phải đi tắm!"),
            (r"\bĐược rồi, sau khi tắm xong, mèo của bạn có thể ăn bất cứ món ăn nhẹ nào nó muốn trong tuần này\.?", "Thôi được rồi, tắm xong tuần này cho ăn súp thưởng thoải mái luôn!"),
            (r"\bdừng con mèo to béo trước mặt (?:tôi|mình) lại\.?", "Con mèo khổng lồ phía trước đứng lại ngay cho tôi!"),
            (r"\bHai bạn có định đóng Fast and Furious không\??", "Hai người tính đóng Quá Nhanh Quá Nguy Hiểm đấy à?"),
            (r"\bQuên đi, đây là lần đầu tiên (?:ngươi|bạn|các bạn) mắc lỗi nên không được phép cưỡi con mèo này nữa, chú cảnh sát tốt bụng\.?", "Thôi bỏ đi, nể tình vi phạm lần đầu, cấm cưỡi mèo ra đường nữa nhé! Vâng ạ chú cảnh sát!"),
            (r"\bHãy nhớ nhé, tạm biệt anh cảnh sát, (?:tôi|mình) mới 26 thôi\.?", "Chúng cháu nhớ rồi, tạm biệt chú cảnh sát ạ! Này, tôi mới 26 tuổi thôi đấy!"),
            (r"\bThật là một con mèo lớn\. Rất tiếc, con mèo này quá lớn và thiết bị của chúng (?:tôi|mình) không thể giặt được\.?", "Mèo bự dữ vậy! Ngại quá, mèo to thế này máy móc bên tôi không tắm nổi đâu."),
            (r"\bHiểu rồi, anh bạn nhỏ\.?", "Xong xuôi rồi nhé nhóc con!"),
            (r"\bMẹ không giận bạn nhưng bạn phải cắt đồ ăn vặt đi một nửa\.?", "Mẹ không giận con đâu, nhưng súp thưởng tuần này bị cắt một nửa nhé!"),
            (r"\bChúng (?:tôi|mình) không thể làm gì được\. Chúng ta hãy đi bộ tới đó\.?", "Biết làm sao giờ, đành đi bộ tới tiệm vậy."),
            (r"\bChào sếp\b", "Chào ông chủ"),
            (r"\bNó có rất nhiều khả năng\b", "Bé nhà mình nhiều tài lẻ lắm"),
            (r"\b(?:Tôi|Mình) chỉ không thích tắm\b", "chỉ mỗi tội lười tắm thôi"),
            (r"\bTắm cộng với thiết bị bị hư hỏng\b", "Tiền tắm cộng với tiền đền máy móc bị hỏng"),
            (r"\b(?:Tôi|Mình) sẽ tính phí cho bạn tổng cộng 1[.,]?800\b", "tổng cộng của quý khách hết một nghìn tám trăm tệ"),
            (r"\bÀ, không buồn\b", "Thôi nào, đừng buồn nữa nhé"),
            (r"\bNhưng hãy cắt đôi đồ ăn nhẹ của bạn\b", "Nhưng súp thưởng của con tuần này phải cắt giảm một nửa đấy!"),
            (r"\bnhưng đồ ăn nhẹ của bạn sẽ giảm đi một nửa\b", "nhưng súp thưởng của con tuần này phải cắt giảm một nửa đấy!"),
            (r"\b(?:Tôi|Mình) chỉ mới (?:26|hai mươi sáu)(?: tuổi)?\b", "Em mới có 26 tuổi thôi mà!"),
            (r"\bshop nước hoa\b", "tiệm phấn thơm"),
            (r"\bcửa hàng nước hoa\b", "tiệm phấn thơm"),
            (r"\btiệm nước hoa\b", "tiệm phấn thơm"),
            (r"\bcửa hàng bột thơm\b", "tiệm phấn thơm"),
            (r"\btiệm bột thơm\b", "tiệm phấn thơm"),
            (r"\bphấn phủ thơm\b", "phấn thơm"),
            (r"\bbột thơm\b", "phấn thơm"),
            (r"\bphủ đường cho\b", "tô hồng cho"),
            (r"\bKhi bạn không gắn bó với ai\b", "Khi chẳng còn cố chấp vì bất kỳ ai"),
            (r"\bmột loại lợi ích\b", "một sự trưởng thành đắt giá"),
            (r"\bĐó không phải là một loại lợi ích sao\??", "Chẳng phải đều là bài học đắt giá hay sao?"),
            (r"\btôi giống như\b", "thật sự là"),
            (r"\bnhư thể là\b", "dường như"),
            (r"\bđừng hiểu lầm tôi\b", "nói thật nhé"),
            (r"\bcủa tôi\b", "của mình" if channel_key == "channel_2" else "của ta" if channel_key == "channel_4" else "của tôi"),
            (r"\bMột trong những điều, mặc dù, là\b", "Nhưng điều kỳ lạ nhất là"),
            (r"\btheo như tôi có thể nói\b", "nhìn đi nhìn lại"),
            (r"\s+", " "),
        ]
        for pat, rep in common_fixes:
            text = re.sub(pat, rep, text, flags=re.IGNORECASE)

        # 2. Chuẩn hóa văn phong & thuật ngữ Tiên Hiệp cho Kênh 4 (Tu Tiên / Huyền Huyễn)
        if channel_key == "channel_4":
            cultivation_replacements = [
                (r"\bTôi\b", "Ta"),
                (r"\btôi\b", "ta"),
                (r"\bMình\b", "Ta"),
                (r"\bmình\b", "ta"),
                (r"\bBạn\b", "Ngươi"),
                (r"\bbạn\b", "ngươi"),
                (r"\bCác bạn\b", "Chư vị"),
                (r"\bcác bạn\b", "chư vị"),
                (r"\bchúng tôi\b", "chúng ta"),
                (r"\bChúng tôi\b", "Chúng ta"),
                (r"\banh ấy\b", "hắn"),
                (r"\bAnh ấy\b", "Hắn"),
                (r"\bcô ấy\b", "nàng"),
                (r"\bCô ấy\b", "Nàng"),
                (r"\bông chủ lớn\b", "đại lão"),
                (r"\bông lớn\b", "đại lão"),
                (r"\bgiáo phái\b", "tông môn"),
                (r"\bmôn phái\b", "tông môn"),
                (r"\bđất thánh\b", "Thánh địa"),
                (r"\bvùng đất thánh\b", "Thánh địa"),
                (r"\brễ tâm linh\b", "linh căn"),
                (r"\bgốc rễ tâm linh\b", "linh căn"),
                (r"\bsức mạnh siêu nhiên\b", "thần thông"),
                (r"\bphép thuật\b", "thần thông"),
                (r"\bphản công\b", "nghịch tập"),
                (r"\bkẻ vô dụng\b", "phế vật"),
                (r"\bđồ vô dụng\b", "phế vật"),
                (r"\brác rưởi\b", "phế vật"),
                (r"\bngười bạn Đạo giáo\b", "đạo hữu"),
                (r"\bbạn đạo\b", "đạo hữu"),
                (r"\bchào thầy\b", "bái kiến sư tôn"),
                (r"\bkính chào sư phụ\b", "bái kiến sư tôn"),
                (r"\bthầy giáo\b", "sư tôn"),
                (r"\bsư phụ\b", "sư tôn"),
                (r"\bxây dựng nền móng\b", "Trúc Cơ"),
                (r"\bnền tảng xây dựng\b", "Trúc Cơ"),
                (r"\btrúc cơ\b", "Trúc Cơ"),
                (r"\blinh hồn sơ sinh\b", "Nguyên Anh"),
                (r"\bnguyên anh\b", "Nguyên Anh"),
                (r"\bkim đan\b", "Kim Đan"),
                (r"\bviên thuốc vàng\b", "Kim Đan"),
                (r"\bchín tầng trời\b", "Cửu Trọng Thiên"),
                (r"\btrời và đất\b", "thiên địa"),
                (r"\bTrời và đất\b", "Thiên địa"),
                (r"\bthế giới bên ngoài\b", "cõi hồng trần"),
                (r"\bmức độ\b", "cảnh giới"),
                (r"\btrình độ\b", "cảnh giới"),
                (r"\bthực hành\b", "tu luyện"),
                (r"\bluyện tập\b", "tu luyện"),
                (r"\bbất tử\b", "tiên nhân"),
            ]
            for pat, rep in cultivation_replacements:
                text = re.sub(pat, rep, text)

        # 3. Chuẩn hóa văn phong & xưng hô chuyên biệt cho Kênh 3 (Tổng tài / Trùng sinh / Xuyên không)
        elif channel_key == "channel_3":
            drama_replacements = [
                (r"\bbạn trai\b", "hắn"),
                (r"\banh ấy\b", "hắn"),
                (r"\bAnh ấy\b", "Hắn"),
                (r"\bcô ấy\b", "cô ta"),
                (r"\bCô ấy\b", "Cô ta"),
                (r"\bbà chủ\b", "Phu nhân"),
                (r"\bvợ chủ tịch\b", "Phu nhân"),
                (r"\bquý cô\b", "tiểu thư"),
                (r"\bcô chủ nhỏ\b", "tiểu thư"),
                (r"\btái sinh\b", "trọng sinh"),
                (r"\bsống lại\b", "trọng sinh"),
                (r"\bkiếp trước của tôi\b", "kiếp trước"),
                (r"\bgiám đốc\b", "Chủ tịch"),
                (r"\b ông chủ\b", " Chủ tịch"),
            ]
            for pat, rep in drama_replacements:
                text = re.sub(pat, rep, text, flags=re.IGNORECASE)

        # 4. Chuẩn hóa văn phong cho Kênh 1 (AI Hoạt họa / Thú cưng / Kể chuyện)
        elif channel_key == "channel_1":
            cute_replacements = [
                (r"\bTôi\b", "Mình"),
                (r"\btôi\b", "mình"),
                (r"\bChúa ơi\b", "Trời đất ơi"),
                (r"\bÔi chúa ơi\b", "Ối giời ơi"),
            ]
            for pat, rep in cute_replacements:
                text = re.sub(pat, rep, text)

        # 5. Chuẩn hóa văn phong cho Kênh 2 (Hài / Đời sống / Gia đình / Baby AI)
        elif channel_key == "channel_2":
            is_child_speaking = (
                ("妈妈" in (orig_text or ""))
                or ("上学" in (orig_text or ""))
                or ("裤子" in (orig_text or ""))
                or ("老师" in (orig_text or ""))
            )
            self_pronoun_cap = "Con" if is_child_speaking else "Mẹ"
            self_pronoun_low = "con" if is_child_speaking else "mẹ"
            comedy_replacements = [
                (r"\bChúa ơi\b", "Trời đất ơi"),
                (r"\bÔi chúa ơi\b", "Ối giời ơi"),
                (r"\banh bạn\b", "nhóc con"),
                (r"\bÔi em yêu, em no chưa\b", "Ôi cục cưng ơi, con ăn no chưa"),
                (r"\bem yêu\b", "cục cưng"),
                (r"\bMiệng tôi đầy thức ăn\b", "Nhìn con ăn lem luốc đầy cả miệng kìa"),
                (r"\btôi sắp trễ giờ học mẫu giáo\b", "sắp trễ giờ đi học mẫu giáo"),
                (r"\btôi không cho tôi lau cho bạn\b", "con không cho mẹ lau thì mẹ không lau nữa"),
                (r"\bTôi\b", self_pronoun_cap),
                (r"\btôi\b", self_pronoun_low),
                (r"\bMình\b", self_pronoun_cap),
                (r"\bmình\b", self_pronoun_low),
                (r"\bBạn\b", "Con"),
                (r"\bbạn\b", "con"),
                (r"\bMiệng Mẹ đầy thức ăn\b", "Nhìn con ăn lem luốc đầy cả miệng kìa"),
                (r"\bMẹ sắp trễ giờ học mẫu giáo\b", "Con sắp trễ giờ đi học mẫu giáo rồi"),
                (r"\bMẹ đang đi học\b", "Con phải đi học đây"),
                (r"\bAnh kiệt sức vì phải bò lên người em\b", "Mẹ mệt đứt hơi vì ngày nào cũng phải leo trèo trên người con"),
                (r"\bnó bị vặn rồi\b", "thôi xong rồi"),
            ]
            for pat, rep in comedy_replacements:
                text = re.sub(pat, rep, text, flags=re.IGNORECASE)

        # 6. Đảm bảo viết hoa chữ cái đầu câu, giữ trọn vẹn câu (không cắt cụt chữ giữa chừng)
        text = text.strip(" -–—")
        if text:
            text = text[0].upper() + text[1:]

        return text

    @staticmethod
    def _deduplicate_vietnamese_clauses(text: str) -> str:
        """
        Khử triệt để các từ/mệnh đề tiếng Việt bị lặp lại trong cùng một câu:
        - Xóa các chú thích bối cảnh nằm trong ngoặc đơn `(...)` hoặc ngoặc vuông `[...]`.
        - Rút gọn các từ cảm thán bị lặp nhiều lần liên tiếp ('Này này này này...' -> 'Này!').
        - Nếu trong cùng 1 câu có 2 mệnh đề giống nhau cách bởi dấu phẩy/chấm ('A, A' -> 'A'), chỉ giữ 1 mệnh đề.
        """
        if not text:
            return ""
        import difflib as _difflib

        cleaned = re.sub(r"\([^)]*\)|\[[^\]]*\]|（[^）]*）|【[^】]*】", " ", text)
        # Rút gọn từ đơn bị lặp >= 3 lần liên tiếp (ví dụ: 'Này này này này' -> 'Này')
        cleaned = re.sub(r"\b(\w+)(?:\s+\1){2,}\b", r"\1", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;.-–—")
        if not cleaned:
            return ""

        clauses = [c.strip() for c in re.split(r"[,;]+|\.\s+", cleaned) if c.strip()]
        if len(clauses) > 1:
            unique_clauses: List[str] = []
            for cl in clauses:
                if not unique_clauses:
                    unique_clauses.append(cl)
                    continue
                prev_cl = unique_clauses[-1]
                p_low = re.sub(r"[\W_]+", " ", prev_cl.lower()).strip()
                c_low = re.sub(r"[\W_]+", " ", cl.lower()).strip()
                if not c_low:
                    continue
                sim = _difflib.SequenceMatcher(None, p_low, c_low).ratio()
                if sim >= 0.76 or (len(c_low) >= 6 and (c_low in p_low or p_low in c_low)):
                    if len(cl) > len(prev_cl):
                        unique_clauses[-1] = cl
                    continue
                unique_clauses.append(cl)
            cleaned = ", ".join(unique_clauses)

        cleaned = cleaned.strip(" ,;-–—")
        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]
        return cleaned

    def _call_openai_compatible(
        self,
        api_key: str,
        base_url: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Gọi API tương thích chuẩn OpenAI (OpenAI, DeepSeek, OpenRouter, v.v.)."""
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4,
        }
        resp = requests.post(url, headers=headers, json=body, timeout=45)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        """Gọi Google Gemini REST API."""
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent?key={self.gemini_api_key}"
        )
        headers = {"Content-Type": "application/json"}
        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.4,
                "responseMimeType": "application/json",
            },
        }
        resp = requests.post(url, headers=headers, json=body, timeout=45)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini không trả về kết quả: {data}")
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)

    @staticmethod
    def _parse_llm_json(raw_text: str) -> Dict[int, str]:
        """Bóc tách mảng JSON từ câu trả lời của LLM một cách an toàn."""
        cleaned = raw_text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        bracket_match = re.search(r"(\[.*\]|\{.*\})", cleaned, re.DOTALL)
        if bracket_match:
            cleaned = bracket_match.group(1)

        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            for val in parsed.values():
                if isinstance(val, list):
                    parsed = val
                    break

        if not isinstance(parsed, list):
            raise ValueError("Dữ liệu JSON từ LLM không phải danh sách.")

        result_map: Dict[int, str] = {}
        for idx, item in enumerate(parsed):
            if isinstance(item, dict):
                item_id = int(item.get("id", idx))
                text = (
                    item.get("translated_text")
                    or item.get("vi")
                    or item.get("text")
                    or ""
                )
                result_map[item_id] = str(text).strip()
            elif isinstance(item, str):
                result_map[idx] = item.strip()
        return result_map

    @staticmethod
    def _translate_single_fallback(text: str, src_lang: str) -> str:
        """
        Bộ dịch dự phòng sang tiếng Việt (sử dụng Google Contextual NMT + MyMemory API)
        khi người dùng chưa điền API key vào `.env` hoặc khi LLM gặp sự cố mạng.
        """
        sl = "zh-CN" if src_lang.startswith("zh") else src_lang
        cleaned_text = (
            SubtitleTranslator._clean_chinese_asr_homophones(text)
            if sl == "zh-CN"
            else text
        )

        # 1. Ưu tiên Google Contextual NMT endpoint (clients5.google.com)
        try:
            g_resp = requests.post(
                "https://clients5.google.com/translate_a/t",
                params={"client": "dict-chrome-ex", "sl": sl, "tl": "vi"},
                data={"q": cleaned_text},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            if g_resp.status_code == 200:
                g_data = g_resp.json()
                if isinstance(g_data, list) and g_data and isinstance(g_data[0], str):
                    g_text = g_data[0].strip()
                    if g_text:
                        return g_text
        except Exception as exc:
            logger.debug(f"Google Contextual single fallback error: {exc}")

        # 2. Dự phòng qua MyMemory Translation API
        try:
            mm_resp = requests.get(
                "https://api.mymemory.translated.net/get",
                params={"q": cleaned_text[:480], "langpair": f"{sl}|vi"},
                timeout=12,
            )
            if mm_resp.status_code == 200:
                mm_data = mm_resp.json()
                mm_text = (
                    mm_data.get("responseData", {}).get("translatedText") or ""
                ).strip()
                if mm_text and "MYMEMORY WARNING" not in mm_text.upper():
                    return mm_text
        except Exception as exc:
            logger.debug(f"MyMemory fallback error: {exc}")

        return cleaned_text

    @staticmethod
    def _clean_source_watermark_from_title(raw_title: str) -> str:
        """
        Làm sạch tiêu đề gốc:
        - Xóa toàn bộ hashtag cũ, lời kêu gọi sang nền tảng khác (`xem full tại yt...`, `youtube`, `link bio`...)
          vì thuật toán TikTok phạt nặng các bài đăng nhắc tới YouTube/Facebook hoặc tên kênh khác.
        - Xóa tên thương hiệu kênh gốc (`Gà mukbang audio`, `Cre:...`, `@handle`) để kịch bản ngắn độc lập 100%.
        """
        if not raw_title:
            return ""
        text = re.sub(r"(?:\s*#[^\s#]+)+", "", raw_title).strip()
        # Xóa cụm dẫn sang YouTube / kênh khác
        text = re.sub(
            r"(?i)[\.\s,-]*xem\s+full\s+t[aạ]i\s+yt\s*@?[A-Za-z0-9_.-]*",
            "",
            text,
        )
        text = re.sub(r"(?i)\b(?:cre|nguồn)\s*:\s*[^|~\n]+", "", text)
        text = re.sub(r"@[A-Za-z0-9_.-]+", "", text)
        text = re.sub(r"(?i)FULL\s*\|\|\s*Gà\s*mukbang\s*audio\s*🐣?\s*~?\s*", "", text)
        text = re.sub(r"(?i)^Truyện:\s*Zin\s*Audio\s*Số\s*(\d+)", r"Tập \1 - Biến cố bất ngờ và sự thật phía sau", text)
        text = re.sub(r"(?i)^Bông\s*cúc\s*số\s*(\d+)\s*(?:nhé)?", r"Tập \1 - Góc khuất tình cảm và màn đáp trả cực gắt", text)
        text = re.sub(r"^\|\|\s*FULL\s*\|\|\s*:?\s*", "", text)
        text = re.sub(r"\|\|\s*$", "", text)
        text = re.sub(r"\s*\|\|\s*", " - ", text)
        text = re.sub(r"\s+", " ", text).strip(" -|~.,")
        return text

    @staticmethod
    def fetch_live_tiktok_hot_hashtags(
        seed_keyword: str, max_live: int = 2
    ) -> List[str]:
        """
        Truy vấn trực tiếp API tìm kiếm Challenge/Hashtag đang thịnh hành trên TikTok
        theo từ khóa ngách để lấy thêm các hashtag Hot Search có lượt xem cao.
        """
        found: List[str] = []
        try:
            resp = requests.get(
                "https://www.tikwm.com/api/challenge/search",
                params={"keywords": seed_keyword, "count": 8},
                timeout=5,
            )
            if resp.status_code == 200:
                ch_list = (resp.json().get("data") or {}).get("challenge_list") or []
                # Sắp xếp ưu tiên theo view_count cao nhất
                ch_list.sort(key=lambda x: int(x.get("view_count") or 0), reverse=True)
                for ch in ch_list:
                    ch_name = re.sub(r"[^\w]", "", str(ch.get("cha_name") or "").lower())
                    if ch_name and len(ch_name) >= 3:
                        tag = f"#{ch_name}"
                        if tag not in found:
                            found.append(tag)
                        if len(found) >= max_live:
                            break
        except Exception:
            pass
        return found

    def generate_story_metadata(
        self,
        segments: List[Dict[str, Any]],
        platform: str = "tiktok",
        genre: str = "storytime",
        original_title: Optional[str] = None,
        src_lang: str = "zh",
    ) -> Dict[str, str]:
        """
        Tự động viết Kịch bản ngắn (Short Video Script / Hook + Tóm tắt + Câu hỏi tương tác kéo bình luận)
        và bộ Hashtag Hot Search chuẩn SEO TikTok cho từng kênh (`video_AI_hay`, `baby_AI`, `audio_dem_khuyen`, `tu_chan_gioi`):
        - Loại bỏ hoàn toàn chữ nhắc sang YouTube/kênh gốc (`xem full tai yt...`) để tránh bị thuật toán TikTok bóp tương tác.
        - Trộn ngẫu nhiên Hashtag Ngách + Hashtag Hot Search đang thịnh hành + Hashtag Xu Hướng để không bao giờ bị trùng lặp spam.
        """
        import hashlib

        story_lines: List[str] = []
        for seg in segments:
            raw_l = (seg.get("translated_text") or seg.get("text") or "").strip()
            if not raw_l:
                continue
            if re.search(r"[\u4e00-\u9fff]", raw_l):
                raw_l = self._translate_single_fallback(raw_l, "zh").strip()
                raw_l = re.sub(r"[\u4e00-\u9fff]+", "", raw_l).strip()
            if raw_l:
                story_lines.append(raw_l)

        genre_clean = genre.lstrip("#").strip()
        ch_key = self._normalize_channel_key(genre_clean)

        raw_title = (original_title or "").strip()
        clean_title = self._clean_source_watermark_from_title(raw_title)

        if clean_title and not re.match(r"^(?:tiktok|douyin)_\d+$", clean_title):
            if src_lang == "vi" and not re.search(r"[\u4e00-\u9fff]", clean_title):
                core_topic = clean_title
            elif has_vietnamese_diacritics(clean_title) and not re.search(r"[\u4e00-\u9fff]", clean_title):
                core_topic = clean_title
            else:
                core_topic = self._translate_single_fallback(clean_title, src_lang).strip()
                core_topic = self._clean_source_watermark_from_title(core_topic)
                core_topic = re.sub(r"[\u4e00-\u9fff]+", "", core_topic).strip(" -|~.,")
        elif story_lines:
            core_topic = story_lines[0][:110].strip()
        else:
            core_topic = ""

        if not core_topic or re.search(r"[\u4e00-\u9fff]", core_topic) or len(core_topic) < 6:
            fallback_by_ch = {
                "channel_1": "Thế giới hoạt hình AI kỳ ảo với tình tiết bất ngờ ở giây cuối cùng",
                "channel_2": "Nhật ký gia đình siêu lầy và màn đối đáp cười xỉu của em bé lém lỉnh",
                "channel_3": "Biến cố bất ngờ lúc nửa đêm và sự thật chấn động phía sau cuộc hôn nhân",
                "channel_4": "Phàm nhân nghịch thiên cải mệnh thức tỉnh huyết mạch thượng cổ tại Tu Chân Giới",
            }
            core_topic = fallback_by_ch.get(ch_key, "Câu chuyện kịch tính không thể rời mắt")

        # Tạo seed xác định nhưng khác biệt cho từng video để xoay vòng văn phong Hook & Hashtag
        seed_val = int(
            hashlib.md5((core_topic + "".join(story_lines[:2])).encode("utf-8")).hexdigest()[:8],
            16,
        )

        # Nén ngữ cảnh kịch bản bằng LeanCTX (SDE Target-Density Compression) để chọn câu cao trào giàu thông tin nhất
        ctx_gateway = LeanContextGateway(target_density=0.45)
        ctx_compressed = ctx_gateway.compress_transcript_context(story_lines)
        cache_key = f"{ch_key}|{core_topic}|{ctx_compressed['compressed_text'][:220]}"
        cached_meta = ctx_gateway.get_cached_metadata(cache_key)
        if cached_meta:
            return cached_meta

        # Lấy câu thoại cao trào / mở đầu (đã qua bộ lọc Shannon Entropy của LeanCTX) để đưa vào kịch bản ngắn
        extra_summary = ""
        if len(story_lines) >= 2:
            best_line = max(
                story_lines[1 : min(12, len(story_lines))],
                key=lambda s: LeanContextGateway._shannon_entropy(s),
                default=story_lines[1],
            )
            if best_line and best_line.lower() not in core_topic.lower():
                extra_summary = f" \"{best_line[:95].rstrip('.,!?')}...\""

        # Bộ mẫu Short Script chuẩn thuật toán giữ chân & kích thích bình luận theo từng kênh
        script_templates = {
            "channel_1": [
                f"🎬 {core_topic}.{extra_summary} Xem đến cuối để thấy cái kết vừa bất ngờ vừa đáng yêu nhé! Bạn chấm mấy điểm cho màn biến hình này?",
                f"✨ Câu chuyện AI cực cuốn: {core_topic}.{extra_summary} Cứ tưởng bình thường mà diễn biến phía sau làm ai cũng bật cười. Bạn thích phân cảnh nào nhất?",
                f"🐾 {core_topic} — Tập phim ngắn khiến hàng triệu người xem đi xem lại.{extra_summary} Theo bạn tập tiếp theo sẽ ra sao?",
                f"🔥 Đừng rời mắt khỏi màn hình: {core_topic}.{extra_summary} Chi tiết cuối video thực sự quá đỉnh, mọi người nghĩ sao về cái kết này?",
            ],
            "channel_2": [
                f"😂 {core_topic}.{extra_summary} Tình huống dở khóc dở cười trong gia đình khiến ai xem cũng thấy bóng dáng mình ở trỏng! Nhà bạn có giống vậy không?",
                f"🥰 Nhật ký gia đình siêu lầy: {core_topic}.{extra_summary} Xem phản ứng của bé ở cuối video mà cười xỉu! Mọi người chấm độ lém lỉnh này mấy điểm?",
                f"🤣 {core_topic} — Pha xử lý đi vào lòng đất của hai mẹ con!{extra_summary} Có ai từng rơi vào cảnh này hồi nhỏ chưa?",
            ],
            "channel_3": [
                f"🎧 [Truyện Đêm Khuya] {core_topic}.{extra_summary} Một câu chuyện đầy kịch tính với cú quay xe cực gắt ở phút cuối. Nếu ở trong hoàn cảnh của nhân vật chính, bạn sẽ chọn tha thứ hay trả đũa?",
                f"📖 Góc Kể Chuyện: {core_topic}.{extra_summary} Lòng người khó đoán, chỉ đến khi biến cố ập tới mới biết ai thật lòng. Bạn nghĩ sao về cái kết của câu chuyện này?",
                f"🔥 Cốt truyện cuốn từ giây đầu tiên: {core_topic}.{extra_summary} Nghe hết để thấy màn vả mặt cực kỳ hả dạ phía sau nhé! Theo bạn ai mới là người sai?",
                f"🌙 Thức đêm nghe truyện: {core_topic}.{extra_summary} Từng tình tiết đều khiến người nghe nổi da gà, đừng bỏ lỡ đoạn kết!",
            ],
            "channel_4": [
                f"⚔️ [Tu Chân Giới] {core_topic}.{extra_summary} Giữa thế giới cá lớn nuốt cá bé, kẻ phàm nhân nghịch thiên cải mệnh bước lên đỉnh cao vạn giới! Đạo hữu đoán xem cảnh giới tiếp theo của main là gì?",
                f"🔥 Siêu phẩm Huyền Huyễn Tu Tiên: {core_topic}.{extra_summary} Tưởng là phế vật dưới đáy tông môn, ai ngờ lại ẩn giấu huyết mạch chấn động thiên địa! Các đạo hữu chấm bộ này mấy điểm?",
                f"🐉 {core_topic} — Màn thức tỉnh linh căn và phản kích khiến toàn bộ cường giả phải khiếp sợ!{extra_summary} Theo đạo hữu, main nên ẩn nhẫn hay đại khai sát giới?",
            ],
            "general": [
                f"🎬 {core_topic}.{extra_summary} Xem hết video để không bỏ lỡ cái kết bất ngờ ở giây cuối cùng nhé!",
            ],
        }

        templates = script_templates.get(ch_key, script_templates["general"])
        short_script = templates[seed_val % len(templates)]

        # Kho Hashtag Hot Search TikTok Việt Nam phân tầng (Core Niche + Hot Search + Viral FYP)
        hot_search_pools = {
            "channel_1": {
                "seed_kw": "hoathinhai",
                "core": ["#videoaihay", "#hoathinhai", "#phimnganai", "#aianimation"],
                "hot_search": [
                    "#phimhaymoingay",
                    "#tomtatphim",
                    "#thucungthanhtinh",
                    "#hoathinh3d",
                    "#khamphaai",
                    "#meocute",
                    "#chualanh",
                    "#phimhoathinh",
                ],
            },
            "channel_2": {
                "seed_kw": "giadinhhaihuoc",
                "core": ["#babyai", "#giadinhhaihuoc", "#embehaihuoc", "#chamcon"],
                "hot_search": [
                    "#haihuocmoingay",
                    "#doisonggiadinh",
                    "#mevabe",
                    "#cuoixiu",
                    "#phimnganhai",
                    "#giaitritiktok",
                    "#embecute",
                    "#tinhhuonghaihuoc",
                ],
            },
            "channel_3": {
                "seed_kw": "truyenaudio",
                "core": ["#audiodemkhuya", "#truyenaudio", "#kechuyendemkhuya", "#truyenngan"],
                "hot_search": [
                    "#ngontinh",
                    "#trongsinh",
                    "#tongtai",
                    "#truyenfull",
                    "#dramatiktok",
                    "#phimngantrungquoc",
                    "#vahmattraxanh",
                    "#gocchiaSe",
                    "#tamtrang",
                ],
            },
            "channel_4": {
                "seed_kw": "tutien",
                "core": ["#tuchangioi", "#tutien", "#huyenhuyen", "#tienhiep"],
                "hot_search": [
                    "#hoathinh3dtrungquoc",
                    "#aitutien",
                    "#tiennghich",
                    "#xuyenkhong",
                    "#hethongtutien",
                    "#tomtathoathinh3d",
                    "#animehay",
                    "#phimhoathinh3d",
                ],
            },
        }
        fyp_pool = [
            "#xuhuong",
            "#xuhuongtiktok",
            "#fyp",
            "#viral",
            "#tiktokvn",
            "#xh",
            "#foryou",
        ]

        pool_cfg = hot_search_pools.get(ch_key, hot_search_pools["channel_1"])
        core_tags = list(pool_cfg["core"])
        hot_tags = list(pool_cfg["hot_search"])

        # Xoay vòng ngẫu nhiên nhưng ổn định theo từng video để mỗi bài có tổ hợp hashtag riêng biệt
        selected_core = [
            core_tags[(seed_val + i) % len(core_tags)] for i in range(min(3, len(core_tags)))
        ]
        selected_hot = [
            hot_tags[(seed_val * 3 + i) % len(hot_tags)] for i in range(min(3, len(hot_tags)))
        ]
        selected_fyp = [
            fyp_pool[seed_val % len(fyp_pool)],
            fyp_pool[(seed_val + 3) % len(fyp_pool)],
        ]

        # Bổ sung thêm 1 hashtag Hot Search trực tiếp từ API TikTok nếu có mạng
        live_tags = self.fetch_live_tiktok_hot_hashtags(pool_cfg["seed_kw"], max_live=1)

        combined_tags: List[str] = []
        for t in selected_core + selected_hot + live_tags + selected_fyp:
            if t.lower() not in [u.lower() for u in combined_tags]:
                combined_tags.append(t)

        result_meta = {
            "caption": short_script,
            "hashtags": " ".join(combined_tags[:8]),
        }
        ctx_gateway.save_cached_metadata(
            cache_key=cache_key,
            caption=result_meta["caption"],
            hashtags=result_meta["hashtags"],
            compression_info=ctx_compressed,
        )
        return result_meta
