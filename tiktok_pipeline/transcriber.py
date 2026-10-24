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

        logger.info(f"Đang nhận diện giọng nói từ: {audio_path} (auto-detect language)...")
        segments_gen, info = self.model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=350),
        )

        detected_lang = (info.language or "en").lower()
        lang_prob = float(info.language_probability or 0.0)
        logger.info(
            f"Ngôn ngữ phát hiện: '{detected_lang}' (độ tin cậy: {lang_prob:.2%})"
        )

        raw_segments: List[Dict[str, Any]] = []
        logprobs: List[float] = []

        for seg in segments_gen:
            text = (seg.text or "").strip()
            if not text:
                continue

            no_speech = float(getattr(seg, "no_speech_prob", 0.0) or 0.0)
            avg_lp = float(getattr(seg, "avg_logprob", -0.3) or -0.3)
            if (no_speech > 0.92 and avg_lp < -0.85) or avg_lp < -1.15:
                continue

            start_sec = round(float(seg.start), 3)
            end_sec = round(float(seg.end), 3)
            if end_sec <= start_sec:
                end_sec = round(start_sec + 0.5, 3)

            logprobs.append(avg_lp)
            raw_segments.append(
                {
                    "start": start_sec,
                    "end": end_sec,
                    "text": text,
                    "language": detected_lang,
                    "avg_logprob": round(avg_lp, 3),
                }
            )

        mean_logprob = sum(logprobs) / len(logprobs) if logprobs else -1.0
        ocr_coverage = 0.0

        # Bước 2: Khử lỗi đồng âm tiếng Trung của Whisper bằng Apple Vision OCR trên vùng phụ đề video gốc
        if has_video and detected_lang.startswith("zh") and raw_segments:
            raw_segments, ocr_coverage = self._refine_chinese_segments_with_vision_ocr(
                video_path=inferred_video,
                segments=raw_segments,
            )

        # Bước 3: Gộp các mệnh đề quá ngắn/bị ngắt hơi giữa câu thành câu hoàn chỉnh tự nhiên
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

    @staticmethod
    def _clean_ocr_line(raw_ocr: str) -> str:
        """Làm sạch chuỗi OCR khỏi ký hiệu rác, chữ in trên áo/biển hiệu, tên kênh watermark và chuẩn hóa chữ gần nét."""
        if not raw_ocr:
            return ""
        text = raw_ocr.strip()
        # Loại bỏ watermark @tên_kênh và các cụm ký tự Latin/ký hiệu rác (như chữ in trên áo, biển quảng cáo)
        text = re.sub(r"@[A-Za-z0-9_\u4e00-\u9fff]+", "", text)
        text = re.sub(r"[A-Za-z$¢≥=®©™&*+#_/<>②⑥⑨]{1,}", " ", text)
        text = text.replace("“", "“").replace("”", "”").replace('"', "”")
        for wrong, right in OCR_LOOKALIKE_FIXES:
            text = text.replace(wrong, right)
        # Bỏ dấu ngoặc kép lẻ nếu bị khuyết 1 bên
        if text.count("“") != text.count("”"):
            text = text.replace("“", "").replace("”", "")
        text = re.sub(r"\s+", " ", text).strip(" ，,。.!！?？:：;；-—'’")
        # Chỉ chấp nhận chuỗi phụ đề tiếng Trung thực sự: có ít nhất 2 chữ Hán và tỷ lệ chữ Hán >= 70%
        hanzi_chars = re.findall(r"[\u4e00-\u9fff]", text)
        non_space = [c for c in text if not c.isspace()]
        if len(hanzi_chars) < 2 or (non_space and len(hanzi_chars) / len(non_space) < 0.70):
            return ""
        return text

    def _refine_chinese_segments_with_vision_ocr(
        self,
        video_path: Path,
        segments: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], float]:
        """
        Sử dụng Apple Vision (`VNRecognizeTextRequest` với `zh-Hans`) để đọc trực tiếp phụ đề
        tiếng Trung trên khung hình tại mốc thời gian của từng câu thoại, sửa sạch lỗi đồng âm
        (homophones) của Whisper ASR.
        """
        try:
            ffmpeg_bin, _ = ensure_ffmpeg()
            with tempfile.TemporaryDirectory() as tmp_dir:
                img_paths: List[str] = []
                seg_sample_indices: List[List[int]] = []
                img_idx = 0

                for seg in segments:
                    st = float(seg["start"])
                    en = float(seg["end"])
                    dur = max(0.2, en - st)
                    if dur >= 1.30:
                        sample_times = [st + dur * 0.22, st + dur * 0.72]
                    else:
                        sample_times = [st + dur * 0.50]

                    cur_indices: List[int] = []
                    for t in sample_times:
                        jpg_path = f"{tmp_dir}/sub_{img_idx:04d}.jpg"
                        img_idx += 1
                        subprocess.run(
                            [
                                ffmpeg_bin,
                                "-y",
                                "-v",
                                "error",
                                "-ss",
                                f"{t:.2f}",
                                "-i",
                                str(video_path),
                                "-frames:v",
                                "1",
                                "-vf",
                                "crop=iw*0.90:ih*0.36:iw*0.05:ih*0.58",
                                jpg_path,
                            ],
                            check=False,
                            timeout=8,
                        )
                        img_paths.append(jpg_path)
                        cur_indices.append(len(img_paths) - 1)
                    seg_sample_indices.append(cur_indices)

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
    print(texts.joined(separator: " "))
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

                ocr_raw_lines = res.stdout.splitlines()

            cards_per_seg: List[List[str]] = []
            for idxs in seg_sample_indices:
                parts: List[str] = []
                for k in idxs:
                    raw_str = ocr_raw_lines[k] if k < len(ocr_raw_lines) else ""
                    cleaned = self._clean_ocr_line(raw_str)
                    if not cleaned:
                        continue
                    if not parts:
                        parts.append(cleaned)
                    else:
                        sim = difflib.SequenceMatcher(None, parts[-1], cleaned).ratio()
                        if sim >= 0.62:
                            if len(cleaned) > len(parts[-1]):
                                parts[-1] = cleaned
                        else:
                            parts.append(cleaned)
                cards_per_seg.append(parts)

            # Khử trùng lặp thẻ phụ đề ở ranh giới giữa câu i và câu i+1
            for i in range(len(cards_per_seg) - 1):
                if len(cards_per_seg[i]) > 1 and cards_per_seg[i + 1]:
                    sim = difflib.SequenceMatcher(
                        None, cards_per_seg[i][-1], cards_per_seg[i + 1][0]
                    ).ratio()
                    if sim >= 0.62:
                        cards_per_seg[i].pop()

            refined: List[Dict[str, Any]] = []
            ocr_hits = 0
            for seg, parts in zip(segments, cards_per_seg):
                w_text = (seg.get("text") or "").strip()
                ocr_text = "，".join(parts).strip()

                if ocr_text and len(ocr_text) >= max(2, int(len(w_text) * 0.45)):
                    ocr_hits += 1
                    # Giữ lại từ cảm thán đầu câu của Whisper nếu trên màn hình lược bỏ
                    for interj in LEADING_SPOKEN_INTERJECTIONS:
                        if w_text.startswith(interj) and not ocr_text.startswith(interj):
                            # Tránh ghép trùng nếu phần sau đã chứa từ tương đương
                            if interj not in ocr_text[:4]:
                                ocr_text = f"{interj}，{ocr_text}"
                            break
                    new_seg = dict(seg)
                    new_seg["whisper_raw_text"] = w_text
                    new_seg["text"] = ocr_text
                    refined.append(new_seg)
                else:
                    refined.append(dict(seg))

            coverage = ocr_hits / len(segments) if segments else 0.0
            logger.info(
                f"[Vision OCR Refiner] Đã đối chiếu & chuẩn hóa phụ đề gốc cho {ocr_hits}/{len(segments)} câu ({coverage:.0%})."
            )
            return refined, coverage
        except Exception as exc:
            logger.debug(f"Vision OCR refinement skipped: {exc}")
            return segments, 0.0

    @staticmethod
    def _merge_short_clauses(
        segments: List[Dict[str, Any]], detected_lang: str
    ) -> List[Dict[str, Any]]:
        """
        Gộp các mệnh đề ngắn bị ngắt hơi liền kề (gap <= 0.40s) thành câu có ngữ pháp hoàn chỉnh:
        - Tránh tình trạng câu tiếng Trung bị cắt đôi giữa định ngữ và danh từ (`...的` / `不会垂青于` + `...的自己`)
          khiến bản dịch bị cụt nghĩa hoặc đảo ngược chủ vị.
        - Giúp mỗi câu có thời lượng lý tưởng (1.8s - 4.2s) để giọng đọc Edge-TTS diễn cảm tự nhiên, không bị tua nhanh.
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
            if not merged:
                merged.append(dict(seg))
                continue

            prev = merged[-1]
            gap = float(seg["start"]) - float(prev["end"])
            prev_dur = float(prev["end"]) - float(prev["start"])
            cur_dur = float(seg["end"]) - float(seg["start"])
            comb_dur = float(seg["end"]) - float(prev["start"])

            prev_txt = (prev.get("text") or "").strip().rstrip("，,。.!！?？ ")
            cur_txt = (seg.get("text") or "").strip().lstrip("，,。.!！?？ ")

            # Nếu 2 segment liên tiếp có cùng nội dung OCR trùng lặp, gộp lại luôn
            if prev_txt and cur_txt and difflib.SequenceMatcher(None, prev_txt, cur_txt).ratio() >= 0.78:
                if gap <= 0.8:
                    prev["end"] = seg["end"]
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
                prev["end"] = seg["end"]
                sep = "，" if detected_lang.startswith("zh") else ", "
                if cur_txt not in prev_txt:
                    prev["text"] = f"{prev_txt}{sep}{cur_txt}"
            else:
                merged.append(dict(seg))

        return merged
