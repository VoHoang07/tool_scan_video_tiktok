import difflib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from faster_whisper import WhisperModel

from .utils import ensure_ffmpeg, logger

# Các từ cảm thán mở đầu câu thường có trong giọng nói nhưng đôi khi không in trên phụ đề gốc
LEADING_SPOKEN_INTERJECTIONS = (
    "算了",
    "好啦",
    "好了",
    "哎呀妈呀",
    "哎呀",
    "对不起",
    "不好意思",
)

# Sửa các chữ tiếng Trung có nét vẽ gần giống dễ bị OCR nhầm trên font nghệ thuật
OCR_LOOKALIKE_FIXES = [
    ("冼澡", "洗澡"),
    ("玄排", "安排"),
    ("不爰", "不爱"),
    ("回耒", "回来"),
    ("辉堭", "辉煌"),
    ("辉惶", "辉煌"),
    ("等诗", "等待"),
    ("社芳春", "杜芳春"),
    ("过芳春", "杜芳春"),
    ("萌爸爸", "求爸爸"),
    ("绺小姐", "给小姐"),
    ("臷好", "戴好"),
    ("臷个", "戴个"),
    ("涑度", "速度"),
    ("不许酉骑", "不许再骑"),
    ("酉骑", "再骑"),
    ("酉见", "再见"),
    ("老极", "老板"),
    ("咱家珍子", "咱家孩子"),
    ("汶就", "这就"),
    ("头-回", "头一回"),
    ("水全用我", "水全甩我"),
    ("减毕", "减半"),
    ("遗撼", "遗憾"),
    ("不相学", "不想学"),
]


class AudioTranscriber:
    """
    Nhận diện giọng nói và tự động phát hiện ngôn ngữ gốc (tiếng Trung `zh`,
    tiếng Anh `en`, v.v.) bằng `faster-whisper`, kết hợp Apple Vision OCR (`zh-Hans`)
    để khử triệt để lỗi đồng âm tiếng Trung của Whisper và gộp mệnh đề thông minh.
    """

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
    ):
        self.model_size = model_size or os.getenv("WHISPER_MODEL", "small")
        self.device = device or os.getenv("WHISPER_DEVICE", "cpu")
        self.compute_type = compute_type or os.getenv("WHISPER_COMPUTE_TYPE", "int8")
        self._model: Optional[WhisperModel] = None

    @property
    def model(self) -> WhisperModel:
        if self._model is None:
            logger.info(
                f"Đang khởi tạo faster-whisper model='{self.model_size}' "
                f"(device={self.device}, compute_type={self.compute_type})..."
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def transcribe(
        self,
        audio_path: Path,
        language: Optional[str] = None,
        save_json_path: Optional[Path] = None,
        video_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Bóc tách băng ghi âm thành danh sách segments kèm ngôn ngữ phát hiện được:
        1. Chạy `faster-whisper` nhận diện mốc thời gian & câu thoại, lọc bỏ tạp âm (`no_speech_prob`, `avg_logprob`).
        2. Nếu là video tiếng Trung (`zh`) có phụ đề gốc trên hình, tự động đối chiếu với Apple Vision OCR
           để sửa chính xác 100% các từ đồng âm bị Whisper nghe nhầm.
        3. Gộp các mệnh đề vụn (<1.2s) liền kề thành câu hoàn chỉnh để dịch thuật có nghĩa và lồng tiếng mượt mà.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file âm thanh: {audio_path}")

        inferred_video = video_path or (audio_path.parent / "raw_video.mp4")
        has_video = inferred_video.exists()

        logger.info(f"Đang nhận diện giọng nói từ: {audio_path} (auto-detect language, word_timestamps=True)...")
        segments_gen, info = self.model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=250, speech_pad_ms=120, threshold=0.35),
            word_timestamps=True,
            condition_on_previous_text=False,
            repetition_penalty=1.18,
            no_repeat_ngram_size=3,
        )

        detected_lang = (info.language or "en").lower()
        lang_prob = float(info.language_probability or 0.0)
        logger.info(
            f"Ngôn ngữ phát hiện: '{detected_lang}' (độ tin cậy: {lang_prob:.2%})"
        )

        whisper_seg_list = list(segments_gen)
        audio_dur_est = float(getattr(info, "duration", 0.0) or 0.0)

        # Dự phòng: nếu Silero VAD cắt quá tay làm gộp cả bài thành <= 2 câu trên video dài >= 10s,
        # chạy lại với vad_filter=False + word_timestamps=True để lấy đầy đủ từng câu theo đúng mốc từ.
        if len(whisper_seg_list) <= 2 and audio_dur_est >= 10.0:
            segs_no_vad, _ = self.model.transcribe(
                str(audio_path),
                language=detected_lang,
                beam_size=5,
                vad_filter=False,
                word_timestamps=True,
                condition_on_previous_text=False,
                repetition_penalty=1.18,
                no_repeat_ngram_size=3,
            )
            no_vad_list = list(segs_no_vad)
            if len(no_vad_list) > len(whisper_seg_list):
                logger.info(
                    f"[Word-Timestamp Recovery] Phục hồi {len(no_vad_list)} câu thoại chuẩn xác (thay vì {len(whisper_seg_list)} câu bị gộp do VAD)."
                )
                whisper_seg_list = no_vad_list

        raw_segments: List[Dict[str, Any]] = []
        logprobs: List[float] = []

        for seg in whisper_seg_list:
            text = (seg.text or "").strip()
            if not text:
                continue

            no_speech = float(getattr(seg, "no_speech_prob", 0.0) or 0.0)
            avg_lp = float(getattr(seg, "avg_logprob", -0.3) or -0.3)
            if (no_speech > 0.92 and avg_lp < -0.85) or avg_lp < -1.15:
                continue

            aligned_sub_segs = self._extract_word_aligned_segments(seg, detected_lang, avg_lp)
            for sub_s in aligned_sub_segs:
                logprobs.append(avg_lp)
                raw_segments.append(sub_s)

        mean_logprob = sum(logprobs) / len(logprobs) if logprobs else -1.0
        ocr_coverage = 0.0

        # Bước 2: Khử lỗi đồng âm tiếng Trung của Whisper bằng Apple Vision OCR (chỉ sửa đồng âm, chặn sạch chữ tĩnh/poster)
        if has_video and detected_lang.startswith("zh") and raw_segments:
            raw_segments, ocr_coverage = self._refine_chinese_segments_with_vision_ocr(
                video_path=inferred_video,
                segments=raw_segments,
            )

        # Bước 3: Khử câu lặp/nói lắp và gộp các mệnh đề quá ngắn thành câu hoàn chỉnh tự nhiên
        segments = self._merge_short_clauses(raw_segments, detected_lang)

        logger.info(
            f"Đã bóc tách & chuẩn hóa thành công {len(segments)} câu thoại hoàn chỉnh "
            f"(mean_logprob={mean_logprob:.2f}, ocr_coverage={ocr_coverage:.0%})."
        )

        result: Dict[str, Any] = {
            "language": detected_lang,
            "language_probability": round(lang_prob, 4),
            "mean_avg_logprob": round(mean_logprob, 4),
            "ocr_coverage": round(ocr_coverage, 4),
            "segments": segments,
        }

        if save_json_path:
            Path(save_json_path).write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return result

    @classmethod
    def _is_meaningless_filler_text(cls, text: str) -> bool:
        """Loại bỏ các đoạn chỉ có tiếng cười, tiếng thở/kêu cảm thán lặp lại ('哎哎哎', '嘿嘿嘿', '哈哈哈') hoặc 1 chữ rời rạc."""
        cleaned = re.sub(r"[\s，,。.!！?？:：;；、~～\-—…'\"“”‘’（）()【】\[\]]+", "", text or "")
        if not cleaned:
            return True
        if len(cleaned) == 1 and cleaned in ("我", "啊", "哦", "嗯", "呀", "呢", "吧", "喂", "诶", "唉", "噢", "哈", "嘿"):
            return True
        unique_chars = set(cleaned)
        if len(unique_chars) == 1 and cleaned[0] in ("哎", "嘿", "哈", "啊", "哦", "嗯", "呀", "呜", "呵", "唉", "噢"):
            return True
        if cleaned in ("是啊", "哎呀", "好啦", "好了") and len(cleaned) <= 2:
            return False
        return False

    @classmethod
    def _deduplicate_internal_clauses(cls, text: str) -> str:
        """Khử các vế câu bị lặp lại ngay bên trong cùng 1 segment (ví dụ: 'A，A' -> 'A')."""
        if not text:
            return ""
        parts = [p.strip() for p in re.split(r"[，,。.!！?？;；]+", text) if p.strip()]
        if len(parts) <= 1:
            return text.strip()
        deduped: List[str] = []
        for p in parts:
            if not deduped:
                deduped.append(p)
                continue
            prev_p = deduped[-1]
            sim = difflib.SequenceMatcher(None, prev_p, p).ratio()
            if sim >= 0.76 or (len(p) >= 3 and (p in prev_p or prev_p in p)):
                if len(p) > len(prev_p):
                    deduped[-1] = p
                continue
            deduped.append(p)
        sep = "，" if re.search(r"[\u4e00-\u9fff]", text) else ", "
        return sep.join(deduped).strip()

    @classmethod
    def _extract_word_aligned_segments(
        cls, seg: Any, detected_lang: str, avg_lp: float
    ) -> List[Dict[str, Any]]:
        """
        Căn chỉnh mốc `start` và `end` chính xác đến từng mili-giây theo `seg.words` (Word-Level Timestamp Alignment):
        - Tách các cụm từ nếu giữa 2 từ có khoảng lặng lớn (> 0.85s) để không bao giờ gộp 20-30s nhạc nền vào câu thoại.
        - Loại bỏ các từ nhiễu đơn lẻ (1 chữ có xác suất thấp hoặc nằm cách cụm câu chính > 1.0s như '早[95.59s]' cách '点想回家[127.20s]' 31 giây).
        - Cắt gọn phần nhạc dạo bị dính vào từ đầu tiên (nếu 1 chữ kéo dài bất thường > 0.75s).
        """
        words = list(getattr(seg, "words", None) or [])
        raw_text = cls._deduplicate_internal_clauses((seg.text or "").strip())
        if not raw_text or cls._is_meaningless_filler_text(raw_text):
            return []

        if not words:
            st = round(float(seg.start), 3)
            en = round(max(st + 0.45, float(seg.end)), 3)
            return [
                {
                    "start": st,
                    "end": en,
                    "text": raw_text,
                    "language": detected_lang,
                    "avg_logprob": round(avg_lp, 3),
                }
            ]

        # Gom các từ thành từng cụm liên tục (tách cụm nếu khoảng nghỉ giữa 2 từ > 0.85s)
        clusters: List[List[Any]] = []
        for w in words:
            w_txt = (getattr(w, "word", "") or "").strip()
            if not w_txt:
                continue
            if not clusters:
                clusters.append([w])
            else:
                prev_w = clusters[-1][-1]
                gap = float(w.start) - float(prev_w.end)
                if gap > 0.85:
                    clusters.append([w])
                else:
                    clusters[-1].append(w)

        if not clusters:
            return []

        # Nếu có nhiều cụm do khoảng lặng > 0.85s:
        # Kiểm tra xem có cụm nào chỉ là 1 chữ nhiễu (outlier) nằm cách xa cụm chính hay không
        if len(clusters) > 1:
            cluster_char_Lens = [
                len(re.sub(r"\s+", "", "".join((getattr(x, "word", "") or "") for x in c)))
                for c in clusters
            ]
            max_c_len = max(cluster_char_Lens)
            # Nếu chỉ có 1 cụm chính (>= 2 chữ) và các cụm kia chỉ có 1 chữ đơn lẻ (như '早' + gap 31s + '点想回家' hoặc '张女' + gap 3.8s + '士'):
            # Gắn toàn bộ câu thoại vào mốc thời gian thực của cụm chính!
            major_clusters = [c for c, l in zip(clusters, cluster_char_Lens) if l >= 2]
            if len(major_clusters) == 1 and max_c_len >= 2:
                main_c = major_clusters[0]
                w0_st = float(main_c[0].start)
                w0_en = float(main_c[0].end)
                w0_chars = max(1, len((getattr(main_c[0], "word", "") or "").strip()))
                if (w0_en - w0_st) > 0.65 * w0_chars:
                    w0_st = max(w0_st, w0_en - 0.42 * w0_chars)
                last_en = float(main_c[-1].end)
                # Cộng bù nhẹ 0.18s cho chữ đơn bị tách ở đầu/cuối nhưng không vượt quá biên câu
                if clusters[0] is not main_c:
                    w0_st = max(0.0, w0_st - 0.18)
                if clusters[-1] is not main_c:
                    last_en = last_en + 0.22
                if last_en <= w0_st:
                    last_en = w0_st + 0.50
                return [
                    {
                        "start": round(w0_st, 3),
                        "end": round(last_en, 3),
                        "text": raw_text,
                        "language": detected_lang,
                        "avg_logprob": round(avg_lp, 3),
                    }
                ]

        # Xử lý từng cụm từ hợp lệ
        out_segments: List[Dict[str, Any]] = []
        use_full_text = len(clusters) == 1
        for c in clusters:
            c_text = raw_text if use_full_text else "".join((getattr(x, "word", "") or "") for x in c).strip()
            c_text = cls._deduplicate_internal_clauses(c_text)
            if not c_text or cls._is_meaningless_filler_text(c_text):
                continue
            w0_st = float(c[0].start)
            w0_en = float(c[0].end)
            w0_chars = max(1, len((getattr(c[0], "word", "") or "").strip()))
            # Nếu từ đầu tiên bị kéo giãn bất thường do dính nhạc dạo phía trước (> 0.65s/chữ), cắt sát về đúng điểm phát âm
            if (w0_en - w0_st) > 0.65 * w0_chars:
                w0_st = max(w0_st, w0_en - 0.42 * w0_chars)

            last_st = float(c[-1].start)
            last_en = float(c[-1].end)
            last_chars = max(1, len((getattr(c[-1], "word", "") or "").strip()))
            if (last_en - last_st) > 0.75 * last_chars:
                last_en = min(last_en, last_st + 0.50 * last_chars)

            if last_en <= w0_st:
                last_en = w0_st + 0.45

            out_segments.append(
                {
                    "start": round(w0_st, 3),
                    "end": round(last_en, 3),
                    "text": c_text,
                    "language": detected_lang,
                    "avg_logprob": round(avg_lp, 3),
                }
            )
        return out_segments

    @staticmethod
    def _clean_ocr_line(raw_ocr: str) -> str:
        """
        Làm sạch chuỗi OCR khỏi ký hiệu rác, dòng chú thích bối cảnh trong ngoặc `（...）` / `【...】`,
        chữ in trên áo/biển hiệu/tờ rơi, tên kênh watermark và chuẩn hóa chữ gần nét.
        """
        if not raw_ocr:
            return ""
        text = raw_ocr.strip()
        # Loại bỏ hoàn toàn các dòng chú thích bối cảnh/tiêu đề tĩnh nằm trong ngoặc đơn/ngoặc vuông trên màn hình (kể cả khi bị ngắt dòng mất 1 bên ngoặc)
        text = re.sub(r"（[^）]*）|\([^)]*\)|【[^】]*】|\[[^\]]*\]", " ", text)
        text = re.sub(r"（[^）]*|^[^（]*）|【[^】]*|^[^【]*】", " ", text)
        # Loại bỏ watermark @tên_kênh và các cụm ký tự Latin/chữ số/ký hiệu rác (như số điện thoại, tờ rơi, biển hiệu)
        text = re.sub(r"@[A-Za-z0-9_\u4e00-\u9fff]+", "", text)
        if re.search(r"\d{3,}", text):
            return ""
        text = re.sub(r"[A-Za-z$¢≥=®©™&*+#_/<>②⑥⑨｛｝{}]+", " ", text)
        text = text.replace("“", "“").replace("”", "”").replace('"', "”")
        for wrong, right in OCR_LOOKALIKE_FIXES:
            text = text.replace(wrong, right)
        if text.count("“") != text.count("”"):
            text = text.replace("“", "").replace("”", "")
        text = re.sub(r"\s+", " ", text).strip(" ，,。.!！?？:：;；-—'’、")
        # Chỉ chấp nhận chuỗi phụ đề tiếng Trung thực sự: có ít nhất 2 chữ Hán, không quá 22 chữ, và tỷ lệ chữ Hán >= 78%
        hanzi_chars = re.findall(r"[\u4e00-\u9fff]", text)
        non_space = [c for c in text if not c.isspace()]
        if (
            len(hanzi_chars) < 2
            or len(hanzi_chars) > 22
            or (non_space and len(hanzi_chars) / len(non_space) < 0.78)
        ):
            return ""
        return text

    def _refine_chinese_segments_with_vision_ocr(
        self,
        video_path: Path,
        segments: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], float]:
        """
        Sử dụng Apple Vision (`VNRecognizeTextRequest` với `zh-Hans`) để đọc phụ đề tiếng Trung
        tại đúng tâm thời gian của từng câu thoại, CHỈ dùng để sửa lỗi đồng âm (homophones) của Whisper:
        - Mỗi segment chỉ lấy 1 khung hình chính giữa (không bao giờ ghép 2 khung hình gây lặp câu 2 lần).
        - Tách riêng từng dòng OCR (`|||`), loại bỏ chữ tĩnh xuất hiện lặp lại ở nhiều cảnh (Static Banner Filter).
        - Chỉ thay thế khi độ dài chữ Hán của dòng phụ đề OCR khớp sát với câu nói Whisper (`w_text`)
          và có độ tương đồng cấu trúc/ký tự hợp lệ, tuyệt đối không đọc nhầm tờ rơi/biển báo trên hình.
        """
        try:
            ffmpeg_bin, _ = ensure_ffmpeg()
            with tempfile.TemporaryDirectory() as tmp_dir:
                img_paths: List[str] = []
                for idx, seg in enumerate(segments):
                    st = float(seg["start"])
                    en = float(seg["end"])
                    mid_t = st + max(0.1, (en - st) * 0.50)
                    jpg_path = f"{tmp_dir}/sub_{idx:04d}.jpg"
                    subprocess.run(
                        [
                            ffmpeg_bin,
                            "-y",
                            "-v",
                            "error",
                            "-ss",
                            f"{mid_t:.2f}",
                            "-i",
                            str(video_path),
                            "-frames:v",
                            "1",
                            "-vf",
                            "crop=iw*0.88:ih*0.28:iw*0.06:ih*0.64",
                            jpg_path,
                        ],
                        check=False,
                        timeout=8,
                    )
                    img_paths.append(jpg_path)

                if not img_paths:
                    return segments, 0.0

                swift_script = """
import Foundation
import Vision
import AppKit

let args = Array(CommandLine.arguments.dropFirst())
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["zh-Hans", "en-US"]
req.usesLanguageCorrection = true

for path in args {
    guard let img = NSImage(contentsOfFile: path),
          let cgImg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("")
        continue
    }
    let handler = VNImageRequestHandler(cgImage: cgImg, options: [:])
    try? handler.perform([req])
    let texts = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    print(texts.joined(separator: " ||| "))
}
"""
                res = subprocess.run(
                    ["swift", "-e", swift_script] + img_paths,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=35,
                )
                if res.returncode != 0:
                    return segments, 0.0

                ocr_raw_rows = res.stdout.splitlines()

            # Tách từng dòng quan sát độc lập trên mỗi khung hình
            candidates_per_seg: List[List[str]] = []
            line_freq: Dict[str, int] = {}
            for row_idx in range(len(segments)):
                raw_row = ocr_raw_rows[row_idx] if row_idx < len(ocr_raw_rows) else ""
                sub_lines: List[str] = []
                for piece in raw_row.split("|||"):
                    cleaned = self._clean_ocr_line(piece)
                    if cleaned and cleaned not in sub_lines:
                        sub_lines.append(cleaned)
                candidates_per_seg.append(sub_lines)
                for sl in sub_lines:
                    line_freq[sl] = line_freq.get(sl, 0) + 1

            refined: List[Dict[str, Any]] = []
            ocr_hits = 0
            for idx, (seg, cand_lines) in enumerate(zip(segments, candidates_per_seg)):
                w_text = (seg.get("text") or "").strip()
                w_hanzi = "".join(re.findall(r"[\u4e00-\u9fff]", w_text))
                w_len = len(w_hanzi)

                best_ocr = ""
                best_score = -1.0

                for c_line in cand_lines:
                    # Bỏ qua các dòng tiêu đề tĩnh treo cố định trên màn hình suốt >= 3 câu thoại
                    if line_freq.get(c_line, 0) >= 3:
                        continue
                    c_hanzi = "".join(re.findall(r"[\u4e00-\u9fff]", c_line))
                    c_len = len(c_hanzi)
                    if c_len < 2 or w_len < 2:
                        continue
                    # Ràng buộc độ dài chặt chẽ: dòng phụ đề OCR phải có số chữ Hán tương đương câu nói Whisper
                    min_allowed = max(2, int(w_len * 0.65) - 1)
                    max_allowed = max(w_len + 2, int(w_len * 1.25))
                    if not (min_allowed <= c_len <= max_allowed):
                        continue

                    char_sim = difflib.SequenceMatcher(None, w_hanzi, c_hanzi).ratio()
                    len_sim = 1.0 - (abs(c_len - w_len) / float(max(c_len, w_len, 1)))
                    # Yêu cầu câu phụ đề phải có ít nhất 25% chữ Hán trùng với Whisper (hoặc trùng khít số chữ Hán và >= 1 chữ chung)
                    common_chars = set(w_hanzi) & set(c_hanzi)
                    if char_sim < 0.25 and not (c_len == w_len and len(common_chars) >= 1):
                        continue

                    score = char_sim * 0.7 + len_sim * 0.3
                    if score > best_score:
                        best_score = score
                        best_ocr = c_line

                if best_ocr:
                    ocr_hits += 1
                    for interj in LEADING_SPOKEN_INTERJECTIONS:
                        if w_text.startswith(interj) and not best_ocr.startswith(interj):
                            if interj not in best_ocr[:4]:
                                best_ocr = f"{interj}，{best_ocr}"
                            break
                    new_seg = dict(seg)
                    new_seg["whisper_raw_text"] = w_text
                    new_seg["text"] = self._deduplicate_internal_clauses(best_ocr)
                    refined.append(new_seg)
                else:
                    refined.append(dict(seg))

            coverage = ocr_hits / len(segments) if segments else 0.0
            logger.info(
                f"[Vision OCR Refiner] Đã đối chiếu & chuẩn hóa đồng âm cho {ocr_hits}/{len(segments)} câu ({coverage:.0%})."
            )
            return refined, coverage
        except Exception as exc:
            logger.debug(f"Vision OCR refinement skipped: {exc}")
            return segments, 0.0

    @classmethod
    def _merge_short_clauses(
        cls, segments: List[Dict[str, Any]], detected_lang: str
    ) -> List[Dict[str, Any]]:
        """
        Khử triệt để các câu bị lặp / nói lắp liên tiếp và gộp các mệnh đề ngắn liền kề (gap <= 0.40s)
        thành câu có ngữ pháp hoàn chỉnh:
        - Nếu 2 câu liên tiếp có nội dung trùng lặp (`ratio >= 0.72` hoặc câu ngắn nằm trọn trong câu liền kề),
          gộp lại thành 1 câu duy nhất, không bao giờ đọc lặp 2 lần.
        - Tránh tình trạng câu tiếng Trung bị cắt đôi giữa định ngữ và danh từ (`...的`).
        """
        if not segments:
            return []

        merged: List[Dict[str, Any]] = []
        incomplete_endings_zh = (
            "于",
            "的",
            "把",
            "被",
            "在",
            "当",
            "跟",
            "和",
            "却",
            "但",
            "而是",
            "因为",
            "所以",
            "如果",
            "连",
            "知道",
            "写着",
            "写着：",
            "成了",
        )

        for seg in segments:
            cur_raw = cls._deduplicate_internal_clauses((seg.get("text") or "").strip())
            if not cur_raw or cls._is_meaningless_filler_text(cur_raw):
                continue
            seg_copy = dict(seg)
            seg_copy["text"] = cur_raw

            if not merged:
                merged.append(seg_copy)
                continue

            prev = merged[-1]
            gap = float(seg_copy["start"]) - float(prev["end"])
            prev_dur = float(prev["end"]) - float(prev["start"])
            cur_dur = float(seg_copy["end"]) - float(seg_copy["start"])
            comb_dur = float(seg_copy["end"]) - float(prev["start"])

            prev_txt = (prev.get("text") or "").strip().rstrip("，,。.!！?？ ")
            cur_txt = (seg_copy.get("text") or "").strip().lstrip("，,。.!！?？ ")
            prev_clean = re.sub(r"[\s，,。.!！?？]+", "", prev_txt)
            cur_clean = re.sub(r"[\s，,。.!！?？]+", "", cur_txt)

            # 1. Khử câu trùng lặp hoặc nói lắp ở 2 segment liên tiếp (ví dụ: '张女士' + '张女士' hoặc '我' + '我女儿丢了')
            if prev_clean and cur_clean and gap <= 2.2:
                sim = difflib.SequenceMatcher(None, prev_clean, cur_clean).ratio()
                is_sub_stutter = (
                    (len(prev_clean) <= 4 and cur_clean.startswith(prev_clean))
                    or (len(cur_clean) <= 4 and prev_clean.endswith(cur_clean))
                    or (len(cur_clean) >= 4 and cur_clean in prev_clean)
                    or (len(prev_clean) >= 4 and prev_clean in cur_clean)
                )
                if sim >= 0.72 or is_sub_stutter:
                    # Giữ câu đầy đủ hơn và cập nhật mốc thời gian chuẩn
                    if len(cur_clean) > len(prev_clean) and len(prev_clean) <= 2:
                        prev["start"] = seg_copy["start"]
                    prev["end"] = seg_copy["end"]
                    if len(cur_txt) > len(prev_txt):
                        prev["text"] = cur_txt
                    continue

            ends_incomplete = detected_lang.startswith("zh") and any(
                prev_txt.endswith(sfx) for sfx in incomplete_endings_zh
            )
            should_merge = False
            if gap <= 0.42 and comb_dur <= 4.3:
                if prev_dur < 1.30 or cur_dur < 1.10 or ends_incomplete:
                    should_merge = True
            elif ends_incomplete and gap <= 0.60 and comb_dur <= 5.2:
                should_merge = True

            if should_merge:
                prev["end"] = seg_copy["end"]
                sep = "，" if detected_lang.startswith("zh") else ", "
                if cur_clean not in prev_clean:
                    prev["text"] = cls._deduplicate_internal_clauses(f"{prev_txt}{sep}{cur_txt}")
            else:
                merged.append(seg_copy)

        return merged
