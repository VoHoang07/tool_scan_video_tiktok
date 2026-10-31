import argparse
import json
import os
import random
import re
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Hỗ trợ chạy trực tiếp `python tiktok_pipeline/main.py` hoặc import như package
if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tiktok_pipeline.auto_poster import TikTokAutoPoster
    from tiktok_pipeline.composer import VideoComposer
    from tiktok_pipeline.crawler import (
        KNOWN_VIETNAMESE_HANDLES,
        StoryAudioCrawler,
        resolve_channel_profile,
    )
    from tiktok_pipeline.downloader import VideoDownloader
    from tiktok_pipeline.transcriber import AudioTranscriber
    from tiktok_pipeline.translator import SubtitleTranslator, has_vietnamese_diacritics
    from tiktok_pipeline.tts_engine import TTSEngine
    from tiktok_pipeline.utils import ensure_ffmpeg, logger, setup_logging
else:
    from .auto_poster import TikTokAutoPoster
    from .composer import VideoComposer
    from .crawler import (
        KNOWN_VIETNAMESE_HANDLES,
        StoryAudioCrawler,
        resolve_channel_profile,
    )
    from .downloader import VideoDownloader
    from .transcriber import AudioTranscriber
    from .translator import SubtitleTranslator, has_vietnamese_diacritics
    from .tts_engine import TTSEngine
    from .utils import ensure_ffmpeg, logger, setup_logging


def process_prepared_story(
    raw_video_path: Path,
    audio_path: Path,
    transcript_data: Dict[str, Any],
    job_work_dir: Path,
    target_output_dir: Path,
    platform: str,
    video_id: str,
    source_url: str,
    genre: str = "storytime",
    channel_folder: str = "general",
    voice: Optional[str] = None,
    llm_provider: Optional[str] = None,
    original_volume: Optional[float] = None,
    output_name: Optional[str] = None,
    original_title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Xử lý video theo ngôn ngữ gốc:
    - Nếu ngôn ngữ gốc đã là Tiếng Việt (`vi`): GIỮ NGUYÊN 100% video và âm thanh gốc
      (copy trực tiếp sang `output/channel_<N>/ready/`, không tạo kịch bản mới, không lồng tiếng lại).
    - Nếu ngôn ngữ gốc là Tiếng Trung / ngoại ngữ (`zh`, ...): Dịch trực tiếp sang Tiếng Việt
      -> Lồng tiếng Edge-TTS -> Làm mờ sub gốc & đóng phụ đề tiếng Việt.
    """
    detected_lang = (transcript_data.get("language") or "zh").lower().strip()
    raw_segments = transcript_data.get("segments") or []
    eff_title = original_title or str(transcript_data.get("original_title") or "")

    target_output_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_filename = output_name or f"{ts_str}_{platform}_story_{video_id}.mp4"
    rel_filename = (
        f"{channel_folder}/ready/{final_filename}"
        if channel_folder and channel_folder != "general"
        else final_filename
    )

    translator = SubtitleTranslator(provider=llm_provider)

    # TRƯỜNG HỢP 1: Video gốc đã là Tiếng Việt -> Giữ nguyên nội dung video & âm thanh gốc + Lách mã băm bản quyền TikTok
    if detected_lang == "vi":
        logger.info(
            "=== [3-5/5] VIDEO GỐC ĐÃ LÀ TIẾNG VIỆT ('vi') -> GIỮ NGUYÊN BẢN GỐC & LÁCH MÃ BĂM TRÙNG LẶP TIKTOK (MD5/vHash/AudioPrint) ==="
        )
        final_video_path = target_output_dir / final_filename
        VideoComposer.evade_tiktok_fingerprint(raw_video_path, final_video_path)
        StoryAudioCrawler.clear_macl_xattr(final_video_path)

        story_meta = translator.generate_story_metadata(
            segments=raw_segments,
            platform=platform,
            genre=genre,
            original_title=eff_title,
            src_lang="vi",
        )
        caption = story_meta["caption"]
        hashtags = story_meta["hashtags"]
        logger.info(f"Đã giữ nguyên video tiếng Việt tại: {final_video_path}")
        logger.info(f"Caption gốc: {caption}")
        logger.info(f"Hashtags: {hashtags}")

        return {
            "channel": channel_folder,
            "platform": platform,
            "video_id": video_id,
            "source_url": source_url,
            "detected_language": "vi",
            "segment_count": len(raw_segments),
            "raw_video": str(raw_video_path),
            "extracted_audio": str(audio_path),
            "final_video": str(final_video_path),
            "filename": rel_filename,
            "caption": caption,
            "hashtags": hashtags,
            "status": "READY",
            "posted_at": "",
        }

    # TRƯỜNG HỢP 2: Video gốc là Tiếng Trung / ngoại ngữ -> Dịch trực tiếp sang Tiếng Việt
    logger.info("=== [3/5] DỊCH TRỰC TIẾP LỜI THOẠI SANG TIẾNG VIỆT ===")
    translated_json = job_work_dir / "segments_translated.json"
    translated_segments = translator.translate_segments(
        segments=raw_segments,
        source_language=detected_lang,
        save_json_path=translated_json,
        channel=channel_folder or genre,
    )

    story_meta = translator.generate_story_metadata(
        segments=translated_segments,
        platform=platform,
        genre=genre,
        original_title=eff_title,
        src_lang=detected_lang,
    )
    caption = story_meta["caption"]
    hashtags = story_meta["hashtags"]
    logger.info(f"Caption đã dịch: {caption}")
    logger.info(f"Hashtags: {hashtags}")

    # Bước 4: Tinh chỉnh giọng đọc edge-tts theo từng kênh & đồng bộ tốc độ
    logger.info("=== [4/5] LỒNG TIẾNG VIỆT THEO KÊNH & CĂN CHỈNH TỐC ĐỘ (EDGE-TTS) ===")
    voice_cfg = VideoComposer.get_channel_voice_config(channel_folder or genre)
    logger.info(
        f"Cấu hình giọng kênh [{channel_folder}]: {voice_cfg['description']} "
        f"(voice={voice or voice_cfg['voice']}, rate={voice_cfg['base_rate']}, pitch={voice_cfg['pitch']})"
    )
    tts_engine = TTSEngine(
        work_dir=job_work_dir,
        voice=voice or voice_cfg["voice"],
        base_rate=voice_cfg["base_rate"],
        pitch=voice_cfg["pitch"],
    )
    voiced_segments = tts_engine.synthesize_segments(
        translated_segments,
        video_path=raw_video_path,
    )

    # Bước 5: Ghép âm thanh (ducking) & burn phụ đề bằng ffmpeg
    logger.info("=== [5/5] GHÉP ÂM THANH & ĐÓNG PHỤ ĐỀ LÊN VIDEO (FFMPEG) ===")
    composer = VideoComposer(
        work_dir=job_work_dir,
        output_dir=target_output_dir,
        original_volume=original_volume,
    )
    compose_result = composer.compose(
        raw_video_path=raw_video_path,
        segments=voiced_segments,
        output_filename=final_filename,
    )
    StoryAudioCrawler.clear_macl_xattr(Path(compose_result["final_video"]))

    return {
        "channel": channel_folder,
        "platform": platform,
        "video_id": video_id,
        "source_url": source_url,
        "detected_language": detected_lang,
        "segment_count": len(voiced_segments),
        "raw_video": str(raw_video_path),
        "extracted_audio": str(audio_path),
        "final_video": str(compose_result["final_video"]),
        "filename": rel_filename,
        "caption": caption,
        "hashtags": hashtags,
        "status": "READY",
        "posted_at": "",
    }


def run_pipeline(
    url: str,
    output_dir: Optional[Path] = None,
    work_dir: Optional[Path] = None,
    whisper_model: Optional[str] = None,
    voice: Optional[str] = None,
    llm_provider: Optional[str] = None,
    original_volume: Optional[float] = None,
    output_name: Optional[str] = None,
    genre: str = "storytime",
    channel: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    Chạy pipeline từ một URL TikTok/Douyin (hỗ trợ cả link video lẻ, danh sách link cách nhau dấu phẩy,
    hoặc link trang chủ kênh TikTok `https://www.tiktok.com/@handle`).
    """
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")
    setup_logging()

    # Hỗ trợ truyền nhiều URL cách nhau bởi dấu phẩy
    if "," in url and "http" in url:
        parts = [u.strip() for u in url.split(",") if u.strip()]
        if len(parts) > 1:
            results = []
            for p_url in parts:
                try:
                    results.append(
                        run_pipeline(
                            url=p_url,
                            output_dir=output_dir,
                            work_dir=work_dir,
                            whisper_model=whisper_model,
                            voice=voice,
                            llm_provider=llm_provider,
                            original_volume=original_volume,
                            output_name=None,
                            genre=genre,
                            channel=channel,
                            limit=limit,
                        )
                    )
                except Exception as exc:
                    logger.error(f"Lỗi khi xử lý URL {p_url}: {exc}")
            return {"batch_count": len(results), "items": results}

    start_time = time.time()
    ffmpeg_bin, _ = ensure_ffmpeg()
    logger.info(f"Đã xác nhận công cụ hệ thống ffmpeg: {ffmpeg_bin}")

    from .lean_context import LeanContextGateway

    ctx_status = LeanContextGateway(project_root=project_root).get_engine_status()
    logger.info(
        "[Open-Source Engine #1] Đã kích hoạt macos-desktop-control "
        "(d-wwei/macos-desktop-control | CoreGraphics CGEvent OS Hardware Mouse & Keyboard)"
    )
    logger.info(
        f"[Open-Source Engine #2] Đã kích hoạt LeanCTX "
        f"(yvgude/lean-ctx | SDE Target-Density={ctx_status['target_density']} | version={ctx_status['version']})"
    )

    base_output_dir = Path(output_dir) if output_dir else (project_root / "output")
    crawler = StoryAudioCrawler(project_root=project_root, whisper_model=whisper_model)

    channel_folder = "general"
    target_output_dir = base_output_dir
    if channel:
        profile = resolve_channel_profile(channel)
        channel_folder = profile["folder_name"]
        target_output_dir = base_output_dir / channel_folder / "ready"
        genre = profile["profile_key"]

    target_output_dir.mkdir(parents=True, exist_ok=True)

    downloader = VideoDownloader(work_dir=project_root / "workspace_temp")
    clean_url = downloader.extract_url_from_text(url)

    # Nếu URL là link kênh TikTok (ví dụ https://www.tiktok.com/@tieutin2000) -> lấy các video trong kênh đó
    if "tiktok.com/@" in clean_url and "/video/" not in clean_url and "/photo/" not in clean_url:
        logger.info(f"Phát hiện URL kênh TikTok ({clean_url}) -> Đang lấy danh sách video trong kênh...")
        ch_candidates = crawler.discover_tiktok_channel_videos(
            handle_or_url=clean_url,
            genre_label=genre,
            max_videos=max(10, limit * 3),
        )
        processed_items = []
        for cand in ch_candidates:
            if len(processed_items) >= limit:
                break
            try:
                res = run_pipeline(
                    url=cand["source_url"],
                    output_dir=output_dir,
                    work_dir=work_dir,
                    whisper_model=whisper_model,
                    voice=voice,
                    llm_provider=llm_provider,
                    original_volume=original_volume,
                    output_name=None,
                    genre=genre,
                    channel=channel,
                )
                processed_items.append(res)
            except Exception as exc:
                logger.warning(f"Bỏ qua video {cand['video_id']} trong kênh: {exc}")
        return {"channel_url": clean_url, "processed_count": len(processed_items), "items": processed_items}

    temp_root = Path(work_dir) if work_dir else (project_root / "workspace_temp")
    temp_root.mkdir(parents=True, exist_ok=True)

    platform = downloader.detect_platform(clean_url)
    resolved_url, detected_vid = downloader.resolve_short_url(clean_url, platform)
    video_id = detected_vid or str(int(time.time()))
    job_tag = f"{platform}_{video_id}"
    job_work_dir = temp_root / job_tag
    job_work_dir.mkdir(parents=True, exist_ok=True)

    downloader.work_dir = job_work_dir
    logger.info(f"=== [1/5] TẢI VIDEO & TÁCH ÂM THANH ({platform.upper()}) ===")
    dl_info = downloader.download(clean_url)
    raw_video_path: Path = dl_info["video_path"]
    audio_path: Path = dl_info["audio_path"]
    dl_title = str(dl_info.get("title") or "")
    if dl_info.get("video_id") and dl_info["video_id"] != "unknown":
        video_id = str(dl_info["video_id"])

    handle_match = re.search(r"tiktok\.com/@([^/?#]+)", resolved_url)
    author_handle = (handle_match.group(1) if handle_match else "").lower()

    if author_handle in KNOWN_VIETNAMESE_HANDLES or has_vietnamese_diacritics(dl_title):
        logger.info("=== [2/5] PHÁT HIỆN VIDEO TIẾNG VIỆT CÓ SẴN -> BỎ QUA WHISPER/TTS, GIỮ NGUYÊN BẢN GỐC ===")
        transcript_data: Dict[str, Any] = {
            "language": "vi",
            "segments": [],
            "original_title": dl_title,
        }
    else:
        logger.info("=== [2/5] NHẬN DIỆN GIỌNG NÓI & NGÔN NGỮ GỐC (FASTER-WHISPER) ===")
        transcriber = AudioTranscriber(model_size=whisper_model)
        transcript_json = job_work_dir / "transcript_raw.json"
        transcript_data = transcriber.transcribe(
            audio_path=audio_path,
            save_json_path=transcript_json,
            video_path=raw_video_path,
        )
        transcript_data["original_title"] = dl_title
        detected_lang = (transcript_data.get("language") or "zh").lower().strip()
        if detected_lang not in ("zh", "yue", "cmn", "vi"):
            shutil.rmtree(job_work_dir, ignore_errors=True)
            raise ValueError(
                f"Loại bỏ video {video_id}: Ngôn ngữ '{detected_lang}' không phải Tiếng Trung ('zh') hoặc Tiếng Việt ('vi')."
            )

    summary = process_prepared_story(
        raw_video_path=raw_video_path,
        audio_path=audio_path,
        transcript_data=transcript_data,
        job_work_dir=job_work_dir,
        target_output_dir=target_output_dir,
        platform=platform,
        video_id=video_id,
        source_url=dl_info.get("resolved_url") or clean_url,
        genre=genre,
        channel_folder=channel_folder,
        voice=voice,
        llm_provider=llm_provider,
        original_volume=original_volume,
        output_name=output_name,
        original_title=dl_title,
    )

    crawler.save_to_history(
        video_id=video_id,
        sound_id=f"{platform}_sound_{video_id}",
        platform=platform,
        genre=genre,
        source_url=summary["source_url"],
        output_filename=summary["filename"],
        channel=channel_folder,
    )
    crawler.append_to_master_csv(
        filename=summary["filename"],
        source_url=summary["source_url"],
        caption=summary["caption"],
        hashtags=summary["hashtags"],
        status="READY",
        channel=channel_folder,
        posted_at="",
    )

    elapsed = time.time() - start_time
    summary["elapsed_seconds"] = round(elapsed, 2)
    (job_work_dir / "pipeline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        f"HOÀN TẤT PIPELINE TRONG {elapsed:.1f}s! "
        f"Video đầu ra: {summary['final_video']}"
    )
    return summary


def run_crawl_story_mode(
    platform: Optional[str] = None,
    genre: str = "情感故事",
    limit: int = 3,
    channel: Optional[str] = None,
    output_dir: Optional[Path] = None,
    whisper_model: Optional[str] = None,
    voice: Optional[str] = None,
    llm_provider: Optional[str] = None,
    original_volume: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Chế độ cào tự động theo Kênh (`--channel 1..4`) hoặc theo Chủ đề (`--mode crawl-story`):
    1. Nạp profile kênh (từ khóa Douyin + TikTok) hoặc chủ đề chỉ định.
    2. Lọc thời lượng 30s - 120s, tối thiểu 20,000 Likes, loại bỏ Livestream/Bán hàng/Ảnh tĩnh,
       chống trùng lặp (`story_audio_history.json`).
    3. Kiểm tra mật độ giọng nói bằng Whisper (số câu >= 3 VÀ thời gian thoại >= 50% thời lượng video).
    4. Tự động đẩy qua pipeline Dịch -> Lồng tiếng -> Đóng phụ đề -> Lưu vào `output/channel_<N>/ready/`
       và cập nhật `output/danh_sach_video.csv` với `status=READY`.
    """
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")
    setup_logging()

    ffmpeg_bin, _ = ensure_ffmpeg()
    logger.info(f"Đã xác nhận công cụ hệ thống ffmpeg: {ffmpeg_bin}")

    from .lean_context import LeanContextGateway

    ctx_status = LeanContextGateway(project_root=project_root).get_engine_status()
    logger.info(
        "[Open-Source Engine #1] Đã kích hoạt macos-desktop-control "
        "(d-wwei/macos-desktop-control | CoreGraphics CGEvent OS Hardware Mouse & Keyboard)"
    )
    logger.info(
        f"[Open-Source Engine #2] Đã kích hoạt LeanCTX "
        f"(yvgude/lean-ctx | SDE Target-Density={ctx_status['target_density']} | version={ctx_status['version']})"
    )

    base_output_dir = Path(output_dir) if output_dir else (project_root / "output")
    base_output_dir.mkdir(parents=True, exist_ok=True)

    crawler = StoryAudioCrawler(
        project_root=project_root,
        min_duration=15.0,
        max_duration=900.0,
        min_segments=2,
        min_speech_ratio=0.25,
        min_likes=5000,
        whisper_model=whisper_model,
    )

    if channel:
        profile, candidates = crawler.discover_for_channel(
            channel_arg=channel,
            platform_override=platform,
            max_candidates=max(30, limit * 12),
        )
        channel_folder = profile["folder_name"]
        profile_key = profile["profile_key"]
        target_output_dir = base_output_dir / channel_folder / "ready"
        target_output_dir.mkdir(parents=True, exist_ok=True)
        effective_genre = profile_key
        logger.info(
            f"=== BẮT ĐẦU CÀO KÊNH [{profile_key} -> output/{channel_folder}/ready/ | Số lượng cần lấy: {limit}] ==="
        )
    else:
        eff_platform = platform or "douyin"
        channel_folder = "general"
        target_output_dir = base_output_dir
        effective_genre = genre
        logger.info(
            f"=== BẮT ĐẦU CÀO AUDIO KỂ CHUYỆN [{eff_platform.upper()} | Chủ đề: '{genre}' | Số lượng cần lấy: {limit}] ==="
        )
        candidates = crawler.discover_candidates(
            platform=eff_platform,
            genre=genre,
            max_candidates=max(30, limit * 12),
        )

    completed_results: List[Dict[str, Any]] = []
    for idx, cand in enumerate(candidates, start=1):
        if len(completed_results) >= limit:
            break

        logger.info(
            f"--- Kiểm tra ứng viên #{idx}/{len(candidates)}: [{cand['platform'].upper()}] ID={cand['video_id']} "
            f"(tim={cand['digg_count']}, thời lượng={cand['duration']:.1f}s) ---"
        )
        passed, bundle = crawler.verify_voice_density(cand)
        if not passed or not bundle:
            continue

        start_t = time.time()
        try:
            summary = process_prepared_story(
                raw_video_path=bundle["raw_video_path"],
                audio_path=bundle["audio_path"],
                transcript_data=bundle["transcript_data"],
                job_work_dir=bundle["work_dir"],
                target_output_dir=target_output_dir,
                platform=cand["platform"],
                video_id=cand["video_id"],
                source_url=cand["source_url"],
                genre=effective_genre,
                channel_folder=channel_folder,
                voice=voice,
                llm_provider=llm_provider,
                original_volume=original_volume,
                original_title=bundle.get("original_title") or cand.get("title"),
            )
            summary["sound_id"] = cand["sound_id"]
            summary["speech_ratio"] = bundle["speech_ratio"]
            summary["spoken_seconds"] = bundle["spoken_seconds"]
            summary["elapsed_seconds"] = round(time.time() - start_t, 2)

            crawler.save_to_history(
                video_id=cand["video_id"],
                sound_id=cand["sound_id"],
                platform=cand["platform"],
                genre=cand.get("genre") or effective_genre,
                source_url=cand["source_url"],
                output_filename=summary["filename"],
                channel=channel_folder,
            )
            crawler.append_to_master_csv(
                filename=summary["filename"],
                source_url=cand["source_url"],
                caption=summary["caption"],
                hashtags=summary["hashtags"],
                status="READY",
                channel=channel_folder,
                posted_at="",
            )
            completed_results.append(summary)
            logger.info(
                f"==> Đã hoàn thành {len(completed_results)}/{limit} video cho [{channel_folder}]: {summary['final_video']}"
            )
        except Exception as exc:
            logger.error(f"Lỗi khi xử lý video {cand['video_id']}: {exc}", exc_info=True)
            continue

    return completed_results


def _compute_smart_sleep_seconds(sleep_between: Optional[int] = None) -> int:
    """
    Tính thời gian nghỉ an toàn (Smart Sleep) giữa các video:
    - Mặc định (khi `sleep_between=None`): ngẫu nhiên từ 120s đến 300s (2 - 5 phút).
    - Khi người dùng truyền `--sleep-between N`: dao động ngẫu nhiên ±15% quanh N giây.
    """
    if sleep_between is None:
        return random.randint(120, 300)
    if sleep_between <= 0:
        return 0
    if sleep_between < 10:
        return sleep_between
    low = max(1, int(round(sleep_between * 0.85)))
    high = max(low, int(round(sleep_between * 1.15)))
    return random.randint(low, high)


def _get_codebase_mtime_snapshot(project_root: Path) -> Dict[str, float]:
    """Lấy bản chụp thời gian sửa đổi (mtime) của tất cả file .py để tự động Hot-Reload khi sửa code."""
    snapshot: Dict[str, float] = {}
    for py_file in sorted(project_root.glob("*.py")) + sorted((project_root / "tiktok_pipeline").glob("*.py")):
        try:
            if py_file.is_file():
                snapshot[str(py_file.relative_to(project_root))] = round(py_file.stat().st_mtime, 2)
        except Exception:
            pass
    return snapshot


def _auto_git_pull_if_available(project_root: Path) -> bool:
    """
    Nếu thư mục dự án đã kết nối Git Remote (GitHub), tự động kéo code/cấu hình mới nhất
    từ máy Apple Silicon về máy Mac Intel bằng `git fetch` + `git reset --hard origin/main`
    (bảo toàn `story_audio_history.json` và `output/danh_sach_video.csv` của máy Intel,
    không bao giờ bị kẹt lỗi merge conflict hay thiếu git user.email).
    """
    git_dir = project_root / ".git"
    if not git_dir.exists():
        return False
    try:
        import shutil
        import subprocess
        import tempfile

        for lf_name in ("index.lock", "HEAD.lock", "refs/heads/main.lock"):
            lf_path = git_dir / lf_name
            if lf_path.exists():
                try:
                    lf_path.unlink()
                except Exception:
                    pass
        if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
            shutil.rmtree(git_dir / "rebase-merge", ignore_errors=True)
            shutil.rmtree(git_dir / "rebase-apply", ignore_errors=True)

        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        local_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
        remote_sha = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()

        if local_sha and remote_sha and local_sha != remote_sha:
            # Có code mới từ Silicon
            commit_msg = subprocess.run(
                ["git", "log", "origin/main", "-1", "--format=%s"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout.strip()
            logger.info(
                f"[Auto-Git-Sync] 🔄 Phát hiện code mới! ({local_sha[:7]} -> {remote_sha[:7]}) "
                f"| {commit_msg}"
            )
            pid = os.getpid()
            tmp_dir = Path(tempfile.gettempdir())
            hist_path = project_root / "story_audio_history.json"
            csv_path = project_root / "output" / "danh_sach_video.csv"
            reg_path = project_root / "uploaded_videos_registry.json"
            post_log_path = project_root / "posted_schedule_history.json"
            hist_bak = tmp_dir / f"tiktok_story_audio_history_{pid}.bak.json"
            csv_bak = tmp_dir / f"tiktok_danh_sach_video_{pid}.bak.csv"
            reg_bak = tmp_dir / f"tiktok_uploaded_videos_registry_{pid}.bak.json"
            post_log_bak = tmp_dir / f"tiktok_posted_schedule_history_{pid}.bak.json"

            if hist_path.exists():
                shutil.copy2(hist_path, hist_bak)
            if csv_path.exists():
                shutil.copy2(csv_path, csv_bak)
            if reg_path.exists():
                shutil.copy2(reg_path, reg_bak)
            if post_log_path.exists():
                shutil.copy2(post_log_path, post_log_bak)

            subprocess.run(
                ["git", "checkout", "-f", "-B", "main", "origin/main"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

            if hist_bak.exists():
                shutil.copy2(hist_bak, hist_path)
                hist_bak.unlink(missing_ok=True)
            if csv_bak.exists():
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(csv_bak, csv_path)
                csv_bak.unlink(missing_ok=True)
            if reg_bak.exists():
                shutil.copy2(reg_bak, reg_path)
                reg_bak.unlink(missing_ok=True)
            if post_log_bak.exists():
                try:
                    bak_dict = json.loads(post_log_bak.read_text(encoding="utf-8"))
                    cur_dict = (
                        json.loads(post_log_path.read_text(encoding="utf-8"))
                        if post_log_path.exists()
                        else {}
                    )
                    merged = {**bak_dict, **cur_dict}
                    post_log_path.write_text(
                        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                except Exception:
                    shutil.copy2(post_log_bak, post_log_path)
                post_log_bak.unlink(missing_ok=True)

            logger.info(
                f"[Auto-Git-Sync] Đã tự động cập nhật code mới từ GitHub ({local_sha[:7]} -> {remote_sha[:7]})!"
            )
            return True
    except Exception as exc:
        logger.debug(f"[Auto-Git-Sync] Bỏ qua kiểm tra git: {exc}")
    return False


def run_continuous_mode(
    max_ready: int = 5,
    sleep_between: Optional[int] = None,
    max_cycles: Optional[int] = None,
    platform: Optional[str] = None,
    output_dir: Optional[Path] = None,
    whisper_model: Optional[str] = None,
    voice: Optional[str] = None,
    llm_provider: Optional[str] = None,
    original_volume: Optional[float] = None,
    enable_stream2_filter24h: bool = True,
) -> Dict[str, Any]:
    """
    Chế độ CÀO VÀ XỬ LÝ LIÊN TỤC (Daemon / Continuous Worker Mode):
    - Xoay vòng tuần tự 4 kênh (Round-Robin Engine): Kênh 1 -> Kênh 2 -> Kênh 3 -> Kênh 4 -> Nghỉ ngơi -> Lặp lại.
    - Buffer Guard: Kiểm tra số lượng video trong `output/channel_X/ready/`. Nếu >= `max_ready` thì bỏ qua.
    - Fault Tolerance: Bọc toàn bộ quy trình tải & render trong `try...except`, tự động bỏ qua lỗi để không dừng daemon.
    - Smart Sleep: Nghỉ ngẫu nhiên 2-5 phút (hoặc theo `--sleep-between`) sau mỗi video hoàn tất để tránh chặn IP.
    - Graceful Shutdown: Hỗ trợ dừng mượt mà khi nhận tín hiệu `Ctrl + C` (`SIGINT` / `SIGTERM`).
    """
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")
    setup_logging()

    ffmpeg_bin, _ = ensure_ffmpeg()
    logger.info(f"Đã xác nhận công cụ hệ thống ffmpeg: {ffmpeg_bin}")

    from .lean_context import LeanContextGateway

    ctx_status = LeanContextGateway(project_root=project_root).get_engine_status()
    logger.info(
        "[Open-Source Engine #1] Đã kích hoạt macos-desktop-control "
        "(d-wwei/macos-desktop-control | CoreGraphics CGEvent OS Hardware Mouse & Keyboard)"
    )
    logger.info(
        f"[Open-Source Engine #2] Đã kích hoạt LeanCTX "
        f"(yvgude/lean-ctx | SDE Target-Density={ctx_status['target_density']} | version={ctx_status['version']})"
    )

    base_output_dir = Path(output_dir) if output_dir else (project_root / "output")
    base_output_dir.mkdir(parents=True, exist_ok=True)

    crawler = StoryAudioCrawler(
        project_root=project_root,
        min_duration=15.0,
        max_duration=900.0,
        min_segments=2,
        min_speech_ratio=0.25,
        min_likes=5000,
        whisper_model=whisper_model,
    )

    stop_requested = False

    def _signal_handler(signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        print(
            f"\n[Shutdown] Nhận tín hiệu dừng ({sig_name} / Ctrl+C). Đang dừng ngay lập tức...",
            flush=True,
        )
        logger.info(f"Nhận tín hiệu dừng {sig_name} (Ctrl+C).")
        stop_requested = True
        raise KeyboardInterrupt()

    prev_sigint = signal.signal(signal.SIGINT, _signal_handler)
    prev_sigterm = signal.signal(signal.SIGTERM, _signal_handler)

    poster = TikTokAutoPoster(project_root=project_root)
    initial_code_snapshot = _get_codebase_mtime_snapshot(project_root)

    import threading

    scheduler_lock = threading.Lock()

    def _reload_poster_if_updated() -> None:
        nonlocal poster, crawler, initial_code_snapshot
        pulled = _auto_git_pull_if_available(project_root)
        cur_snap = _get_codebase_mtime_snapshot(project_root)
        if pulled or cur_snap != initial_code_snapshot:
            initial_code_snapshot = cur_snap
            try:
                import importlib
                from . import crawler as _cr_mod
                from . import translator as _tr_mod
                from . import growth_24h_filter as _g24_mod
                from . import auto_poster as _ap_mod

                importlib.reload(_cr_mod)
                importlib.reload(_tr_mod)
                importlib.reload(_g24_mod)
                importlib.reload(_ap_mod)
                crawler = _cr_mod.StoryAudioCrawler(
                    project_root=project_root,
                    min_duration=15.0,
                    max_duration=900.0,
                    min_segments=2,
                    min_speech_ratio=0.25,
                    min_likes=5000,
                    whisper_model=whisper_model,
                )
                crawler.auto_sync_posted_videos()
                poster = _ap_mod.TikTokAutoPoster(project_root=project_root)
                logger.info("[Hot-Reload In-Place] Đã nạp nóng code mới cho Bộ Cào Trung/Việt View Cao & 2 Dòng Hoạt Động!")
            except Exception as rel_exc:
                logger.debug(f"[Hot-Reload In-Place] Bỏ qua lỗi nạp nóng phụ: {rel_exc}")

    def _run_stream1_upload_safe() -> None:
        """Dòng hoạt động 1: Giữ nguyên khung giờ đăng video cho 4 kênh (Ưu tiên cao)."""
        if not scheduler_lock.acquire(blocking=False):
            return
        try:
            _reload_poster_if_updated()
            crawler.auto_sync_posted_videos()
            poster.check_and_run_stream1_upload_schedule()
        except Exception as sched_exc:
            logger.debug(f"[Dòng 1 - Upload Scheduler] Bỏ qua lỗi phụ: {sched_exc}")
        finally:
            scheduler_lock.release()

    def _run_stream2_filter24h_safe() -> None:
        """Dòng hoạt động 2: Mở 4 kênh chạy Bộ lọc 24 giờ (7 lần/ngày × 10 video/kênh)."""
        if not scheduler_lock.acquire(blocking=False):
            return
        try:
            _reload_poster_if_updated()
            poster.check_and_run_warmup_browse()
            poster.check_and_run_stream2_filter24h_schedule()
        except Exception as sched_exc:
            logger.debug(f"[Dòng 2 - Filter 24h Scheduler] Bỏ qua lỗi phụ: {sched_exc}")
        finally:
            scheduler_lock.release()

    def _run_scheduler_safe() -> None:
        _run_stream1_upload_safe()
        if enable_stream2_filter24h:
            _run_stream2_filter24h_safe()

    def _stream1_upload_loop() -> None:
        """Luồng chạy ngầm Dòng 1: Kiểm tra lịch đăng video cố định của 4 kênh mỗi 30 giây."""
        while not stop_requested:
            time.sleep(30)
            if stop_requested:
                break
            _run_stream1_upload_safe()

    def _stream2_filter24h_loop() -> None:
        """Luồng chạy ngầm Dòng 2: Mở 4 kênh chạy Bộ lọc 24 giờ (7 lần/ngày × 10 video/kênh) mỗi 45 giây."""
        while not stop_requested:
            time.sleep(45)
            if stop_requested:
                break
            _run_stream2_filter24h_safe()

    stream1_thread = threading.Thread(
        target=_stream1_upload_loop, name="Stream1-UploadSchedulerThread", daemon=True
    )
    stream1_thread.start()
    if enable_stream2_filter24h:
        stream2_thread = threading.Thread(
            target=_stream2_filter24h_loop, name="Stream2-Filter24hThread", daemon=True
        )
        stream2_thread.start()

    def _check_hot_reload() -> None:
        if scheduler_lock.locked():
            return
        _reload_poster_if_updated()

    def _interruptible_sleep(seconds: int, reason: str) -> None:
        if seconds <= 0 or stop_requested:
            return
        logger.info(
            f"[Smart Sleep] {reason}: Nghỉ an toàn {seconds}s (~{seconds / 60:.1f} phút) "
            "để tránh bị Douyin/TikTok chặn IP & canh giờ tự động đăng bài (Nhấn Ctrl+C để dừng)..."
        )
        for sec_idx in range(seconds):
            if stop_requested:
                break
            if sec_idx > 0 and sec_idx % 30 == 0:
                try:
                    _check_hot_reload()
                    _run_scheduler_safe()
                except SystemExit:
                    raise
                except Exception:
                    pass
            time.sleep(1)

    def _get_active_channel_sequence() -> List[str]:
        try:
            cfg = poster.ensure_schedule_config()
            act = cfg.get("active_channels")
            if isinstance(act, list) and act:
                return [str(x).strip() for x in act if str(x).strip()]
        except Exception:
            pass
        return ["1", "2", "3", "4"]

    channel_sequence = _get_active_channel_sequence()
    cycle_count = 0
    total_produced = 0
    produced_videos: List[Dict[str, Any]] = []

    logger.info(
        f"=== KHỞI ĐỘNG CHẾ ĐỘ CONTINUOUS WORKER 24/7 (ROUND-ROBIN {len(channel_sequence)} KÊNH + AUTO POST SCHEDULER) | "
        f"max_ready={max_ready} video/kênh | "
        f"sleep_between={f'{sleep_between}s' if sleep_between is not None else 'ngẫu nhiên 120s-300s (2-5 phút)'} ==="
    )

    try:
        while not stop_requested:
            cycle_count += 1
            produced_in_cycle = 0
            pending_smart_sleep_from_channel: Optional[str] = None
            channel_sequence = _get_active_channel_sequence()
            print("\n" + "=" * 94, flush=True)
            logger.info(
                f"=== [CHU KỲ #{cycle_count}] BẮT ĐẦU VÒNG LẶP KIỂM TRA, CÀO & CANH GIỜ ĐĂNG ({len(channel_sequence)} KÊNH ĐANG BẬT: {channel_sequence}) ==="
            )

            # Kiểm tra cập nhật code tự động (Hot-Reload) & lịch đăng tự động ở đầu mỗi chu kỳ
            _check_hot_reload()
            _run_scheduler_safe()

            for ch_id in channel_sequence:
                if stop_requested:
                    break

                # Kiểm tra Hot-Reload & lịch đăng ngay trước mỗi kênh để phản hồi tức thì
                _check_hot_reload()
                _run_scheduler_safe()

                try:
                    # Tự động đồng bộ trạng thái READY <-> POSTED trước khi đếm kho
                    crawler.auto_sync_posted_videos()

                    profile = resolve_channel_profile(ch_id)
                    ch_folder = profile["folder_name"]
                    profile_key = profile["profile_key"]
                    ready_dir = base_output_dir / ch_folder / "ready"
                    ready_dir.mkdir(parents=True, exist_ok=True)

                    # 1. Buffer Guard: Đếm số lượng file .mp4 hiện có trong output/channel_X/ready/
                    ready_files = [f for f in ready_dir.glob("*.mp4") if f.is_file()]
                    ready_count = len(ready_files)

                    if ready_count >= max_ready:
                        msg = (
                            f"Kênh {ch_id} ({ch_folder} - {profile_key}) đã đủ kho video chờ đăng "
                            f"({ready_count}/{max_ready}), bỏ qua"
                        )
                        print(f"[Buffer Guard] {msg}", flush=True)
                        logger.info(msg)
                        continue

                    # 4. Smart Sleep giữa các lần cào video để tránh bị Douyin/TikTok chặn IP
                    if pending_smart_sleep_from_channel and not stop_requested:
                        sleep_sec = _compute_smart_sleep_seconds(sleep_between)
                        _interruptible_sleep(
                            sleep_sec,
                            reason=f"Nghỉ an toàn sau khi hoàn thành video {pending_smart_sleep_from_channel}",
                        )
                        pending_smart_sleep_from_channel = None
                        if stop_requested:
                            break

                    logger.info(
                        f"[Round-Robin] Kênh {ch_id} ({ch_folder}) hiện có {ready_count}/{max_ready} video trong ready/. "
                        f"Bắt đầu cào và xử lý 1 video mới..."
                    )

                    # 2 & 3. Cào và xử lý 1 video hoàn chỉnh cho kênh hiện tại
                    ch_results = run_crawl_story_mode(
                        platform=platform,
                        limit=1,
                        channel=ch_id,
                        output_dir=base_output_dir,
                        whisper_model=whisper_model,
                        voice=voice,
                        llm_provider=llm_provider,
                        original_volume=original_volume,
                    )

                    if ch_results:
                        produced_in_cycle += len(ch_results)
                        total_produced += len(ch_results)
                        produced_videos.extend(ch_results)
                        updated_ready_count = len(
                            [f for f in ready_dir.glob("*.mp4") if f.is_file()]
                        )
                        logger.info(
                            f"[Round-Robin] Hoàn tất 1 video mới cho Kênh {ch_id} ({ch_folder})! "
                            f"Kho chờ ready/ hiện tại: {updated_ready_count}/{max_ready}"
                        )
                        pending_smart_sleep_from_channel = f"Kênh {ch_id} ({ch_folder})"
                    else:
                        logger.warning(
                            f"[Round-Robin] Chưa tìm được ứng viên mới đạt chuẩn cho Kênh {ch_id} trong lượt này. "
                            "Chuyển sang kênh kế tiếp..."
                        )

                except KeyboardInterrupt:
                    stop_requested = True
                    logger.info("Nhận lệnh dừng từ bàn phím (Ctrl+C). Đang kết thúc chu kỳ...")
                    break
                except Exception as ch_exc:
                    # Bảo vệ chống sập chương trình (Fault Tolerance)
                    print(
                        f"[Fault Tolerance] Lỗi khi xử lý Kênh {ch_id}: {ch_exc}. "
                        "Bỏ qua và tiếp tục chạy kênh tiếp theo...",
                        flush=True,
                    )
                    logger.error(
                        f"[Fault Tolerance] Lỗi ở Kênh {ch_id}, tự động phục hồi và chuyển kênh kế tiếp: {ch_exc}",
                        exc_info=True,
                    )
                    continue

            # In bảng thống kê tồn kho sau mỗi chu kỳ (Kênh 1 -> 2 -> 3 -> 4)
            print("\n" + crawler.format_inventory_status_table() + "\n", flush=True)

            if stop_requested:
                break

            if max_cycles is not None and cycle_count >= max_cycles:
                logger.info(
                    f"Đã hoàn thành đủ {cycle_count}/{max_cycles} chu kỳ theo giới hạn `--max-cycles`. Dừng worker."
                )
                break

            # Kiểm tra xem các kênh đang bật (active_channels) đã đầy kho chờ ready/ chưa
            inv = crawler.get_inventory_status()
            active_folders = {resolve_channel_profile(c)["folder_name"] for c in channel_sequence}
            all_channels_full = all(
                ch_stat["ready_count"] >= max_ready
                for ch_stat in inv["channels"]
                if ch_stat.get("folder_name") in active_folders
            )
            if all_channels_full and produced_in_cycle == 0:
                if max_cycles is not None:
                    logger.info(
                        f"[Continuous Worker] Tất cả 4 kênh đều đã đủ kho video chờ đăng ({max_ready}/{max_ready} video/kênh). "
                        "Hoàn tất chu kỳ kiểm tra & lấp đầy kho!"
                    )
                    break
                logger.info(
                    f"[24/7 Worker] Cả 4 kênh đều đã đầy kho chờ ({max_ready}/{max_ready}). "
                    "Đang canh khung giờ tự động đăng bài (kiểm tra lại sau 60s)..."
                )
                _interruptible_sleep(60, reason="Kho 4 kênh đã đầy, chờ đến khung giờ đăng bài")
                continue

            if all_channels_full and produced_in_cycle > 0:
                logger.info(
                    f"[Continuous Worker] Cả 4 kênh vừa đạt mốc {max_ready}/{max_ready} video. "
                    "Chuyển sang chu kỳ kiểm tra xác nhận..."
                )
                continue

            # Nghỉ ngơi cuối chu kỳ trước khi lặp lại vòng mới (Kênh 1 -> 2 -> 3 -> 4 -> Nghỉ ngơi -> Lặp lại)
            cycle_sleep = _compute_smart_sleep_seconds(sleep_between)
            _interruptible_sleep(
                cycle_sleep,
                reason=f"Hoàn tất Chu kỳ #{cycle_count} (đã xuất {produced_in_cycle} video mới), nghỉ ngơi trước khi lặp lại",
            )

    except KeyboardInterrupt:
        stop_requested = True
        logger.info("Đã nhận Ctrl+C (Graceful Shutdown).")
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)
        logger.info(
            f"=== ĐÃ DỪNG CONTINUOUS WORKER AN TOÀN (Tổng số chu kỳ: {cycle_count} | "
            f"Tổng số video mới đã xuất: {total_produced}) ==="
        )

    if stop_requested:
        sys.exit(130)

    return {
        "cycles_completed": cycle_count,
        "total_produced": total_produced,
        "max_ready": max_ready,
        "produced_videos": produced_videos,
    }


def run_watch_sync_mode(interval_seconds: int = 5) -> None:
    """
    Chế độ theo dõi ngầm (Auto-Sync Watcher Daemon):
    Quét định kỳ mỗi `interval_seconds` giây trên cả 4 kênh (`channel_1` .. `channel_4`).
    Ngay khi người dùng chọn upload bất kỳ video nào từ `ready/` lên TikTok qua trình duyệt
    (hoặc đánh dấu trong CSV / kéo thả sang `posted/`), hệ thống tự động di chuyển file sang `posted/`
    và cập nhật `output/danh_sach_video.csv` (`status = POSTED`, `posted_at`).
    """
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")
    setup_logging()
    crawler = StoryAudioCrawler(project_root=project_root)

    logger.info(
        f"=== KHỞI ĐỘNG AUTO-SYNC WATCHER (ĐỒNG BỘ TỰ ĐỘNG 4 KÊNH MỖI {interval_seconds}s) ==="
    )
    poster = TikTokAutoPoster(project_root=project_root)
    tick = 0
    try:
        while True:
            moved = crawler.auto_sync_posted_videos(min_age_seconds=35.0)
            if moved:
                print("\n" + crawler.format_inventory_status_table() + "\n", flush=True)
            tick += 1
            if tick % 6 == 0:
                if _auto_git_pull_if_available(project_root):
                    poster = _reload_poster_if_updated(project_root, poster)
                poster._auto_revive_stream1_upload_if_dead()
            time.sleep(max(2, interval_seconds))
    except KeyboardInterrupt:
        logger.info("Đã dừng Auto-Sync Watcher.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "TikTok & Douyin Automated 4-Channel Storytelling Crawler, Dubbing & Subtitle Pipeline"
        )
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help=(
            "Bật chế độ Cào & Xử lý liên tục (Daemon / Continuous Worker Mode): "
            "xoay vòng Kênh 1 -> 2 -> 3 -> 4, tự động kiểm tra kho ready/ và lấp đầy"
        ),
    )
    parser.add_argument(
        "--max-ready",
        type=int,
        default=5,
        help="Giới hạn số lượng video chờ đăng tối đa trong ready/ của mỗi kênh khi chạy --continuous (mặc định: 5)",
    )
    parser.add_argument(
        "--sleep-between",
        type=int,
        default=None,
        help=(
            "Thời gian nghỉ (giây) giữa các video trong chế độ --continuous "
            "(mặc định: ngẫu nhiên từ 120 đến 300 giây / 2-5 phút để tránh chặn IP)"
        ),
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Tùy chọn giới hạn số chu kỳ xoay vòng khi chạy --continuous (mặc định: chạy liên tục cho đến khi Ctrl+C)",
    )
    parser.add_argument(
        "--channel",
        default=None,
        help=(
            "Chọn kênh TikTok chuyên biệt: 'all' (tất cả 4 kênh), "
            "'1,2,3', hoặc từng kênh (1: AI Animation | 2: Life Comedy | 3: Novel Drama | 4: AI Cultivation)"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["single", "crawl-story"],
        default="single",
        help="Chế độ chạy: 'single' (tải 1 URL cụ thể) hoặc 'crawl-story' (tự động săn audio kể chuyện viral)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Đường dẫn URL / Shortlink video TikTok hoặc Douyin (ví dụ: https://v.douyin.com/Mr4JFtYJ-lg/)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Tự động đồng bộ 4 kênh và in bảng thống kê số lượng video trong ready/ và posted/",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Tự động quét và đồng bộ các video đã chọn upload lên TikTok (từ ready/ sang posted/) trên cả 4 kênh",
    )
    parser.add_argument(
        "--watch-sync",
        action="store_true",
        help="Chạy ngầm tự động theo dõi & chuyển video đã upload lên TikTok từ ready/ sang posted/ cho cả 4 kênh",
    )
    parser.add_argument(
        "--mark-posted",
        default=None,
        help=(
            "Đánh dấu video đã đăng lên TikTok (nhập đường dẫn file, tên file hoặc ID): "
            "tự động chuyển từ ready/ sang posted/ và cập nhật CSV (status=POSTED, posted_at)"
        ),
    )
    parser.add_argument(
        "--login-channel",
        default=None,
        help=(
            "Mở trình duyệt Chrome để đăng nhập và lưu phiên TikTok cho 1 kênh "
            "(ví dụ: --login-channel video_AI_hay)"
        ),
    )
    parser.add_argument(
        "--set-schedule",
        default=None,
        help=(
            "Cài đặt các khung giờ tự động đăng bài mỗi ngày (ví dụ: --set-schedule '08:00,12:30,20:00')"
        ),
    )
    parser.add_argument(
        "--post-now",
        default=None,
        help=(
            "Đăng ngay lập tức 1 video chờ trong ready/ của kênh chỉ định (hoặc 'all' cho cả 4 kênh) lên TikTok"
        ),
    )
    parser.add_argument(
        "--ensure-public",
        default=None,
        help=(
            "Mở trang Quản lý Bài đăng (tiktok.com/tiktokstudio/content) của kênh chỉ định (hoặc 'all') "
            "và tự động chuyển toàn bộ các video đang bị để 'Chỉ mình tôi' sang 'Mọi người' (Công khai)"
        ),
    )
    parser.add_argument(
        "--desktop-status",
        action="store_true",
        help=(
            "Kiểm tra trạng thái hoạt động của 2 bộ mã nguồn mở tích hợp: "
            "macos-desktop-control (CoreGraphics CGEvent) và LeanCTX (yvgude/lean-ctx)"
        ),
    )
    parser.add_argument(
        "--filter-24h",
        default=None,
        help=(
            "Chạy tự động 'Bộ lọc 24 giờ' (Xem hết video + Thả tim + Sao chép liên kết + "
            "Thêm vào yêu thích + Follow chủ kênh + Bình luận có ý nghĩa xác nhận đã follow) "
            "cho kênh chỉ định hoặc 'all' (tất cả 4 kênh)"
        ),
    )
    parser.add_argument(
        "--filter-count",
        type=int,
        default=10,
        help="Số lượng video mục tiêu tương tác mỗi kênh trong 1 phiên --filter-24h (mặc định: 10)",
    )
    parser.add_argument(
        "--stream-upload",
        action="store_true",
        help="Chạy riêng Dòng Hoạt Động 1: Giữ nguyên khung giờ đăng video 4 kênh (08:00, 12:30, 20:00)",
    )
    parser.add_argument(
        "--stream-filter24h",
        action="store_true",
        help="Chạy riêng Dòng Hoạt Động 2: Mở 4 kênh chạy Bộ lọc 24 giờ (7 lần/ngày × 10 video/kênh)",
    )
    parser.add_argument(
        "--platform",
        choices=["douyin", "tiktok"],
        default=None,
        help="Tùy chọn lọc nền tảng ('douyin' hoặc 'tiktok')",
    )
    parser.add_argument(
        "--genre",
        default="情感故事",
        help=(
            "Chủ đề kể chuyện cần cào khi không dùng --channel "
            "(Douyin: 情感故事, 睡前故事, 讲故事, 有声书, 文案 | TikTok: storytime, redditstories, scarystories)"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2,
        help="Số lượng video đạt chuẩn cần lấy (mặc định: 2)",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="Giọng đọc Edge-TTS: vi-VN-HoaiMyNeural (Nữ) hoặc vi-VN-NamMinhNeural (Nam)",
    )
    parser.add_argument(
        "--whisper-model",
        default=None,
        help="Kích thước model faster-whisper: base | small (mặc định đọc từ .env)",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="LLM Provider dịch thuật: auto | gemini | deepseek | openai",
    )
    parser.add_argument(
        "--duck-volume",
        type=float,
        default=None,
        help="Âm lượng âm thanh gốc khi lồng tiếng (0.0 = tắt hẳn, 0.12 = giữ nhạc nền nhẹ)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Thư mục lưu video hoàn chỉnh (mặc định: ./output)",
    )
    parser.add_argument(
        "--output-name",
        default=None,
        help="Tên file video đầu ra (chỉ dùng cho chế độ single)",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")
    setup_logging()

    try:
        # 1. Lệnh kiểm tra tồn kho & đồng bộ tự động --status / --sync
        if args.status or args.sync:
            crawler = StoryAudioCrawler(project_root=project_root)
            print("\n" + crawler.format_inventory_status_table() + "\n")
            return

        # 1b. Chế độ theo dõi ngầm tự động đồng bộ 4 kênh --watch-sync
        if args.watch_sync:
            run_watch_sync_mode(interval_seconds=5)
            return

        # 2. Lệnh đánh dấu đã đăng --mark-posted <filename_or_id>
        if args.mark_posted:
            crawler = StoryAudioCrawler(project_root=project_root)
            res = crawler.mark_video_posted(args.mark_posted)
            print("\n" + "=" * 70)
            print("ĐÃ CẬP NHẬT TRẠNG THÁI BÀI ĐĂNG (POSTED):")
            print(json.dumps(res, ensure_ascii=False, indent=2))
            print("=" * 70)
            print("\n" + crawler.format_inventory_status_table() + "\n")
            return

        # 2b. Đăng nhập và lưu phiên TikTok cho từng kênh --login-channel <channel>
        if args.login_channel:
            poster = TikTokAutoPoster(project_root=project_root)
            poster.login_channel_profile(args.login_channel)
            return

        # 2c. Cài đặt khung giờ tự động đăng bài --set-schedule "08:00,12:30,20:00"
        if args.set_schedule:
            poster = TikTokAutoPoster(project_root=project_root)
            cfg = poster.update_schedule_times(args.set_schedule)
            print("\n" + "=" * 70)
            print("ĐÃ CẬP NHẬT LỊCH TỰ ĐỘNG ĐĂNG TIKTOK 4 KÊNH (schedule_config.json):")
            print(json.dumps(cfg, ensure_ascii=False, indent=2))
            print("=" * 70 + "\n")
            return

        # 2d. Đăng thử ngay lập tức 1 video trong ready/ lên TikTok --post-now <channel|all>
        if args.post_now:
            poster = TikTokAutoPoster(project_root=project_root)
            target_ch = args.post_now.strip()
            if target_ch.lower() == "all":
                for ch_num in [p["channel_id"] for p in CHANNEL_PROFILES.values()]:
                    poster.upload_one_channel_video(ch_num)
            else:
                poster.upload_one_channel_video(target_ch)
            return

        # 2e. Kiểm tra & chuyển tất cả bài đăng đang bị "Chỉ mình tôi" sang "Mọi người" (Công khai) --ensure-public <channel|all>
        if args.ensure_public:
            poster = TikTokAutoPoster(project_root=project_root)
            res = poster.ensure_channel_posts_public(args.ensure_public.strip())
            print("\n" + "=" * 70)
            print("KẾT QUẢ CHUYỂN TRẠNG THÁI CÔNG KHAI ('MỌI NGƯỜI') TRÊN TIKTOK STUDIO:")
            print(json.dumps(res, ensure_ascii=False, indent=2))
            print("=" * 70 + "\n")
            return

        # 2f. Kiểm tra trạng thái tích hợp macos-desktop-control & LeanCTX --desktop-status
        if args.desktop_status:
            from .lean_context import LeanContextGateway
            from .macos_desktop_controller import MacOSDesktopController

            desktop = MacOSDesktopController(project_root=project_root)
            ctx_gw = LeanContextGateway(project_root=project_root)
            windows = desktop.list_windows()
            mouse_pos = desktop.get_mouse_position()
            report = {
                "macos_desktop_control": {
                    "mcp_server_js": str(desktop.mcp_server_path),
                    "coregraphics_jxa_ready": True,
                    "current_mouse_position": {"x": mouse_pos[0], "y": mouse_pos[1]},
                    "visible_windows_count": len(windows),
                    "sample_windows": windows[:5],
                },
                "lean_ctx": ctx_gw.get_engine_status(),
            }
            print("\n" + "=" * 70)
            print("TRẠNG THÁI TÍCH HỢP MACOS-DESKTOP-CONTROL & LEAN-CTX:")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print("=" * 70 + "\n")
            return

        # 2g. Chạy tương tác chéo "Bộ lọc 24 giờ" --filter-24h <channel|all>
        if args.filter_24h:
            poster = TikTokAutoPoster(project_root=project_root)
            res = poster.run_24h_filter_for_channel(
                channel_arg=args.filter_24h.strip(),
                video_count=max(1, args.filter_count),
            )
            print("\n" + "=" * 70)
            print("KẾT QUẢ TƯƠNG TÁC CHÉO 'BỘ LỌC 24 GIỜ' (6 BƯỚC CHUẨN):")
            print(json.dumps(res, ensure_ascii=False, indent=2))
            print("=" * 70 + "\n")
            return

        # 2h. Chạy riêng Dòng Hoạt Động 1 (--stream-upload) hoặc Dòng Hoạt Động 2 (--stream-filter24h)
        if args.stream_upload and not args.stream_filter24h:
            logger.info(
                "=== KHỞI ĐỘNG DÒNG HOẠT ĐỘNG 1 (--stream-upload): "
                "LẤP ĐẦY KHO READY/ & GIỮ NGUYÊN KHUNG GIỜ ĐĂNG VIDEO CHO 4 KÊNH ==="
            )
            run_continuous_mode(
                max_ready=args.max_ready,
                sleep_between=args.sleep_between,
                max_cycles=args.max_cycles,
                platform=args.platform,
                output_dir=Path(args.output_dir) if args.output_dir else None,
                whisper_model=args.whisper_model,
                voice=args.voice,
                llm_provider=args.provider,
                original_volume=args.duck_volume,
                enable_stream2_filter24h=False,
            )
            return

        if args.stream_filter24h:
            poster = TikTokAutoPoster(project_root=project_root)
            snap = _get_codebase_mtime_snapshot(project_root)
            logger.info(
                "=== KHỞI ĐỘNG DÒNG HOẠT ĐỘNG 2 (--stream-filter24h): "
                "MỞ 4 KÊNH CHẠY BỘ LỌC 24 GIỜ (7 LẦN/NGÀY × 10 VIDEO/KÊNH) ==="
            )
            f24_tick = 0
            while True:
                pulled = _auto_git_pull_if_available(project_root)
                if pulled or _get_codebase_mtime_snapshot(project_root) != snap:
                    try:
                        import importlib
                        from . import growth_24h_filter as _g24_mod
                        from . import auto_poster as _ap_mod

                        importlib.reload(_g24_mod)
                        importlib.reload(_ap_mod)
                        poster = _ap_mod.TikTokAutoPoster(project_root=project_root)
                        snap = _get_codebase_mtime_snapshot(project_root)
                    except Exception:
                        pass
                poster.check_and_run_warmup_browse()
                poster.check_and_run_stream2_filter24h_schedule()
                f24_tick += 1
                if f24_tick % 10 == 0:
                    logger.info(
                        "[Dòng 2 - Bộ lọc 24h] Đang hoạt động ổn định — canh khung giờ chạy tiếp theo (7 lần/ngày)..."
                    )
                time.sleep(30)
            return

        # 3. Chế độ Cào và xử lý liên tục (Daemon / Continuous Worker Mode) --continuous
        if args.continuous:
            run_continuous_mode(
                max_ready=args.max_ready,
                sleep_between=args.sleep_between,
                max_cycles=args.max_cycles,
                platform=args.platform,
                output_dir=Path(args.output_dir) if args.output_dir else None,
                whisper_model=args.whisper_model,
                voice=args.voice,
                llm_provider=args.provider,
                original_volume=args.duck_volume,
            )
            return

        # 4. Nếu truyền --channel mà không truyền --url -> tự động kích hoạt chế độ cào theo kênh
        if args.channel and not args.url:
            ch_arg = args.channel.strip().lower()
            if ch_arg == "all":
                target_channels = [p["channel_id"] for p in CHANNEL_PROFILES.values()]
            elif "," in ch_arg:
                target_channels = [c.strip() for c in ch_arg.split(",") if c.strip()]
            else:
                target_channels = [args.channel.strip()]

            all_results: List[Dict[str, Any]] = []
            for ch in target_channels:
                ch_results = run_crawl_story_mode(
                    platform=args.platform,
                    genre=args.genre,
                    limit=args.limit,
                    channel=ch,
                    output_dir=Path(args.output_dir) if args.output_dir else None,
                    whisper_model=args.whisper_model,
                    voice=args.voice,
                    llm_provider=args.provider,
                    original_volume=args.duck_volume,
                )
                all_results.extend(ch_results)
            print("\n" + "=" * 70)
            print(f"KẾT QUẢ CÀO KÊNH (CHANNEL {args.channel}):")
            print(json.dumps(all_results, ensure_ascii=False, indent=2))
            print("=" * 70)
        elif args.mode == "crawl-story" and not args.url:
            results = run_crawl_story_mode(
                platform=args.platform or "douyin",
                genre=args.genre,
                limit=args.limit,
                channel=args.channel,
                output_dir=Path(args.output_dir) if args.output_dir else None,
                whisper_model=args.whisper_model,
                voice=args.voice,
                llm_provider=args.provider,
                original_volume=args.duck_volume,
            )
            print("\n" + "=" * 70)
            print(f"KẾT QUẢ CÀO KỂ CHUYỆN ({args.genre}):")
            print(json.dumps(results, ensure_ascii=False, indent=2))
            print("=" * 70)
        else:
            if not args.url:
                parser.error(
                    "Vui lòng cung cấp `--continuous`, `--status`, `--mark-posted <ID/file>`, `--channel <1|2|3|4>`, hoặc `--url <URL>`."
                )
            summary = run_pipeline(
                url=args.url,
                output_dir=Path(args.output_dir) if args.output_dir else None,
                whisper_model=args.whisper_model,
                voice=args.voice,
                llm_provider=args.provider,
                original_volume=args.duck_volume,
                output_name=args.output_name,
                genre=args.genre,
                channel=args.channel,
                limit=args.limit,
            )
            print("\n" + "=" * 70)
            print("KẾT QUẢ PIPELINE:")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print("=" * 70)
    except Exception as exc:
        logger.error(f"Pipeline thất bại: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
