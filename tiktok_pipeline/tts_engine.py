import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import edge_tts

from .utils import ensure_ffmpeg, get_media_duration, logger

SUPPORTED_VOICES = {
    "female": "vi-VN-HoaiMyNeural",
    "male": "vi-VN-NamMinhNeural",
    "vi-VN-HoaiMyNeural": "vi-VN-HoaiMyNeural",
    "vi-VN-NamMinhNeural": "vi-VN-NamMinhNeural",
}


class TTSEngine:
    """
    Tổng hợp giọng đọc tiếng Việt bằng `edge-tts` (vi-VN-HoaiMyNeural / vi-VN-NamMinhNeural),
    tự động tính toán thời lượng audio tiếng Việt so với câu gốc và điều chỉnh tốc độ (speed rate)
    để giọng đọc khớp tự nhiên với khung thời gian của video.
    """

    def __init__(
        self,
        work_dir: Path,
        voice: Optional[str] = None,
        base_rate: str = "+0%",
        pitch: str = "+0Hz",
    ):
        self.work_dir = Path(work_dir)
        self.tts_dir = self.work_dir / "tts_segments"
        self.tts_dir.mkdir(parents=True, exist_ok=True)

        chosen_voice = (voice or os.getenv("TTS_VOICE") or "vi-VN-NamMinhNeural").strip()
        self.voice = SUPPORTED_VOICES.get(chosen_voice, chosen_voice)
        self.base_rate = base_rate or "+0%"
        self.pitch = pitch or "+0Hz"
        self.ffmpeg_bin, _ = ensure_ffmpeg()

    def synthesize_segments(
        self, segments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Tạo file âm thanh lồng tiếng cho từng segment và căn chỉnh tốc độ khớp với mốc thời gian gốc.
        """
        if not segments:
            return []

        logger.info(
            f"Đang tổng hợp giọng đọc tiếng Việt bằng edge-tts "
            f"(Giọng: {self.voice}, Tốc độ gốc: {self.base_rate}, Độ trầm/cao: {self.pitch}) "
            f"cho {len(segments)} câu thoại..."
        )
        return asyncio.run(self._synthesize_all_async(segments))

    async def _synthesize_all_async(
        self, segments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        cursor_sec = 0.0

        for idx, seg in enumerate(segments):
            vi_text = (seg.get("translated_text") or seg.get("text") or "").strip()
            if not vi_text:
                continue

            orig_start = float(seg["start"])
            orig_end = float(seg["end"])
            orig_duration = max(0.35, orig_end - orig_start)

            # Neo cứng mốc bắt đầu (Strict Timestamp Anchoring): đảm bảo không bị trôi dây chuyền (Domino Drift)
            effective_start = max(orig_start, cursor_sec + 0.02) if idx > 0 else orig_start

            # Khoảng thời gian tối đa tuyệt đối trước khi câu tiếp theo bắt đầu (không bao giờ tràn qua next_orig_start)
            if idx + 1 < len(segments):
                next_orig_start = float(segments[idx + 1]["start"])
                hard_max_window = max(0.35, next_orig_start - effective_start - 0.03)
                # Chỉ cho phép ngân đuôi tối đa +0.22s vào khoảng nghỉ để giọng dứt đúng lúc nhân vật khép miệng
                target_window = min(orig_duration + 0.22, hard_max_window)
            else:
                hard_max_window = orig_duration + 0.60
                target_window = orig_duration + 0.25

            target_window = max(0.35, target_window)

            raw_mp3 = self.tts_dir / f"seg_{idx:04d}_raw.mp3"
            trimmed_mp3 = self.tts_dir / f"seg_{idx:04d}_trimmed.mp3"
            final_wav = self.tts_dir / f"seg_{idx:04d}.wav"

            # Bước 1: Tạo TTS với cấu hình giọng & tốc độ cơ sở của kênh
            await self._generate_edge_tts(vi_text, self.base_rate, raw_mp3)
            self._trim_silence(raw_mp3, trimmed_mp3)
            base_duration = get_media_duration(trimmed_mp3)

            if base_duration <= 0.05:
                base_duration = get_media_duration(raw_mp3)
                trimmed_mp3 = raw_mp3

            # Bước 2: Căn chỉnh tốc độ (atempo) để khớp khít với khẩu hình câu gốc và tuyệt đối không tràn sang câu sau
            if base_duration > target_window and target_window > 0.25:
                needed_factor = base_duration / target_window
                # Nếu câu sau sát bên cạnh, ưu tiên đẩy tốc độ lên tới 1.60x để không làm trễ câu sau
                max_atempo = 1.60 if base_duration > hard_max_window else 1.48
                residual_factor = min(max_atempo, needed_factor)
            elif base_duration < orig_duration * 0.68 and orig_duration >= 1.8:
                # Nếu câu dịch hơi ngắn so với thời lượng nhân vật nói, đọc chậm lại nhẹ (0.90x) cho tự nhiên
                residual_factor = max(0.90, base_duration / (orig_duration * 0.82))
            else:
                residual_factor = 1.0

            rate_str = self.base_rate
            self._finalize_segment_audio(
                trimmed_mp3,
                final_wav,
                residual_factor,
                max_duration=hard_max_window if idx + 1 < len(segments) else None,
            )
            final_duration = get_media_duration(final_wav)

            # Dọn file tạm
            raw_mp3.unlink(missing_ok=True)
            if trimmed_mp3 != raw_mp3:
                trimmed_mp3.unlink(missing_ok=True)

            effective_end = effective_start + final_duration
            cursor_sec = effective_end

            enriched_seg = dict(seg)
            enriched_seg["start"] = round(effective_start, 3)
            enriched_seg["end"] = round(effective_end, 3)
            enriched_seg["tts_path"] = str(final_wav)
            enriched_seg["tts_duration"] = round(final_duration, 3)
            enriched_seg["target_duration"] = round(orig_duration, 3)
            enriched_seg["tts_rate"] = rate_str
            enriched_seg["atempo_factor"] = round(residual_factor, 3)
            results.append(enriched_seg)

            logger.info(
                f"TTS #{idx:02d} [{effective_start:05.2f}s-{effective_end:05.2f}s | gốc={orig_duration:.2f}s]: "
                f"rate={rate_str}, atempo={residual_factor:.2f}x -> {final_duration:.2f}s"
            )

        return results

    async def _generate_edge_tts(
        self, text: str, rate: str, output_path: Path
    ) -> None:
        """Gọi edge-tts với cơ chế retry 5 lần tự động và dự phòng an toàn."""
        clean_text = text.strip()
        last_err = None
        for attempt in range(5):
            try:
                communicate = edge_tts.Communicate(
                    text=clean_text,
                    voice=self.voice,
                    rate=rate,
                    pitch=self.pitch,
                )
                await communicate.save(str(output_path))
                if output_path.exists() and output_path.stat().st_size > 100:
                    return
            except Exception as exc:
                last_err = exc
                await asyncio.sleep(1.0 * (attempt + 1))

        # Dự phòng: tạo 0.4s âm thanh im lặng nếu câu chỉ chứa ký tự đặc biệt khiến Edge-TTS từ chối
        logger.warning(
            f"Edge-TTS không trả về audio cho câu '{clean_text}' ({last_err}), tạo đoạn lặng thay thế."
        )
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "0.4",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, check=False)

    def _trim_silence(self, input_path: Path, output_path: Path) -> None:
        """Cắt sạch khoảng lặng thừa ở đầu/cuối và rút gọn khoảng nghỉ giữa câu do edge-tts sinh ra."""
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(input_path),
            "-af",
            (
                "silenceremove=start_periods=1:start_duration=0.01:start_threshold=-38dB:"
                "stop_periods=-1:stop_duration=0.12:stop_threshold=-42dB,"
                "areverse,"
                "silenceremove=start_periods=1:start_duration=0.01:start_threshold=-38dB,"
                "areverse"
            ),
            str(output_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 100:
            # Nếu silenceremove cắt quá tay với câu cực ngắn, giữ nguyên file gốc
            import shutil

            shutil.copyfile(str(input_path), str(output_path))

    def _finalize_segment_audio(
        self,
        input_path: Path,
        output_wav: Path,
        atempo_factor: float = 1.0,
        max_duration: Optional[float] = None,
    ) -> None:
        """Chuẩn hóa định dạng WAV 44.1kHz mono, áp dụng atempo và giới hạn biên thời lượng chống tràn câu."""
        filters = []
        if abs(atempo_factor - 1.0) > 0.02:
            # atempo của ffmpeg hỗ trợ khoảng [0.5, 2.0]
            clamped = max(0.5, min(2.0, atempo_factor))
            filters.append(f"atempo={clamped:.4f}")
        if max_duration is not None and max_duration > 0.30:
            fade_st = max(0.15, max_duration - 0.05)
            filters.append(f"atrim=0:{max_duration:.3f}")
            filters.append(f"afade=t=out:st={fade_st:.3f}:d=0.05")
        filters.append("aresample=44100")

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(input_path),
            "-af",
            ",".join(filters),
            "-ac",
            "1",
            "-ar",
            "44100",
            str(output_wav),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"Lỗi chuẩn hóa file âm thanh TTS: {res.stderr}")
