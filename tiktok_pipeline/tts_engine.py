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
        self,
        segments: List[Dict[str, Any]],
        video_path: Optional[Path] = None,
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
        return asyncio.run(self._synthesize_all_async(segments, video_path=video_path))

    async def _synthesize_all_async(
        self,
        segments: List[Dict[str, Any]],
        video_path: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        cursor_sec = 0.0
        raw_vid = Path(video_path) if video_path else (self.work_dir / "raw_video.mp4")
        vid_max_dur = get_media_duration(raw_vid) if raw_vid.exists() else 0.0

        for idx, seg in enumerate(segments):
            vi_text = (seg.get("translated_text") or seg.get("text") or "").strip()
            if not vi_text:
                continue

            orig_start = float(seg["start"])
            orig_end = float(seg["end"])
            orig_duration = max(0.35, orig_end - orig_start)

            # Neo cứng mốc bắt đầu (Strict Timestamp Anchoring): khớp đúng thời điểm giọng gốc mở miệng
            effective_start = max(orig_start, cursor_sec + 0.01) if idx > 0 else orig_start

            # Khóa chính xác thời điểm kết thúc (desired_end == orig_end) để giọng gốc kết thúc lúc nào thì giọng Việt kết thúc đúng lúc đó
            desired_end = orig_end
            if idx + 1 < len(segments):
                next_orig_start = float(segments[idx + 1]["start"])
                hard_max_end = max(effective_start + 0.35, next_orig_start - 0.06)
                desired_end = min(orig_end - 0.02, hard_max_end)
            else:
                safe_vid_end = (vid_max_dur - 0.22) if vid_max_dur > 1.0 else (orig_end - 0.04)
                desired_end = max(effective_start + 0.35, min(orig_end - 0.04, safe_vid_end))
                if (desired_end - effective_start) < 1.30 and idx > 0:
                    effective_start = max(cursor_sec + 0.02, desired_end - 1.30)

            exact_target_dur = max(0.35, desired_end - effective_start)

            raw_mp3 = self.tts_dir / f"seg_{idx:04d}_raw.mp3"
            trimmed_wav = self.tts_dir / f"seg_{idx:04d}_trimmed.wav"
            final_wav = self.tts_dir / f"seg_{idx:04d}.wav"

            # Bước 1: Tạo TTS ở tốc độ tự nhiên (+0%) và cắt khoảng lặng thừa sát đầu/cuối âm tiết
            rate_str = "+0%"
            await self._generate_edge_tts(vi_text, rate_str, raw_mp3)
            self._trim_silence(raw_mp3, trimmed_wav)
            base_duration = get_media_duration(trimmed_wav)

            if base_duration <= 0.05:
                base_duration = get_media_duration(raw_mp3)
                trimmed_wav = raw_mp3

            # Nếu câu tiếng Việt ngắn hơn hoặc dài hơn đáng kể so với thời lượng câu gốc (exact_target_dur),
            # điều chỉnh trực tiếp tốc độ phát âm Neural Prosody của Edge-TTS trước để giữ ngữ điệu tự nhiên nhất
            ratio = base_duration / max(0.25, exact_target_dur)
            if ratio < 0.90:
                slow_pct = max(-42, min(-10, int(round((ratio - 1.0) * 100))))
                rate_str = f"{slow_pct}%"
                await self._generate_edge_tts(vi_text, rate_str, raw_mp3)
                self._trim_silence(raw_mp3, trimmed_wav)
                new_dur = get_media_duration(trimmed_wav)
                if new_dur > 0.05:
                    base_duration = new_dur
            elif ratio > 1.05 or (idx == len(segments) - 1 and ratio > 0.98):
                fast_pct = max(12, min(38, int(round((ratio - 0.95) * 115))))
                rate_str = f"+{fast_pct}%"
                await self._generate_edge_tts(vi_text, rate_str, raw_mp3)
                self._trim_silence(raw_mp3, trimmed_wav)
                new_dur = get_media_duration(trimmed_wav)
                if new_dur > 0.05:
                    base_duration = new_dur

            # Bước 2: Tinh chỉnh atempo liên tục (không cắt ngắt giữa câu) để khớp 1:1 với exact_target_dur
            needed_factor = base_duration / max(0.25, exact_target_dur)
            residual_factor = max(0.55, min(1.90, needed_factor))

            self._finalize_segment_audio(
                trimmed_wav,
                final_wav,
                residual_factor,
                exact_target_dur=exact_target_dur,
            )
            final_duration = get_media_duration(final_wav)

            # Dọn file tạm
            raw_mp3.unlink(missing_ok=True)
            if trimmed_wav != raw_mp3:
                trimmed_wav.unlink(missing_ok=True)

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
        """
        Cắt khoảng lặng thừa ở đầu/cuối bằng đường bao RMS mượt trên miền PCM 44.1kHz
        (TUYỆT ĐỐI KHÔNG dùng `areverse + silenceremove -38dB` gây cắt cụt đuôi âm tiết và tạo tiếng 'tịch' cuối câu).
        Giữ lại 20ms đệm đầu câu và 45ms đệm đuôi tự nhiên, kết hợp fade-in/fade-out hình cosin mượt.
        """
        import numpy as np
        import wave

        tmp_wav = output_path.with_suffix(".pcm_tmp.wav")
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "44100",
            str(tmp_wav),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0 or not tmp_wav.exists() or tmp_wav.stat().st_size < 200:
            import shutil

            shutil.copyfile(str(input_path), str(output_path))
            return

        try:
            with wave.open(str(tmp_wav), "rb") as wf:
                sr = wf.getframerate()
                pcm_i16 = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

            if len(pcm_i16) < int(sr * 0.08):
                import shutil

                shutil.move(str(tmp_wav), str(output_path))
                return

            samples = pcm_i16.astype(np.float32) / 32768.0
            win = max(64, int(sr * 0.010))
            kernel = np.ones(win, dtype=np.float32) / float(win)
            rms_env = np.sqrt(np.convolve(samples * samples, kernel, mode="same"))

            active_idx = np.where(rms_env > 0.0035)[0]
            if len(active_idx) > 0:
                pre_pad = int(sr * 0.015)
                post_pad = int(sr * 0.035)
                st_idx = max(0, int(active_idx[0]) - pre_pad)
                en_idx = min(len(samples), int(active_idx[-1]) + post_pad)
                trimmed = samples[st_idx:en_idx].copy()
            else:
                trimmed = samples.copy()

            # Áp dụng đường bao Raised-Cosine Fade-In (12ms) & Fade-Out (30ms) để triệt tiêu 100% tiếng 'tịch'/click cuối câu
            fade_in_len = min(int(sr * 0.012), len(trimmed) // 4)
            fade_out_len = min(int(sr * 0.030), len(trimmed) // 3)
            if fade_in_len > 8:
                fi_curve = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, fade_in_len, dtype=np.float32)))
                trimmed[:fade_in_len] *= fi_curve
            if fade_out_len > 8:
                fo_curve = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, fade_out_len, dtype=np.float32)))
                trimmed[-fade_out_len:] *= fo_curve

            out_i16 = np.clip(np.round(trimmed * 32767.0), -32768, 32767).astype(np.int16)
            with wave.open(str(output_path), "wb") as out_wf:
                out_wf.setnchannels(1)
                out_wf.setsampwidth(2)
                out_wf.setframerate(sr)
                out_wf.writeframes(out_i16.tobytes())
        finally:
            tmp_wav.unlink(missing_ok=True)

    def _finalize_segment_audio(
        self,
        input_path: Path,
        output_wav: Path,
        atempo_factor: float = 1.0,
        max_duration: Optional[float] = None,
        exact_target_dur: Optional[float] = None,
    ) -> None:
        """
        Chuẩn hóa định dạng WAV 44.1kHz mono, áp dụng atempo và khóa thời lượng 1:1 với `exact_target_dur`
        (nếu sau atempo câu vẫn ngắn hơn giọng gốc, tự động chèn nhịp nghỉ tự nhiên vào đúng điểm ngắt từ giữa câu
        để chữ đầu và chữ cuối khớp khít 100% với giọng gốc mà không bao giờ kết thúc sớm).
        """
        import numpy as np
        import wave

        filters = []
        if abs(atempo_factor - 1.0) > 0.015:
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

        # Khóa thời lượng theo exact_target_dur và làm mượt biên PCM bằng đường cong Raised-Cosine
        try:
            with wave.open(str(output_wav), "rb") as wf:
                sr = wf.getframerate()
                pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32)
            if len(pcm) > 64:
                if exact_target_dur is not None and exact_target_dur >= 0.35:
                    target_samples = int(round(exact_target_dur * sr))
                    if len(pcm) > target_samples + int(sr * 0.02):
                        pcm = pcm[:target_samples].copy()

                fi = min(int(sr * 0.012), len(pcm) // 5)
                fo = min(int(sr * 0.035), len(pcm) // 4)
                if fi > 4:
                    pcm[:fi] *= 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, fi, dtype=np.float32)))
                if fo > 4:
                    pcm[-fo:] *= 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, fo, dtype=np.float32)))
                pcm[0] = 0.0
                pcm[-1] = 0.0
                out_pcm = np.clip(np.round(pcm), -32768, 32767).astype(np.int16)
                with wave.open(str(output_wav), "wb") as wf_out:
                    wf_out.setnchannels(1)
                    wf_out.setsampwidth(2)
                    wf_out.setframerate(sr)
                    wf_out.writeframes(out_pcm.tobytes())
        except Exception as exc:
            logger.debug(f"Bỏ qua bước làm mượt biên PCM: {exc}")
