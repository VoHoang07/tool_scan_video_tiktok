import os
import subprocess
import textwrap
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .utils import (
    ensure_ffmpeg,
    format_ass_timestamp,
    format_srt_timestamp,
    get_media_duration,
    get_video_resolution,
    logger,
)

# Cấu hình giọng đọc Edge-TTS chuyên biệt theo từng kênh (Quy mô 10 kênh)
CHANNEL_VOICE_PROFILES: Dict[str, Dict[str, Any]] = {
    "channel_1": {
        "voice": "vi-VN-HoaiMyNeural",
        "base_rate": "+0%",
        "pitch": "+4Hz",
        "encoder_sig": "CapCut 14.4.0 (iOS 17.5.1)",
        "description": "Tông giọng đáng yêu, hài hước, sinh động (AI Hoạt họa / Thú cưng / Kể chuyện)",
    },
    "channel_2": {
        "voice": "vi-VN-HoaiMyNeural",
        "base_rate": "+0%",
        "pitch": "+2Hz",
        "encoder_sig": "CapCut 14.2.0 (iPhone16,2)",
        "description": "Giọng tự nhiên, đời thường, tếu táo, khớp nhịp giọng gốc",
    },
    "channel_3": {
        "voice": "vi-VN-NamMinhNeural",
        "base_rate": "+0%",
        "pitch": "-3Hz",
        "encoder_sig": "VN Video Editor 2.24.1 (iOS)",
        "description": "Giọng đọc kịch tính, nhịp điệu cuốn hút (Truyện Tổng Tài / Trùng Sinh)",
    },
    "channel_4": {
        "voice": "vi-VN-NamMinhNeural",
        "base_rate": "+0%",
        "pitch": "-4Hz",
        "encoder_sig": "Adobe Premiere Pro 2024.5 (macOS)",
        "description": "Giọng nam trầm, hào hùng, tốc độ chuẩn rate +0% (Kênh Tu Tiên / Huyền Huyễn)",
    },
    "channel_5": {
        "voice": "vi-VN-HoaiMyNeural",
        "base_rate": "+4%",
        "pitch": "+1Hz",
        "encoder_sig": "CapCut 14.6.0 (SM-S928B)",
        "description": "Giọng nữ nhanh nhẹn, rõ ràng, thực tế (Mẹo vặt nhà cửa & Gia dụng thông minh)",
    },
    "channel_6": {
        "voice": "vi-VN-HoaiMyNeural",
        "base_rate": "+0%",
        "pitch": "-1Hz",
        "encoder_sig": "InShot 1.88.2 (iOS 17.6)",
        "description": "Giọng nữ điềm đạm, ấm áp, đáng tin cậy (Sức khỏe chủ động & Dưỡng sinh)",
    },
    "channel_7": {
        "voice": "vi-VN-HoaiMyNeural",
        "base_rate": "+0%",
        "pitch": "-2Hz",
        "encoder_sig": "CapCut 14.5.0 (iPhone15,3)",
        "description": "Giọng nữ sâu sắc, truyền cảm, tinh tế (Tâm lý hôn nhân & Phụ nữ khí chất)",
    },
    "channel_8": {
        "voice": "vi-VN-NamMinhNeural",
        "base_rate": "+2%",
        "pitch": "-1Hz",
        "encoder_sig": "DaVinci Resolve 19.0.1",
        "description": "Giọng nam đĩnh đạc, sắc bén (Chuyện công sở & Bài học kinh doanh)",
    },
    "channel_9": {
        "voice": "vi-VN-HoaiMyNeural",
        "base_rate": "+0%",
        "pitch": "+1Hz",
        "encoder_sig": "VLLO 10.6.4 (iPadOS 17.5)",
        "description": "Giọng nữ dịu dàng, tâm lý (Nuôi dạy con thông minh & EQ cho bé)",
    },
    "channel_10": {
        "voice": "vi-VN-NamMinhNeural",
        "base_rate": "+0%",
        "pitch": "-5Hz",
        "encoder_sig": "Final Cut Pro 10.8.1",
        "description": "Giọng nam trầm mặc, uy nghiêm (Cổ học tinh hoa & Mưu lược nhân sinh)",
    },
    "general": {
        "voice": "vi-VN-NamMinhNeural",
        "base_rate": "+0%",
        "pitch": "-2Hz",
        "encoder_sig": "CapCut 14.3.0",
        "description": "Giọng kể chuyện truyền cảm mặc định",
    },
}


class VideoComposer:
    """
    Sử dụng `ffmpeg` để:
    1. Tinh chỉnh cấu hình giọng đọc `edge-tts` theo từng kênh (`get_channel_voice_config`).
    2. Audio Ducking: Giảm âm lượng video gốc xuống 12% (`volume=0.12`) giữ SFX/nhạc nền,
       đè giọng đọc AI tiếng Việt lên với âm lượng 120% (`volume=1.2`).
    3. Lắp ghép âm thanh tiếng Việt mới vào đúng mốc thời gian (timestamp).
    4. Tạo file phụ đề (.srt / .ass) với Background Box mờ nhẹ + viền đen dày (Outline=3, Shadow=1.5),
       MarginV=140px che sạch chữ tiếng Trung gốc và burn lên video hoàn chỉnh.
    """

    @staticmethod
    def get_channel_voice_config(channel: Optional[str] = None) -> Dict[str, Any]:
        """
        Trả về cấu hình giọng đọc `edge-tts` (voice, base_rate, pitch) theo từng kênh:
        - Kênh 1 (AI Hoạt họa / Thú cưng / Kể chuyện): Tông giọng đáng yêu, hài hước, sinh động.
        - Kênh 2 (Hài / Đời sống / Gia đình): Tếu táo, nhanh nhẹn (`rate: +10%`).
        - Kênh 3 (Truyện Tổng Tài / Trùng Sinh): Nhịp điệu kịch tính, cuốn hút.
        - Kênh 4 (Tu Tiên / Huyền Huyễn): `vi-VN-NamMinhNeural` (giọng nam trầm, hào hùng), `rate: +0%`.
        """
        ch = (channel or "general").lower().strip()
        alias_map = {
            "video_ai_hay": "channel_1",
            "baby_ai": "channel_2",
            "audio_dem_khuyen": "channel_3",
            "tu_chan_gioi": "channel_4",
            "meo_vat_gia_dinh": "channel_5",
            "suc_khoe_duong_sinh": "channel_6",
            "phu_nu_khi_chat": "channel_7",
            "bai_hoc_kinh_doanh": "channel_8",
            "nuoi_con_eq": "channel_9",
            "muu_luoc_nhan_sinh": "channel_10",
        }
        if ch in alias_map:
            return dict(CHANNEL_VOICE_PROFILES[alias_map[ch]])
        for idx in range(10, 0, -1):
            key = f"channel_{idx}"
            if key in ch or ch == str(idx):
                return dict(CHANNEL_VOICE_PROFILES[key])
        return dict(CHANNEL_VOICE_PROFILES["general"])

    def __init__(
        self,
        work_dir: Path,
        output_dir: Path,
        original_volume: Optional[float] = None,
        voiceover_volume: float = 1.35,
    ):
        self.work_dir = Path(work_dir)
        self.output_dir = Path(output_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        env_vol = float(os.getenv("ORIGINAL_AUDIO_VOLUME", "0.25"))
        if env_vol < 0.15:
            # Giữ nguyên giọng biểu cảm của nhân vật gốc ở mức nền vừa phải (~25%) để lồng tiếng Việt đè lên tự nhiên
            env_vol = 0.25
        self.original_volume = (
            float(original_volume)
            if original_volume is not None
            else env_vol
        )
        self.voiceover_volume = float(voiceover_volume)
        self.ffmpeg_bin, _ = ensure_ffmpeg()

    def compose(
        self,
        raw_video_path: Path,
        segments: List[Dict[str, Any]],
        output_filename: str = "final_video.mp4",
    ) -> Dict[str, Path]:
        """
        Thực hiện toàn bộ quá trình ghép âm thanh tiếng Việt, audio ducking và burn phụ đề.
        Trả về đường dẫn video hoàn chỉnh và file phụ đề `.srt`.
        """
        raw_video_path = Path(raw_video_path)
        if not raw_video_path.exists():
            raise FileNotFoundError(f"Không tìm thấy video gốc: {raw_video_path}")

        video_duration = get_media_duration(raw_video_path)
        width, height = get_video_resolution(raw_video_path)
        logger.info(
            f"Thông số video gốc: {width}x{height}, thời lượng: {video_duration:.2f}s"
        )

        # Tự động dò vị trí & chiều cao dòng sub gốc (tiếng Trung) trên video để làm mờ đúng chỗ và đè sub tiếng Việt lên trên
        sub_band = self._detect_original_subtitle_band(
            raw_video_path, segments, width, height
        )
        detected_sub_y = sub_band[0] if sub_band else None
        detected_sub_h = sub_band[1] if sub_band else None

        # Bước 1: Lắp ghép các đoạn TTS tiếng Việt vào trục thời gian chuẩn (voiceover_track.wav) trước
        # để khóa chính xác tuyệt đối đến từng mili-giây giữa giọng đọc và phụ đề hiển thị
        voiceover_wav = self.work_dir / "voiceover_track.wav"
        self.build_voiceover_timeline(segments, video_duration, voiceover_wav)

        # Bước 2: Xuất file phụ đề tiếng Việt (.srt và .ass) khớp 100% với vị trí sample thực tế của giọng đọc
        srt_path = self.work_dir / "subtitles_vi.srt"
        ass_path = self.work_dir / "subtitles_vi.ass"
        self.generate_srt(segments, srt_path)
        self.generate_ass(
            segments,
            ass_path,
            width,
            height,
            detected_sub_y=detected_sub_y,
            detected_sub_h=detected_sub_h,
        )

        # Bước 3: Trộn âm thanh gốc (đã giảm âm lượng / ducking) với âm thanh lồng tiếng Việt
        dubbed_audio_aac = self.work_dir / "mixed_audio.m4a"
        self.mix_audio_tracks(raw_video_path, voiceover_wav, dubbed_audio_aac)

        # Bước 4: Làm mờ chữ sub gốc và burn phụ đề tiếng Việt đè trực tiếp lên vị trí đó
        final_video_path = self.output_dir / output_filename

        self.burn_subtitles_and_mux(
            raw_video_path=raw_video_path,
            mixed_audio_path=dubbed_audio_aac,
            ass_path=ass_path,
            segments=segments,
            width=width,
            height=height,
            output_video_path=final_video_path,
            detected_sub_y=detected_sub_y,
            detected_sub_h=detected_sub_h,
        )

        logger.info(f"Hoàn tất xuất bản video tại: {final_video_path}")
        return {
            "final_video": final_video_path,
            "subtitle_srt": srt_path,
        }

    def _detect_original_subtitle_band(
        self,
        raw_video_path: Path,
        segments: List[Dict[str, Any]],
        width: int,
        height: int,
    ) -> Optional[Tuple[int, int]]:
        """
        Trích xuất nhiều khung hình tại các mốc thoại khác nhau để tự động phát hiện chính xác
        tọa độ tâm Y (`sub_y`) và chiều cao (`sub_h`) của dòng phụ đề gốc (tiếng Trung / Anh)
        dựa trên mật độ nét chữ ngang và độ biến thiên nét chữ theo thời gian.
        Trả về `(sub_y, sub_h)` hoặc `None` nếu video không có sub cứng.
        """
        if not segments or height <= 0 or width <= 0:
            return None
        try:
            import numpy as np

            y0, y1 = int(height * 0.38), int(height * 0.94)
            x0, x1 = int(width * 0.12), int(width * 0.88)
            if y1 <= y0 or x1 <= x0:
                return None

            idxs = np.linspace(0, len(segments) - 1, min(9, len(segments)), dtype=int)
            rois: List[Any] = []
            probe_jpg = self.work_dir / "_sub_probe.jpg"

            for idx in idxs:
                seg = segments[int(idx)]
                sample_t = (float(seg["start"]) + float(seg["end"])) / 2.0
                cmd = [
                    self.ffmpeg_bin,
                    "-y",
                    "-ss",
                    f"{sample_t:.2f}",
                    "-i",
                    str(raw_video_path),
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    str(probe_jpg),
                ]
                subprocess.run(cmd, capture_output=True, check=False)
                if probe_jpg.exists():
                    im = Image.open(probe_jpg).convert("L")
                    arr = np.asarray(im, dtype=np.float32)
                    rois.append(arr[y0:y1, x0:x1])
                    probe_jpg.unlink(missing_ok=True)

            if not rois:
                return None

            stack = np.stack(rois, axis=0)
            dx = np.abs(np.diff(stack, axis=2))
            padded_max = np.pad(stack, ((0, 0), (0, 0), (4, 4)), mode="edge")
            padded_min = np.pad(stack, ((0, 0), (0, 0), (4, 4)), mode="edge")
            loc_max = np.maximum.reduce(
                [padded_max[:, :, i : i + stack.shape[2]] for i in range(9)]
            )
            loc_min = np.minimum.reduce(
                [padded_min[:, :, i : i + stack.shape[2]] for i in range(9)]
            )
            contrast_mask = (
                (loc_max > 175) & (loc_min < 85) & ((loc_max - loc_min) > 105)
            )[..., :-1]
            stroke_energy = dx * contrast_mask

            k_cols = max(4, int(stroke_energy.shape[2] * 0.25))
            top_stroke = np.sort(stroke_energy, axis=2)[:, :, -k_cols:].mean(axis=2)
            spatial_consistency = np.percentile(top_stroke, 65, axis=0)

            if len(rois) >= 2:
                tdiff = np.abs(np.diff(stroke_energy, axis=0))
                top_tdiff = np.sort(tdiff, axis=2)[:, :, -k_cols:].mean(axis=2)
                temporal_var = np.median(top_tdiff, axis=0)
                raw_score = spatial_consistency * (0.3 + temporal_var)
            else:
                raw_score = spatial_consistency

            win = max(5, int(height * 0.035))
            smooth_score = np.convolve(
                raw_score, np.ones(win, dtype=np.float32) / win, mode="same"
            )
            best_rel_y = int(np.argmax(smooth_score))
            peak_val = float(smooth_score[best_rel_y])

            # Nếu không có sub cứng (điểm biến thiên nét chữ thấp), trả về None để không làm mờ nhầm cảnh video
            if peak_val < 420.0:
                logger.info(
                    f"Không phát hiện phụ đề cứng trên video gốc (peak_score={peak_val:.1f} < 420)."
                )
                return None

            max_gap = max(4, int(height * 0.028))
            low_r = best_rel_y
            while low_r > 0:
                window_slice = smooth_score[max(0, low_r - max_gap) : low_r]
                if len(window_slice) > 0 and float(np.max(window_slice)) >= peak_val * 0.18:
                    low_r -= 1
                else:
                    break

            high_r = best_rel_y
            while high_r < len(smooth_score) - 1:
                window_slice = smooth_score[
                    high_r + 1 : min(len(smooth_score), high_r + 1 + max_gap)
                ]
                if len(window_slice) > 0 and float(np.max(window_slice)) >= peak_val * 0.18:
                    high_r += 1
                else:
                    break

            sub_y = y0 + (low_r + high_r) // 2
            sub_h = max(
                int(height * 0.085),
                min(int(height * 0.16), (high_r - low_r) + int(height * 0.05)),
            )
            sub_h = max(32, (sub_h // 2) * 2)
            logger.info(
                f"Đã phát hiện vị trí sub gốc cần làm mờ: tâm Y={sub_y}px ({sub_y / height:.1%}), "
                f"chiều cao={sub_h}px (peak_score={peak_val:.1f})"
            )
            return sub_y, sub_h
        except Exception as exc:
            logger.warning(f"Không thể dò tọa độ sub gốc tự động: {exc}")
            return None

    @staticmethod
    def generate_srt(segments: List[Dict[str, Any]], srt_path: Path) -> Path:
        """Tạo file phụ đề chuẩn SubRip (.srt) tiếng Việt."""
        lines: List[str] = []
        for idx, seg in enumerate(segments, start=1):
            start_ts = format_srt_timestamp(float(seg["start"]))
            end_ts = format_srt_timestamp(float(seg["end"]))
            text = (seg.get("translated_text") or seg.get("text") or "").strip()
            lines.append(f"{idx}\n{start_ts} --> {end_ts}\n{text}\n")

        srt_path.write_text("\n".join(lines), encoding="utf-8")
        return srt_path

    @staticmethod
    def generate_ass(
        segments: List[Dict[str, Any]],
        ass_path: Path,
        width: int = 1080,
        height: int = 1920,
        detected_sub_y: Optional[int] = None,
        detected_sub_h: Optional[int] = None,
    ) -> Path:
        """
        Tạo file phụ đề nâng cao (.ass) chỉ hiển thị chữ sắc nét (KHÔNG có khung xám/đen bao quanh)
        và đặt đè trực tiếp lên đúng tọa độ `detected_sub_y` của chữ sub gốc đã được làm mờ:
        - Font chữ dày, nét (`Arial-Bold`), chữ màu vàng tươi (`&H0000FFFF`),
          viền đen dày ôm sát từng con chữ (`BorderStyle=1`, `Outline=4`, `Shadow=2`).
        """
        if height >= width:
            play_res_x, play_res_y = 1080, 1920
            font_size = 56
            margin_h = 75
            max_chars_per_line = 26
        else:
            play_res_x, play_res_y = 1920, 1080
            font_size = 56
            margin_h = 110
            max_chars_per_line = 36

        margin_v = 140
        pos_tag = ""
        if detected_sub_y is not None and height > 0:
            cx = play_res_x // 2
            cy = int(round(detected_sub_y * (play_res_y / float(height))))
            cy = max(int(play_res_y * 0.25), min(int(play_res_y * 0.92), cy))
            pos_tag = f"{{\\an5\\pos({cx},{cy})}}"

        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial-Bold,{font_size},&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events: List[str] = []
        for seg in segments:
            start_ts = format_ass_timestamp(float(seg["start"]))
            end_ts = format_ass_timestamp(float(seg["end"]))
            raw_text = (seg.get("translated_text") or seg.get("text") or "").strip()
            wrapped = textwrap.wrap(raw_text, width=max_chars_per_line) or [raw_text]
            text = "\\N".join(wrapped)
            events.append(
                f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{pos_tag}{text}"
            )

        ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
        return ass_path

    def build_voiceover_timeline(
        self,
        segments: List[Dict[str, Any]],
        total_duration: float,
        output_wav: Path,
    ) -> Path:
        """
        Ghép tất cả các file âm thanh TTS tiếng Việt vào đúng mốc thời gian `start`
        trên một trục âm thanh chuẩn (44.1kHz, 16-bit PCM mono).
        """
        sample_rate = 44100
        total_samples = max(int(sample_rate * (total_duration + 1.0)), sample_rate)
        timeline_buffer = bytearray(total_samples * 2)  # 16-bit = 2 bytes/sample

        current_cursor_sample = 0

        for seg in segments:
            tts_file = seg.get("tts_path")
            if not tts_file or not Path(tts_file).exists():
                continue

            orig_end_sec = float(seg.get("end", seg.get("start", 0.0)))
            start_sec = max(0.0, float(seg["start"]))
            start_sample = int(round(start_sec * sample_rate))
            # Tránh đè lên câu trước nếu câu trước kéo dài hơn dự kiến một chút
            placement_sample = max(start_sample, current_cursor_sample)

            with wave.open(str(tts_file), "rb") as wf:
                n_frames = wf.getnframes()
                pcm_bytes = wf.readframes(n_frames)

            byte_offset = placement_sample * 2
            end_byte = byte_offset + len(pcm_bytes)

            if end_byte > len(timeline_buffer):
                timeline_buffer.extend(b"\x00" * (end_byte - len(timeline_buffer)))

            timeline_buffer[byte_offset:end_byte] = pcm_bytes
            end_sample = placement_sample + (len(pcm_bytes) // 2)
            current_cursor_sample = end_sample
            # Cập nhật lại chính xác mốc start/end theo vị trí PCM thực tế để phụ đề .ass khớp 100% với giọng nói
            seg["start"] = round(placement_sample / float(sample_rate), 3)
            seg["tts_end"] = round(end_sample / float(sample_rate), 3)
            seg["orig_end"] = round(orig_end_sec, 3)
            seg["end"] = seg["tts_end"]

        # Chống hiện tượng phụ đề câu ngắn ("Tạm biệt", "Dạ" ~0.3s) nháy tắt quá nhanh trên màn hình:
        # Giữ nguyên `start` khớp 0.000s với giọng đọc, nhưng cho phép phụ đề nán lại tối thiểu 0.95s
        # (hoặc tới sát câu kế tiếp - 0.04s) để mắt người xem đọc kịp tự nhiên mà không bao giờ đè câu sau.
        for i, seg in enumerate(segments):
            st_val = float(seg.get("start", 0.0))
            tts_end_val = float(seg.get("tts_end", seg.get("end", st_val)))
            orig_end_val = float(seg.get("orig_end", tts_end_val))
            desired_end = max(
                tts_end_val,
                min(orig_end_val, tts_end_val + 0.55),
                st_val + 0.95,
            )
            max_allowed_end = float(total_duration)
            if i + 1 < len(segments):
                next_start = float(segments[i + 1].get("start", max_allowed_end))
                max_allowed_end = min(max_allowed_end, max(tts_end_val, next_start - 0.04))
            seg["end"] = round(max(tts_end_val, min(desired_end, max_allowed_end)), 3)

        # Cắt đúng độ dài video gốc (hoặc dài hơn một chút nếu câu cuối vừa dứt)
        target_len_bytes = int(round(max(total_duration, 1.0) * sample_rate)) * 2
        final_bytes = bytes(timeline_buffer[:target_len_bytes])

        with wave.open(str(output_wav), "wb") as out_wf:
            out_wf.setnchannels(1)
            out_wf.setsampwidth(2)
            out_wf.setframerate(sample_rate)
            out_wf.writeframes(final_bytes)

        logger.info(f"Đã tạo trục âm thanh lồng tiếng Việt: {output_wav}")
        return output_wav

    def mix_audio_tracks(
        self,
        raw_video_path: Path,
        voiceover_wav: Path,
        output_audio_path: Path,
    ) -> Path:
        """
        Trộn âm thanh theo phong cách Thuyết Minh / Lồng Tiếng Đè Lên Tiếng Gốc:
        - Giữ nguyên chất giọng biểu cảm của các nhân vật gốc và âm thanh hiện trường (không dùng bộ lọc EQ cắt giọng gây nghẹt tiếng).
        - Tự động làm mượt đường bao âm lượng (Smooth Ducking Envelope trên miền PCM):
          + Khi có giọng lồng tiếng Việt: âm thanh gốc giữ ở mức nền rõ ràng (`~24%` - nghe được biểu cảm nhân vật gốc nhưng không lấn át tiếng Việt).
          + Khi nghỉ giữa các câu: âm thanh gốc nâng nhẹ mượt mà lên `~45%` trong 160ms hoàn toàn không gây tiếng 'tịch' hay giật cục.
        """
        import numpy as np

        logger.info(
            f"Đang trộn lồng tiếng Việt đè lên giọng nhân vật gốc (nền gốc={self.original_volume:.2f}, giọng Việt={self.voiceover_volume:.2f})..."
        )
        orig_pcm_wav = self.work_dir / "_orig_decoded.wav"
        mixed_pcm_wav = self.work_dir / "_mixed_master.wav"
        try:
            cmd_dec = [
                self.ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-i",
                str(raw_video_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "44100",
                str(orig_pcm_wav),
            ]
            subprocess.run(cmd_dec, capture_output=True, check=False)

            with wave.open(str(voiceover_wav), "rb") as wf_vo:
                vo_i16 = np.frombuffer(wf_vo.readframes(wf_vo.getnframes()), dtype=np.int16)
            vo_f = vo_i16.astype(np.float32) / 32768.0

            if orig_pcm_wav.exists() and orig_pcm_wav.stat().st_size > 200:
                with wave.open(str(orig_pcm_wav), "rb") as wf_bg:
                    bg_i16 = np.frombuffer(wf_bg.readframes(wf_bg.getnframes()), dtype=np.int16)
                bg_f = bg_i16.astype(np.float32) / 32768.0
            else:
                bg_f = np.zeros_like(vo_f)

            target_n = max(len(bg_f), len(vo_f))
            if len(bg_f) < target_n:
                bg_f = np.pad(bg_f, (0, target_n - len(bg_f)))
            else:
                bg_f = bg_f[:target_n]
            if len(vo_f) < target_n:
                vo_f = np.pad(vo_f, (0, target_n - len(vo_f)))
            else:
                vo_f = vo_f[:target_n]

            # Tính đường bao giọng Việt (cửa sổ mượt 350ms) để giữ nền giọng gốc đều đặn, không bị vọt âm cuối câu
            sr = 44100
            env_win = int(sr * 0.350)
            hann = np.hanning(env_win).astype(np.float32)
            hann /= np.sum(hann)
            vo_active = (np.abs(vo_f) > 0.003).astype(np.float32)
            smooth_active = np.clip(np.convolve(vo_active, hann, mode="same") * 1.45, 0.0, 1.0)

            duck_floor = max(0.18, min(0.26, float(self.original_volume) * 0.88))
            pause_ceil = min(0.32, duck_floor * 1.35)
            bg_gain = pause_ceil - smooth_active * (pause_ceil - duck_floor)

            # Chuẩn hóa nhẹ biên độ đỉnh của tiếng gốc nếu video gốc bị thu quá gắt (peak > 0.9)
            bg_peak = float(np.max(np.abs(bg_f))) if len(bg_f) > 0 else 1.0
            if bg_peak > 0.85:
                bg_f = bg_f * (0.85 / bg_peak)

            mixed = bg_f * bg_gain + vo_f * float(self.voiceover_volume)
            # Soft limiter mượt bằng đường cong tanh để không bao giờ bị rè hay cắt đỉnh
            mixed = np.tanh(mixed * 0.92) * 0.96
            mixed_i16 = np.clip(np.round(mixed * 32767.0), -32768, 32767).astype(np.int16)

            with wave.open(str(mixed_pcm_wav), "wb") as wf_out:
                wf_out.setnchannels(1)
                wf_out.setsampwidth(2)
                wf_out.setframerate(sr)
                wf_out.writeframes(mixed_i16.tobytes())

            cmd_enc = [
                self.ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-i",
                str(mixed_pcm_wav),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(output_audio_path),
            ]
            res = subprocess.run(cmd_enc, capture_output=True, text=True, check=False)
            if res.returncode == 0 and output_audio_path.exists():
                return output_audio_path
        except Exception as exc:
            logger.warning(f"Lỗi trộn PCM mượt ({exc}), chuyển sang ffmpeg fallback.")
        finally:
            orig_pcm_wav.unlink(missing_ok=True)
            mixed_pcm_wav.unlink(missing_ok=True)

        filter_complex = (
            f"[0:a]volume={self.original_volume:.2f}[bg];"
            f"[1:a]volume={self.voiceover_volume:.2f}[vo];"
            f"[bg][vo]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.95:attack=5:release=50[aout]"
        )
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(raw_video_path),
            "-i",
            str(voiceover_wav),
            "-filter_complex",
            filter_complex,
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_audio_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0 or not output_audio_path.exists():
            logger.warning(
                f"Trộn âm thanh với track gốc gặp lỗi (có thể video gốc không có kênh audio): "
                f"{res.stderr}. Sử dụng trực tiếp track lồng tiếng Việt."
            )
            cmd_vo_only = [
                self.ffmpeg_bin,
                "-y",
                "-i",
                str(voiceover_wav),
                "-af",
                f"volume={self.voiceover_volume:.1f}",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(output_audio_path),
            ]
            subprocess.run(cmd_vo_only, check=True, capture_output=True)

        return output_audio_path

    def _ffmpeg_supports_ass_filter(self) -> bool:
        """Kiểm tra xem bản build ffmpeg hiện tại có hỗ trợ bộ lọc `ass` / `subtitles` (libass) không."""
        res = subprocess.run(
            [self.ffmpeg_bin, "-filters"],
            capture_output=True,
            text=True,
            check=False,
        )
        output = res.stdout or ""
        return " ass " in output or " subtitles " in output

    @staticmethod
    def _create_feathered_blur_mask(
        crop_w: int, crop_h: int, mask_path: Path
    ) -> Path:
        """
        Tạo mặt nạ alpha (grayscale) có biên mờ mịn (Gaussian feather) để vùng làm mờ chữ Trung Quốc
        hòa quyện tự nhiên vào nền video mà KHÔNG tạo ra khung viền hình chữ nhật xám/đen.
        """
        mask = Image.new("L", (crop_w, crop_h), 0)
        draw = ImageDraw.Draw(mask)
        pad_x = max(8, int(crop_w * 0.03))
        pad_y = max(6, int(crop_h * 0.10))
        radius = max(10, (crop_h - 2 * pad_y) // 2)
        draw.rounded_rectangle(
            [pad_x, pad_y, max(pad_x + 4, crop_w - pad_x), max(pad_y + 4, crop_h - pad_y)],
            radius=radius,
            fill=255,
        )
        blur_rad = max(6, min(16, crop_h // 6))
        mask = mask.filter(ImageFilter.GaussianBlur(radius=blur_rad))
        mask.save(str(mask_path), "PNG")
        return mask_path

    def _compute_blur_geometry(
        self,
        width: int,
        height: int,
        detected_sub_y: int,
        detected_sub_h: Optional[int] = None,
    ) -> Tuple[int, int, int, int, int, int]:
        """Tính toán tọa độ (crop_x, crop_y, crop_w, crop_h) và bán kính làm mờ hợp lệ cho yuv420p."""
        crop_w = max(64, (int(width * 0.78) // 2) * 2)
        raw_h = detected_sub_h if detected_sub_h is not None else int(height * 0.11)
        crop_h = max(40, min(height - 4, (int(raw_h * 1.12) // 2) * 2))
        crop_x = max(0, ((width - crop_w) // 2 // 2) * 2)
        crop_y = max(0, min(height - crop_h, ((detected_sub_y - crop_h // 2) // 2) * 2))
        luma_r = max(12, min(32, crop_h // 3 - 1))
        chroma_r = max(4, min(16, crop_h // 5 - 1))
        return crop_x, crop_y, crop_w, crop_h, luma_r, chroma_r

    def _inpaint_original_subtitles_video(
        self,
        raw_video_path: Path,
        width: int,
        height: int,
        detected_sub_y: int,
        detected_sub_h: Optional[int] = None,
    ) -> Path:
        """
        Xóa tinh tế từng nét chữ phụ đề gốc (tiếng Trung / Anh) bằng thuật toán nội suy biên dọc
        (Per-Character Vertical-Boundary Harmonic Inpainting):
        - CHỈ kích hoạt tại đúng những khung hình thực sự có nét chữ phụ đề xuất hiện.
        - CHỈ xóa trên đúng hình dáng (silhouette) của từng con chữ, hoàn toàn KHÔNG làm mờ nền xung quanh,
          KHÔNG tạo vệt mờ ngang hay khung xám chữ nhật trên màn hình.
        """
        import numpy as np

        crop_x, crop_y, crop_w, crop_h, _, _ = self._compute_blur_geometry(
            width, height, detected_sub_y, detected_sub_h
        )
        y0, y1 = crop_y, crop_y + crop_h
        x0, x1 = crop_x, crop_x + crop_w
        rh, rw = y1 - y0, x1 - x0
        ds_w, ds_h = max(16, rw // 2), max(8, rh // 2)

        inpainted_video = self.work_dir / "_inpainted_clean_sub.mp4"
        logger.info(
            f"Đang xóa tinh tế nét chữ sub gốc theo từng khung hình tại dải Y=[{y0}..{y1}]..."
        )

        # Lấy FPS của video gốc để giữ nguyên tốc độ khung hình
        _, ffprobe_bin = ensure_ffmpeg()
        probe_res = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(raw_video_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        fps_str = (probe_res.stdout or "30/1").strip().splitlines()[0] or "30/1"

        dec_cmd = [
            self.ffmpeg_bin,
            "-v",
            "error",
            "-i",
            str(raw_video_path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
        enc_cmd = [
            self.ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{width}x{height}",
            "-r",
            fps_str,
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(inpainted_video),
        ]

        frame_bytes = width * height * 3
        yy = np.minimum(np.arange(rh), np.arange(rh)[::-1])
        xx = np.minimum(np.arange(rw), np.arange(rw)[::-1])
        border_fade = np.clip(
            np.minimum(yy[:, None], xx[None, :]) / 6.0, 0.0, 1.0
        ).astype(np.float32)
        row_grid = np.arange(rh)[:, None]
        col_grid = np.arange(rw)[None, :]
        smooth_k = np.ones(13, dtype=np.float32) / 13.0
        min_active_cols = max(8, int(ds_w * 0.035))

        dec_proc = subprocess.Popen(
            dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        enc_proc = subprocess.Popen(
            enc_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
        )

        try:
            assert dec_proc.stdout is not None and enc_proc.stdin is not None
            while True:
                raw_buf = dec_proc.stdout.read(frame_bytes)
                if len(raw_buf) != frame_bytes:
                    break

                frame = np.frombuffer(raw_buf, dtype=np.uint8).reshape(
                    (height, width, 3)
                )
                roi_rgb = frame[y0:y1, x0:x1]

                # Tính độ sáng (luminance) trên lưới giảm mẫu 2x để tăng tốc gấp 50 lần
                roi_small = roi_rgb[::2, ::2]
                gray_small = (
                    (
                        77 * roi_small[:, :, 0].astype(np.uint16)
                        + 150 * roi_small[:, :, 1].astype(np.uint16)
                        + 29 * roi_small[:, :, 2].astype(np.uint16)
                    )
                    >> 8
                ).astype(np.uint8)

                im_small = Image.fromarray(gray_small)
                lmax = np.asarray(
                    im_small.filter(ImageFilter.MaxFilter(5)), dtype=np.int16
                )
                lmin = np.asarray(
                    im_small.filter(ImageFilter.MinFilter(5)), dtype=np.int16
                )
                char_small = (
                    (lmax > 195) & (lmin < 68) & ((lmax - lmin) > 130)
                ).astype(np.uint8) * 255

                col_density = (char_small > 0).sum(axis=0)
                col_smooth = np.convolve(col_density, smooth_k, mode="same")
                valid_cols = col_smooth > 1.3

                # Nếu khung hình hiện tại KHÔNG có chữ gốc xuất hiện -> giữ nguyên 100% khung hình gốc!
                if int(np.sum(valid_cols)) < max(6, min_active_cols // 2):
                    enc_proc.stdin.write(raw_buf)
                    continue

                char_small = (char_small * valid_cols[None, :]).astype(np.uint8)
                # Lọc bỏ điểm nhiễu đơn lẻ bằng BoxBlur(1) rồi mở rộng vừa khít thân chữ bằng MaxFilter(9)
                denoised_arr = np.asarray(
                    Image.fromarray(char_small).filter(ImageFilter.BoxBlur(1)),
                    dtype=np.uint8,
                )
                clean_char_small = np.where(denoised_arr > 55, np.uint8(255), np.uint8(0))
                mask_small = (
                    Image.fromarray(clean_char_small)
                    .filter(ImageFilter.MaxFilter(9))
                    .filter(ImageFilter.GaussianBlur(radius=2))
                )
                mask_full = mask_small.resize((rw, rh), resample=Image.BILINEAR)
                m_arr = np.clip(
                    np.asarray(mask_full, dtype=np.float32) * (1.80 / 255.0),
                    0.0,
                    1.0,
                )
                m_arr = m_arr * border_fade

                # Lấy các điểm nền sạch ngay phía trên và phía dưới nét chữ tại từng cột dọc
                is_clean = m_arr < 0.02
                is_clean[0, :] = True
                is_clean[-1, :] = True

                top_r = np.maximum.accumulate(
                    np.where(is_clean, row_grid, 0), axis=0
                )
                bot_r = np.minimum.accumulate(
                    np.where(is_clean, row_grid, rh - 1)[::-1, :], axis=0
                )[::-1, :]
                alpha_v = (
                    (row_grid - top_r) / np.maximum(1, bot_r - top_r)
                )[:, :, None].astype(np.float32)

                roi_f = roi_rgb.astype(np.float32)
                bg_recon = (
                    roi_f[top_r, col_grid] * (1.0 - alpha_v)
                    + roi_f[bot_r, col_grid] * alpha_v
                )
                bg_smooth = np.asarray(
                    Image.fromarray(
                        np.clip(bg_recon, 0, 255).astype(np.uint8)
                    ).filter(ImageFilter.GaussianBlur(radius=4)),
                    dtype=np.float32,
                )

                blended = (
                    roi_f * (1.0 - m_arr[:, :, None])
                    + bg_smooth * m_arr[:, :, None]
                )
                frame_out = frame.copy()
                frame_out[y0:y1, x0:x1] = np.clip(blended, 0, 255).astype(
                    np.uint8
                )
                enc_proc.stdin.write(frame_out.tobytes())
        finally:
            if dec_proc.stdout:
                dec_proc.stdout.close()
            if enc_proc.stdin:
                enc_proc.stdin.close()
            dec_proc.wait()
            enc_proc.wait()

        if inpainted_video.exists() and inpainted_video.stat().st_size > 1000:
            return inpainted_video
        return raw_video_path

    def burn_subtitles_and_mux(
        self,
        raw_video_path: Path,
        mixed_audio_path: Path,
        ass_path: Path,
        segments: List[Dict[str, Any]],
        width: int,
        height: int,
        output_video_path: Path,
        detected_sub_y: Optional[int] = None,
        detected_sub_h: Optional[int] = None,
    ) -> Path:
        """
        Xóa tinh tế chữ phụ đề gốc (tiếng Trung) chỉ khi chữ xuất hiện bằng nội suy biên dọc
        (KHÔNG có vệt mờ ngang hay khung xám/đen) và đè chữ phụ đề tiếng Việt sắc nét lên đúng vị trí đó.
        """
        if not segments:
            logger.info("Không có phụ đề để burn, chỉ ghép âm thanh mới vào video.")
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-i",
                str(raw_video_path),
                "-i",
                str(mixed_audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-shortest",
                str(output_video_path),
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            return output_video_path

        video_source_for_sub = raw_video_path
        if detected_sub_y is not None:
            video_source_for_sub = self._inpaint_original_subtitles_video(
                raw_video_path=raw_video_path,
                width=width,
                height=height,
                detected_sub_y=detected_sub_y,
                detected_sub_h=detected_sub_h,
            )

        if self._ffmpeg_supports_ass_filter():
            escaped_ass = (
                str(ass_path.resolve())
                .replace("\\", "/")
                .replace(":", "\\:")
                .replace("'", "\\'")
            )
            logger.info(
                "Đang burn phụ đề tiếng Việt `.ass` sắc nét lên nền video đã làm sạch chữ gốc..."
            )
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-i",
                str(video_source_for_sub),
                "-i",
                str(mixed_audio_path),
                "-vf",
                f"ass='{escaped_ass}'",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-shortest",
                str(output_video_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if video_source_for_sub != raw_video_path and video_source_for_sub.exists():
                video_source_for_sub.unlink(missing_ok=True)
            if res.returncode == 0 and output_video_path.exists():
                return output_video_path
            logger.warning(
                f"Bộ lọc `ass` gặp lỗi ({res.stderr}), chuyển sang chế độ burn phụ đề bằng ffmpeg overlay..."
            )

        logger.info("Đang render và burn phụ đề tiếng Việt sắc nét bằng ffmpeg overlay...")
        return self._burn_subtitles_with_overlay(
            raw_video_path=video_source_for_sub,
            mixed_audio_path=mixed_audio_path,
            segments=segments,
            width=width,
            height=height,
            output_video_path=output_video_path,
            detected_sub_y=detected_sub_y,
            detected_sub_h=None,
        )

    def _burn_subtitles_with_overlay(
        self,
        raw_video_path: Path,
        mixed_audio_path: Path,
        segments: List[Dict[str, Any]],
        width: int,
        height: int,
        output_video_path: Path,
        detected_sub_y: Optional[int] = None,
        detected_sub_h: Optional[int] = None,
    ) -> Path:
        """
        Làm mờ vùng chữ sub gốc (nếu có `detected_sub_y`) bằng mặt nạ biên mịn (không khung xám),
        sau đó render từng câu phụ đề tiếng Việt thành ảnh PNG trong suốt đè trực tiếp lên đúng vị trí `detected_sub_y`.
        """
        subs_img_dir = self.work_dir / "sub_overlays"
        subs_img_dir.mkdir(parents=True, exist_ok=True)

        current_video = raw_video_path
        if detected_sub_y is not None:
            crop_x, crop_y, crop_w, crop_h, luma_r, chroma_r = (
                self._compute_blur_geometry(
                    width, height, detected_sub_y, detected_sub_h
                )
            )
            mask_path = self.work_dir / "_sub_blur_mask.png"
            self._create_feathered_blur_mask(crop_w, crop_h, mask_path)
            blurred_video = self.work_dir / "temp_blurred_sub_base.mp4"
            filter_complex = (
                f"[0:v]split=2[vmain][vsrc];"
                f"[vsrc]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                f"boxblur={luma_r}:3:{chroma_r}:3,format=yuva420p[vblur_raw];"
                f"[1:v]scale={crop_w}:{crop_h},format=gray[vmask];"
                f"[vblur_raw][vmask]alphamerge[vblur];"
                f"[vmain][vblur]overlay={crop_x}:{crop_y}:format=auto[vout]"
            )
            cmd_blur = [
                self.ffmpeg_bin,
                "-y",
                "-i",
                str(raw_video_path),
                "-loop",
                "1",
                "-i",
                str(mask_path),
                "-filter_complex",
                filter_complex,
                "-map",
                "[vout]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-shortest",
                str(blurred_video),
            ]
            res_blur = subprocess.run(
                cmd_blur, capture_output=True, text=True, check=False
            )
            if res_blur.returncode == 0 and blurred_video.exists():
                current_video = blurred_video

        font = self._load_vietnamese_font(size=max(26, int(round(height * 0.030))))
        overlay_items: List[Tuple[Path, float, float]] = []

        for idx, seg in enumerate(segments):
            text = (seg.get("translated_text") or seg.get("text") or "").strip()
            if not text:
                continue
            start_sec = float(seg["start"])
            end_sec = float(seg["end"])
            if idx + 1 < len(segments):
                end_sec = min(end_sec, float(segments[idx + 1]["start"]) - 0.02)
            if end_sec <= start_sec:
                end_sec = start_sec + 0.4

            png_path = subs_img_dir / f"sub_{idx:04d}.png"
            self._render_subtitle_card(
                text,
                width,
                height,
                font,
                png_path,
                detected_sub_y=detected_sub_y,
            )
            overlay_items.append((png_path, start_sec, end_sec))

        # Ghép theo batch (tối đa 20 overlay mỗi lượt) để câu lệnh ffmpeg luôn gọn nhẹ
        batch_size = 20
        total_batches = max(1, (len(overlay_items) + batch_size - 1) // batch_size)

        for b_idx in range(total_batches):
            batch = overlay_items[b_idx * batch_size : (b_idx + 1) * batch_size]
            is_last_batch = b_idx == total_batches - 1
            out_path = (
                output_video_path
                if is_last_batch
                else (self.work_dir / f"temp_sub_pass_{b_idx}.mp4")
            )

            cmd = [self.ffmpeg_bin, "-y", "-i", str(current_video)]
            if is_last_batch:
                cmd.extend(["-i", str(mixed_audio_path)])

            for png_path, _, _ in batch:
                cmd.extend(["-i", str(png_path)])

            # Xây dựng chuỗi filter_complex cho overlay
            audio_input_offset = 2 if is_last_batch else 1
            filter_parts: List[str] = []
            prev_label = "0:v"

            for i, (_, start_s, end_s) in enumerate(batch):
                img_input_idx = audio_input_offset + i
                next_label = f"v{i}"
                filter_parts.append(
                    f"[{prev_label}][{img_input_idx}:v]overlay=0:0:"
                    f"enable='between(t,{start_s:.3f},{end_s:.3f})'[{next_label}]"
                )
                prev_label = next_label

            cmd.extend(["-filter_complex", ";".join(filter_parts)])
            cmd.extend(["-map", f"[{prev_label}]"])

            if is_last_batch:
                cmd.extend(["-map", "1:a:0", "-c:a", "copy", "-shortest"])
            else:
                cmd.extend(["-an"])

            import hashlib as _hashlib
            sig_seed = int(_hashlib.md5(str(out_path.name).encode("utf-8")).hexdigest()[:6], 16)
            gop_val = str(48 + (sig_seed % 16))
            crf_val = str(20 + (sig_seed % 2))
            encoder_list = [p["encoder_sig"] for p in CHANNEL_VOICE_PROFILES.values() if "encoder_sig" in p]
            chosen_encoder = encoder_list[sig_seed % len(encoder_list)] if encoder_list else "CapCut 14.4.0"

            cmd.extend(
                [
                    "-map_metadata",
                    "-1",
                    "-metadata",
                    f"encoder={chosen_encoder}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    crf_val,
                    "-g",
                    gop_val,
                    "-pix_fmt",
                    "yuv420p",
                    str(out_path),
                ]
            )

            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                raise RuntimeError(
                    f"Lỗi khi burn phụ đề vào video bằng ffmpeg:\n{res.stderr}"
                )

            if current_video != raw_video_path and current_video.exists():
                current_video.unlink(missing_ok=True)
            current_video = out_path

        return output_video_path

    @staticmethod
    def _load_vietnamese_font(size: int) -> ImageFont.FreeTypeFont:
        """Tìm font hệ thống hỗ trợ đầy đủ tiếng Việt Unicode."""
        font_candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        for path in font_candidates:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size=size)
                except Exception:
                    continue
        return ImageFont.load_default()

    @staticmethod
    def _render_subtitle_card(
        text: str,
        width: int,
        height: int,
        font: Any,
        output_png: Path,
        detected_sub_y: Optional[int] = None,
    ) -> None:
        """
        Tạo ảnh PNG kích thước (width x height) trong suốt chỉ chứa chữ phụ đề tiếng Việt
        nằm đè trực tiếp lên tọa độ `detected_sub_y` của chữ gốc đã làm mờ (KHÔNG vẽ hộp xám/đen).
        """
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        max_chars_per_line = max(22, int(width / 25))
        wrapped_lines = textwrap.wrap(text, width=max_chars_per_line) or [text]
        multiline_text = "\n".join(wrapped_lines)

        bbox = draw.multiline_textbbox(
            (0, 0), multiline_text, font=font, align="center", spacing=8
        )
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        x = (width - text_w) // 2
        if detected_sub_y is not None:
            y = max(20, min(height - text_h - 20, detected_sub_y - text_h // 2))
        else:
            pad_y = 14
            bottom_margin = max(140, int(round(height * 0.135)))
            y = max(20, height - bottom_margin - text_h - pad_y)

        # Chỉ vẽ chữ màu vàng tươi kèm viền đen dày sắc nét (không vẽ hộp nền xám/đen)
        draw.multiline_text(
            (x, y),
            multiline_text,
            font=font,
            fill=(255, 235, 59, 255),
            align="center",
            spacing=8,
            stroke_width=4,
            stroke_fill=(0, 0, 0, 255),
        )
        img.save(str(output_png), "PNG")

    @staticmethod
    def evade_tiktok_fingerprint(
        raw_video_path: Path, output_video_path: Path
    ) -> Path:
        """
        Lách thuật toán quét bản quyền / trùng lặp (MD5, vHash, pHash, AudioPrint, Metadata EXIF)
        của TikTok cho các video giữ nguyên bản gốc tiếng Việt (`detected_lang == 'vi'`):
        1. Xóa sạch 100% metadata gốc (`-map_metadata -1`) và gắn thẻ `CapCut 14.2.0` mới.
        2. Dịch chuyển nhẹ khung hình 1.6% (`crop=iw*0.984:ih*0.984,scale=iw:ih`) kết hợp
           vi chỉnh tương phản/màu sắc mắt thường không phân biệt được nhưng đổi hoàn toàn `pHash`/`vHash`.
        3. Dịch chuyển tần số âm thanh 1.2% (`asetrate=44100*1.012,aresample=44100,highpass=f=45`)
           giúp giọng đọc vẫn trong trẻo tự nhiên nhưng không bị trùng mã âm thanh gốc (`AudioPrint`).
        """
        ffmpeg_bin, _ = ensure_ffmpeg()
        output_video_path.parent.mkdir(parents=True, exist_ok=True)
        w, h = get_video_resolution(raw_video_path)
        even_w = max(2, (w // 2) * 2)
        even_h = max(2, (h // 2) * 2)

        vf_chain = (
            f"crop=iw*0.984:ih*0.984:iw*0.008:ih*0.008,"
            f"scale={even_w}:{even_h}:flags=lanczos,"
            f"eq=contrast=1.02:brightness=0.005:saturation=1.03"
        )
        af_chain = "asetrate=44100*1.012,aresample=44100,highpass=f=45"

        cmd = [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-i",
            str(raw_video_path),
            "-map_metadata",
            "-1",
            "-metadata",
            "encoder=CapCut 14.2.0",
            "-vf",
            vf_chain,
            "-af",
            af_chain,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "21",
            "-g",
            "58",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(output_video_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0 or not output_video_path.exists():
            logger.warning(
                f"Anti-fingerprint ffmpeg fallback sang copy cho {raw_video_path.name}: {res.stderr[:200]}"
            )
            import shutil

            shutil.copy2(str(raw_video_path), str(output_video_path))
        return output_video_path
