import csv
import hashlib
import json
import os
import platform
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .crawler import CHANNEL_PROFILES, StoryAudioCrawler, resolve_channel_profile
from .utils import logger


class PosterMediaPrepMixin:
    """Chuẩn bị video ready/, cắt tập, chạy FFmpeg 6 lớp & lọc Quy tắc cộng đồng trước khi đăng."""

    @staticmethod
    def _quick_video_fingerprint(file_path: Path) -> str:
        """Tính vân tay nhị phân nhanh (size + 64KB đầu/cuối) để phát hiện 2 file MP4 giống hệt nội dung dù khác tên."""
        import hashlib

        try:
            st = file_path.stat()
            size = st.st_size
            h = hashlib.sha256()
            h.update(str(size).encode("utf-8"))
            with open(file_path, "rb") as f:
                h.update(f.read(65536))
                if size > 131072:
                    f.seek(max(0, size - 65536))
                    h.update(f.read(65536))
            return f"{size}:{h.hexdigest()[:24]}"
        except Exception:
            return ""


    @staticmethod
    def _audio_story_fingerprint(file_path: Path) -> Tuple[float, str]:
        """
        Trích xuất thời lượng chính xác (giây) và vân tay năng lượng âm thanh (Audio Envelope Hash)
        ở đoạn giữa video (t=25s..55s) để phát hiện 2 video khác hình nền ASMR nhưng dùng chung 1 file ghi âm truyện!
        """
        import array
        import subprocess
        from .utils import get_media_duration

        dur = float(get_media_duration(file_path) or 0.0)
        try:
            start_sec = 25 if dur >= 60.0 else max(2, int(dur * 0.2))
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    str(start_sec),
                    "-t",
                    "25",
                    "-i",
                    str(file_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "1000",
                    "-f",
                    "s16le",
                    "-",
                ],
                capture_output=True,
                timeout=8,
                check=False,
            )
            raw = proc.stdout
            if not raw or len(raw) < 2000:
                return dur, ""
            samples = array.array("h", raw[: (len(raw) // 2) * 2])
            bucket_size = 500  # 0.5s mỗi bucket ở 1000Hz
            energies: List[int] = []
            for i in range(0, len(samples) - bucket_size, bucket_size):
                chunk = samples[i : i + bucket_size]
                avg_abs = sum(abs(x) for x in chunk) // bucket_size
                energies.append(avg_abs)
            if len(energies) < 8:
                return dur, ""
            mean_e = max(1, sum(energies) // len(energies))
            # Chuẩn hóa bit theo ngưỡng năng lượng trung bình (bất biến với thay đổi âm lượng hoặc pitch nhẹ)
            bits = "".join("1" if e >= mean_e else "0" for e in energies[:40])
            return dur, bits
        except Exception:
            return dur, ""


    def _record_uploaded_video_in_registry(
        self, ch_folder: str, video_path: Path, caption: str = ""
    ) -> None:
        """Ghi nhận ngay lập tức file/story_id đã bấm Đăng vào `uploaded_videos_registry.json` để chống đăng trùng tuyệt đối."""
        reg_path = self.project_root / "uploaded_videos_registry.json"
        data: Dict[str, Any] = {
            "posted_story_ids": [],
            "posted_filenames": [],
            "posted_fingerprints": [],
            "history": [],
        }
        if reg_path.exists():
            try:
                loaded = json.loads(reg_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data.update(loaded)
            except Exception:
                pass

        m_id = re.search(
            r"(?:tiktok|douyin|youtube_shorts|facebook_reels|instagram_reels|bilibili|xiaohongshu)_story_([A-Za-z0-9_-]+)",
            video_path.stem,
            re.IGNORECASE,
        )
        vid_id = re.split(r"_(?:bongcuc|gautrucden)_", m_id.group(1))[0] if m_id else ""
        fp = self._quick_video_fingerprint(video_path) if video_path.exists() else ""

        sids = set(str(x) for x in (data.get("posted_story_ids") or []) if x)
        fns = set(str(x) for x in (data.get("posted_filenames") or []) if x)
        fps = set(str(x) for x in (data.get("posted_fingerprints") or []) if x)

        if vid_id:
            sids.add(vid_id)
            try:
                hist_p = self.project_root / "story_audio_history.json"
                if hist_p.exists():
                    h_d = json.loads(hist_p.read_text(encoding="utf-8"))
                    if isinstance(h_d, dict):
                        v_list = list(h_d.get("video_ids") or [])
                        if vid_id not in v_list:
                            v_list.append(vid_id)
                            h_d["video_ids"] = v_list
                            hist_p.write_text(json.dumps(h_d, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        fns.add(video_path.name)
        if fp:
            fps.add(fp)

        data["posted_story_ids"] = sorted(sids)
        data["posted_filenames"] = sorted(fns)
        data["posted_fingerprints"] = sorted(fps)
        hist = list(data.get("history") or [])
        hist.insert(
            0,
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "channel": ch_folder,
                "filename": video_path.name,
                "story_id": vid_id,
                "fingerprint": fp,
                "caption": caption[:160],
            },
        )
        data["history"] = hist[:200]
        try:
            reg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        if ch_folder == "audio_dem_khuyen" and vid_id and "bongcuc_v6_s" in video_path.name.lower():
            try:
                inv_path = self.project_root / "data" / "audio_dem_khuyen_live_posts.json"
                inv_path.parent.mkdir(parents=True, exist_ok=True)
                inv_data: Dict[str, Any] = {}
                if inv_path.exists():
                    inv_data = json.loads(inv_path.read_text(encoding="utf-8"))
                rep_sids = set(str(x) for x in (inv_data.get("reposted_v6_story_ids") or []) if x)
                rep_sids.add(vid_id)
                inv_data["reposted_v6_story_ids"] = sorted(rep_sids)
                inv_path.write_text(json.dumps(inv_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass


    def _get_next_ready_video_for_channel(
        self, ch_folder: str
    ) -> Optional[Tuple[Path, str, str]]:
        """
        Lấy video chờ đăng tiếp theo trong `output/<ch_folder>/ready/` cùng `caption` (kịch bản ngắn tiếng Việt)
        và `hashtags` (Hot Search) từ `output/danh_sach_video.csv`.
        Tự động kiểm tra chống đăng trùng 3 lớp (story_id + filename + binary fingerprint) so với `posted/`,
        CSV, `uploaded_videos_registry.json` và lịch sử Telemetry trước khi chọn file.
        """
        try:
            self.crawler.auto_sync_posted_videos()
        except Exception:
            pass

        ready_dir = self.project_root / "output" / ch_folder / "ready"
        posted_dir = self.project_root / "output" / ch_folder / "posted"
        ready_dir.mkdir(parents=True, exist_ok=True)
        posted_dir.mkdir(parents=True, exist_ok=True)

        posted_ids, posted_filenames = self.crawler._collect_all_known_posted_story_ids_and_files()
        lost_repost_sids: Set[str] = set()
        if ch_folder == "audio_dem_khuyen":
            try:
                from .bongcuc_splitter import BongCucYouTubeSplitter

                lost_repost_sids = BongCucYouTubeSplitter.get_lost_story_ids_needing_repost(
                    self.project_root
                )
                posted_ids -= lost_repost_sids
            except Exception:
                pass

        posted_fingerprints: set[str] = set()
        posted_audio_bits: List[str] = []

        for p_mp4 in posted_dir.glob("*.mp4"):
            if p_mp4.is_file():
                if ch_folder == "audio_dem_khuyen" and any(
                    lsid in p_mp4.name for lsid in lost_repost_sids
                ) and "bongcuc_v6_s" not in p_mp4.name.lower():
                    continue
                fp = self._quick_video_fingerprint(p_mp4)
                if fp:
                    posted_fingerprints.add(fp)
                _, p_bits = self._audio_story_fingerprint(p_mp4)
                if p_bits:
                    posted_audio_bits.append(p_bits)

        reg_path = self.project_root / "uploaded_videos_registry.json"
        if reg_path.exists():
            try:
                reg_data = json.loads(reg_path.read_text(encoding="utf-8"))
                for fp in reg_data.get("posted_fingerprints") or []:
                    if fp:
                        posted_fingerprints.add(str(fp))
            except Exception:
                pass

        from .community_guidelines_guard import TikTokCommunityGuidelinesGuard

        target_video: Optional[Path] = None
        valid_candidates: List[Path] = []
        for cand in sorted(f for f in ready_dir.glob("*.mp4") if f.is_file()):
            if TikTokCommunityGuidelinesGuard.is_video_or_story_blocked(cand.name, ch_folder):
                cand.unlink(missing_ok=True)
                logger.info(
                    f"[{ch_folder}] [Community Guidelines Guard] Đã xóa video thuộc danh sách khóa an toàn ({cand.name}) khỏi ready/."
                )
                continue
            # Kênh audio_dem_khuyen chỉ đăng chuẩn motip Truyện Audio TikTok Việt Nam (nền ASMR/chữa lành + giọng kể chuyện), loại bỏ video Douyin lệch motip
            if ch_folder == "audio_dem_khuyen" and "douyin_story_" in cand.name.lower():
                cand.unlink(missing_ok=True)
                logger.info(
                    f"[{ch_folder}] [Motif Guard] Đã xóa video Douyin lệch motip ({cand.name}) khỏi ready/ để chỉ giữ chuẩn Truyện Audio TikTok Việt Nam."
                )
                continue

            m_cand = re.search(
                r"(?:tiktok|douyin|youtube_shorts|facebook_reels|instagram_reels|bilibili|xiaohongshu)_story_([A-Za-z0-9_-]+)",
                cand.stem,
                re.IGNORECASE,
            )
            cand_id = re.split(r"_(?:bongcuc|gautrucden)_", m_cand.group(1))[0] if m_cand else ""
            cand_fp = self._quick_video_fingerprint(cand)
            cand_dur, cand_bits = self._audio_story_fingerprint(cand)

            is_dup_audio = False
            if (
                cand_bits
                and posted_audio_bits
                and not (
                    ch_folder == "audio_dem_khuyen"
                    and cand_id in lost_repost_sids
                    and "bongcuc_v6_s" in cand.name.lower()
                )
            ):
                for p_b in posted_audio_bits:
                    if len(cand_bits) == len(p_b):
                        same_cnt = sum(1 for a, b in zip(cand_bits, p_b) if a == b)
                        if same_cnt / len(cand_bits) >= 0.88:
                            is_dup_audio = True
                            break

            if (
                cand.name in posted_filenames
                or (cand_id and cand_id in posted_ids)
                or (cand_fp and cand_fp in posted_fingerprints)
                or is_dup_audio
            ):
                dest_dup = posted_dir / cand.name
                if is_dup_audio:
                    cand.unlink(missing_ok=True)
                    logger.info(
                        f"[{ch_folder}] [Anti-Duplicate Audio Guard] Đã xóa video trùng nội dung âm thanh {cand.name} (thời lượng={cand_dur:.1f}s) khỏi ready/."
                    )
                    continue
                has_same_sid = bool(cand_id and any(cand_id in p.name for p in posted_dir.glob("*.mp4")))
                if has_same_sid and not dest_dup.exists():
                    cand.unlink(missing_ok=True)
                    logger.info(
                        f"[{ch_folder}] [Anti-Duplicate Guard] Đã xóa bản trùng {cand.name} (story_id={cand_id}) khỏi ready/."
                    )
                else:
                    if dest_dup.exists():
                        dest_dup.unlink()
                    shutil.move(str(cand), str(dest_dup))
                    logger.info(
                        f"[{ch_folder}] [Anti-Duplicate Guard] Bỏ qua {cand.name} (story_id={cand_id}) vì ĐÃ ĐĂNG TRƯỚC ĐÓ -> chuyển sang posted/."
                    )
                continue

            valid_candidates.append(cand)
            if ch_folder not in ("audio_dem_khuyen", "video_AI_hay"):
                break

        if valid_candidates or ch_folder in ("audio_dem_khuyen", "video_AI_hay"):
            if ch_folder == "audio_dem_khuyen":
                try:
                    from .bongcuc_splitter import (
                        BongCucYouTubeSplitter,
                        get_target_bg_mode_for_hour,
                        make_bongcuc_story_id,
                        parse_bongcuc_bg_mode,
                        parse_bongcuc_ep_and_part,
                    )

                    # Thu thập chuỗi các bài vừa đăng gần nhất trên kênh audio_dem_khuyen
                    # để kiểm soát chặt chẽ tỷ lệ [1 Video Đã Mất Cần Đăng Lại : 1 Phần Tiếp Theo]
                    # và khóa Cooldown 4 bài liên tiếp không lặp lại cùng 1 tập!
                    recent_ep_parts: List[Tuple[int, int]] = []

                    def _append_recent_ep(ep_p_val: Optional[Tuple[int, int]]) -> None:
                        if ep_p_val and (not recent_ep_parts or recent_ep_parts[-1] != ep_p_val):
                            recent_ep_parts.append(ep_p_val)

                    if reg_path.exists():
                        try:
                            reg_d = json.loads(reg_path.read_text(encoding="utf-8"))
                            for h_item in reg_d.get("history") or []:
                                if isinstance(h_item, dict) and h_item.get("channel") == "audio_dem_khuyen":
                                    _append_recent_ep(
                                        parse_bongcuc_ep_and_part(
                                            f"{h_item.get('filename', '')} {h_item.get('story_id', '')} {h_item.get('caption', '')}"
                                        )
                                    )
                                    if len(recent_ep_parts) >= 10:
                                        break
                        except Exception:
                            pass

                    rep_p = self.project_root / "remote_telemetry" / "intel_status_report.json"
                    if rep_p.exists() and len(recent_ep_parts) < 10:
                        try:
                            rep_d = json.loads(rep_p.read_text(encoding="utf-8"))
                            for ev in rep_d.get("recent_events") or []:
                                if (
                                    isinstance(ev, dict)
                                    and ev.get("event_type") == "upload"
                                    and ev.get("channel") == "audio_dem_khuyen"
                                ):
                                    det = ev.get("details") or {}
                                    _append_recent_ep(
                                        parse_bongcuc_ep_and_part(
                                            f"{det.get('video_file', '')} {det.get('caption', '')}"
                                        )
                                    )
                                    if len(recent_ep_parts) >= 10:
                                        break
                        except Exception:
                            pass

                    if len(recent_ep_parts) < 10:
                        posted_mp4s = sorted(
                            (p for p in posted_dir.glob("*.mp4") if p.is_file()),
                            key=lambda f: f.stat().st_mtime,
                            reverse=True,
                        )
                        for p_f in posted_mp4s:
                            _append_recent_ep(parse_bongcuc_ep_and_part(p_f.name))
                            if len(recent_ep_parts) >= 10:
                                break

                    last_ep_num: Optional[int] = recent_ep_parts[0][0] if recent_ep_parts else None
                    last_part_num: Optional[int] = recent_ep_parts[0][1] if recent_ep_parts else None
                    recent_cooldown_eps: Set[int] = {ep for ep, _ in recent_ep_parts[:4]}

                    # Đan xen TỪ TỪ khôi phục các video đã mất (1 Video Đã Mất : 1 Phần Tiếp Theo):
                    # Nếu bài đăng gần nhất KHÔNG phải là Phần 1, cho phép lượt này đăng lại 1 video đã mất (kể cả Phần 1 đã mất)
                    want_part1 = len(recent_ep_parts) >= 6 and all(
                        int(pt) >= 2 for _, pt in recent_ep_parts[:6]
                    )
                    allow_lost_recovery_turn = bool(lost_repost_sids) and (
                        last_part_num is None or last_part_num >= 2
                    )
                    target_bg_mode = get_target_bg_mode_for_hour()

                    splitter = BongCucYouTubeSplitter(project_root=self.project_root)
                    cat_views = {
                        int(e.get("ep_num") or 0): int(e.get("view_count") or 0)
                        for e in splitter.get_full_catalog()
                    }
                    # ƯU TIÊN SỐ 1 THEO YÊU CẦU: Ca đăng khuya đêm nay đăng "BC Số 119 - Phần 3" (story_id = 990011903) với nền Mukbang mới
                    pinned_night_sid = "990011903"
                    if target_bg_mode == "night_mukbang" and pinned_night_sid not in posted_ids:
                        ep119_list = [
                            e for e in splitter.get_full_catalog() if int(e.get("ep_num") or 0) == 119
                        ]
                        if ep119_list:
                            p3_list = [
                                p
                                for p in splitter.compute_episode_parts_plan(ep119_list[0])
                                if int(p.get("part_num") or 0) == 3
                            ]
                            if p3_list:
                                chosen_p3 = dict(p3_list[0])
                                chosen_p3["bg_mode"] = "night_mukbang"
                                existing_p3 = [
                                    c_f
                                    for c_f in valid_candidates
                                    if "990011903" in c_f.name
                                    and "bongcuc_v6_s" in c_f.name.lower()
                                    and "_bg_mukbang" in c_f.name.lower()
                                ]
                                if existing_p3:
                                    target_video = existing_p3[0]
                                else:
                                    logger.info(
                                        f"[{ch_folder}] [Pinned Night Slot 23:00] Đang xuất ngay BC Số 119 Phần 3/{chosen_p3['total_parts']} với nền Mukbang Hàn Quốc 1080p mới..."
                                    )
                                    self._refresh_browser_process_lock()
                                    rendered_p3 = splitter.render_single_part(
                                        part_spec=chosen_p3,
                                        target_ready_dir=ready_dir,
                                        bg_mode="night_mukbang",
                                    )
                                    self._refresh_browser_process_lock()
                                    try:
                                        from .crawler import StoryAudioCrawler

                                        cr_sync = StoryAudioCrawler(project_root=self.project_root)
                                        cr_sync.clear_macl_xattr(Path(rendered_p3["final_video"]))
                                        cr_sync.save_to_history(
                                            video_id=rendered_p3["video_id"],
                                            sound_id=rendered_p3["sound_id"],
                                            platform="tiktok",
                                            genre="channel_3_novel_drama",
                                            source_url=rendered_p3["source_url"],
                                            output_filename=rendered_p3["filename"],
                                            channel="audio_dem_khuyen",
                                        )
                                        cr_sync.append_to_master_csv(
                                            filename=rendered_p3["filename"],
                                            source_url=rendered_p3["source_url"],
                                            caption=rendered_p3["caption"],
                                            hashtags=rendered_p3["hashtags"],
                                            status="READY",
                                            channel="audio_dem_khuyen",
                                            posted_at="",
                                        )
                                    except Exception:
                                        pass
                                    target_video = Path(rendered_p3["final_video"])

                    high_view_channel_prio: Dict[int, int] = {
                        21: 1000,
                        120: 950,
                        119: 900,
                        70: 850,
                        39: 800,
                        60: 750,
                        35: 700,
                        110: 650,
                        141: 600,
                    }

                    queue = splitter.build_interleaved_queue(posted_ids)
                    cont_queue_specs = [
                        p
                        for p in queue
                        if int(p["part_num"]) >= 2 and int(p["ep_num"]) not in recent_cooldown_eps
                    ]
                    min_pending_part_num = min(
                        (int(p["part_num"]) for p in cont_queue_specs), default=2
                    )
                    if min_pending_part_num < 8:
                        recent_cooldown_eps.add(10)

                    # KHÓA ĐỘC QUYỀN 100% CẤU TRÚC MỚI v6 (bongcuc_v6_s: có giữ "BC SỐ" trong khung kể chuyện) & DỌN SLOT CŨ:
                    lowest_part_by_ep: Dict[int, Tuple[int, Path, Optional[str]]] = {}
                    for c_file in valid_candidates:
                        ep_p = parse_bongcuc_ep_and_part(c_file.name)
                        if ep_p and "bongcuc_v6_s" in c_file.name.lower():
                            ep_no, pt_no = ep_p
                            sid_check = make_bongcuc_story_id(ep_no, pt_no)
                            if sid_check not in lost_repost_sids and sid_check != pinned_night_sid and (
                                (ep_no == 10 and min_pending_part_num < 8)
                                or (pt_no >= 2 and pt_no > min_pending_part_num + 1)
                            ):
                                c_file.unlink(missing_ok=True)
                                continue
                            bg_m = parse_bongcuc_bg_mode(c_file.name)
                            if (
                                ep_no not in lowest_part_by_ep
                                or pt_no < lowest_part_by_ep[ep_no][0]
                                or (
                                    pt_no == lowest_part_by_ep[ep_no][0]
                                    and bg_m == target_bg_mode
                                )
                            ):
                                lowest_part_by_ep[ep_no] = (pt_no, c_file, bg_m)
                        else:
                            c_file.unlink(missing_ok=True)

                    # Xếp hạng ứng viên trong ready/:
                    exact_bg_and_part_cands: List[Tuple[Tuple[int, int, int, int], Path]] = []
                    exact_bg_only_cands: List[Tuple[Tuple[int, int, int, int], Path]] = []
                    for ep_no, (pt_no, c_file, bg_m) in lowest_part_by_ep.items():
                        if ep_no in recent_cooldown_eps:
                            continue
                        sid_cand = make_bongcuc_story_id(ep_no, pt_no)
                        is_lost_rec = sid_cand in lost_repost_sids
                        is_p1 = pt_no == 1
                        v_cnt = cat_views.get(ep_no, 0)
                        prio_w = high_view_channel_prio.get(ep_no, 0)
                        lost_rank = 0 if (is_lost_rec and allow_lost_recovery_turn) else 1
                        sort_key = (lost_rank, pt_no, -prio_w, -v_cnt)
                        if bg_m == target_bg_mode and (
                            (is_lost_rec and allow_lost_recovery_turn)
                            or (is_p1 == want_part1 and (want_part1 or pt_no <= min_pending_part_num))
                        ):
                            exact_bg_and_part_cands.append((sort_key, c_file))
                        elif bg_m == target_bg_mode and (
                            is_lost_rec or (not is_p1 and pt_no <= min_pending_part_num)
                        ):
                            exact_bg_only_cands.append((sort_key, c_file))

                    exact_bg_and_part_cands.sort(key=lambda x: x[0])
                    exact_bg_only_cands.sort(key=lambda x: x[0])

                    if target_video and target_video.exists():
                        pass
                    elif exact_bg_and_part_cands:
                        target_video = exact_bg_and_part_cands[0][1]
                    else:
                        # Tự động cắt ngay 1 video đúng chuẩn v6 (khôi phục từ từ video đã mất + đồng bộ cấp độ Phần)
                        desired_specs = [
                            p
                            for p in queue
                            if int(p["ep_num"]) not in recent_cooldown_eps
                            and (
                                (bool(p.get("is_lost_recovery")) and allow_lost_recovery_turn)
                                or ((int(p["part_num"]) == 1) == want_part1)
                            )
                        ]
                        if not desired_specs:
                            desired_specs = [
                                p
                                for p in queue
                                if int(p["ep_num"]) not in recent_cooldown_eps
                            ]
                        if desired_specs:
                            chosen_spec = dict(desired_specs[0])
                            chosen_spec["bg_mode"] = target_bg_mode
                            logger.info(
                                f"[{ch_folder}] [On-Demand Render v6] Đang xuất ngay BC Số {chosen_spec['ep_num']} "
                                f"Phần {chosen_spec['part_num']}/{chosen_spec['total_parts']} "
                                f"(lost_recovery={bool(chosen_spec.get('is_lost_recovery'))}, views={chosen_spec.get('view_count', 0):,}, background={target_bg_mode})..."
                            )
                            self._refresh_browser_process_lock()
                            rendered = splitter.render_single_part(
                                part_spec=chosen_spec,
                                target_ready_dir=ready_dir,
                                bg_mode=target_bg_mode,
                            )
                            self._refresh_browser_process_lock()
                            try:
                                from .crawler import StoryAudioCrawler

                                cr_sync = StoryAudioCrawler(project_root=self.project_root)
                                cr_sync.clear_macl_xattr(Path(rendered["final_video"]))
                                cr_sync.save_to_history(
                                    video_id=rendered["video_id"],
                                    sound_id=rendered["sound_id"],
                                    platform="tiktok",
                                    genre="channel_3_novel_drama",
                                    source_url=rendered["source_url"],
                                    output_filename=rendered["filename"],
                                    channel="audio_dem_khuyen",
                                )
                                cr_sync.append_to_master_csv(
                                    filename=rendered["filename"],
                                    source_url=rendered["source_url"],
                                    caption=rendered["caption"],
                                    hashtags=rendered["hashtags"],
                                    status="READY",
                                    channel="audio_dem_khuyen",
                                    posted_at="",
                                )
                            except Exception:
                                pass
                            target_video = Path(rendered["final_video"])
                        elif exact_bg_only_cands:
                            target_video = exact_bg_only_cands[0][1]
                        else:
                            target_video = None

                    if target_video and target_video.exists():
                        sel_ep = parse_bongcuc_ep_and_part(target_video.name) or (0, 0)
                        logger.info(
                            f"[{ch_folder}] [Gradual Lost-Video Recovery + Level-Synchronized v6 Guard] "
                            f"Chọn đăng: BC Số {sel_ep[0]} Phần {sel_ep[1]} "
                            f"(views={cat_views.get(sel_ep[0], 0):,}, min_pending_part={min_pending_part_num}, bg={target_bg_mode} | bài trước: Số {last_ep_num} Phần {last_part_num})"
                        )
                except Exception as int_exc:
                    logger.warning(f"[{ch_folder}] Lỗi Interleaved Guard: {int_exc}")
                    target_video = None
            elif ch_folder == "video_AI_hay":
                try:
                    from .gausohai_splitter import (
                        GauTrucDenYouTubeSplitter,
                        make_gausohai_story_id,
                        parse_gausohai_ep_and_part,
                    )

                    # Thu thập chuỗi các bài vừa đăng gần nhất trên kênh video_AI_hay
                    # để khóa Cooldown 3 bài liên tiếp không lặp lại cùng 1 tập (chặn đứng so le 1:1 A-B-A-B)
                    # và duy trì sóng 4 Phần Tiếp Theo : 1 Tập Mới Phần 1
                    recent_gsh_parts: List[Tuple[int, int]] = []

                    def _append_recent_gsh(ep_p_val: Optional[Tuple[int, int]]) -> None:
                        if ep_p_val and (not recent_gsh_parts or recent_gsh_parts[-1] != ep_p_val):
                            recent_gsh_parts.append(ep_p_val)

                    if reg_path.exists():
                        try:
                            reg_d = json.loads(reg_path.read_text(encoding="utf-8"))
                            for h_item in reg_d.get("history") or []:
                                if isinstance(h_item, dict) and h_item.get("channel") == "video_AI_hay":
                                    _append_recent_gsh(
                                        parse_gausohai_ep_and_part(
                                            f"{h_item.get('filename', '')} {h_item.get('story_id', '')} {h_item.get('caption', '')}"
                                        )
                                    )
                                    if len(recent_gsh_parts) >= 10:
                                        break
                        except Exception:
                            pass

                    rep_p = self.project_root / "remote_telemetry" / "intel_status_report.json"
                    if rep_p.exists() and len(recent_gsh_parts) < 10:
                        try:
                            rep_d = json.loads(rep_p.read_text(encoding="utf-8"))
                            for ev in rep_d.get("recent_events") or []:
                                if (
                                    isinstance(ev, dict)
                                    and ev.get("event_type") == "upload"
                                    and ev.get("channel") == "video_AI_hay"
                                ):
                                    det = ev.get("details") or {}
                                    _append_recent_gsh(
                                        parse_gausohai_ep_and_part(
                                            f"{det.get('video_file', '')} {det.get('caption', '')}"
                                        )
                                    )
                                    if len(recent_gsh_parts) >= 10:
                                        break
                        except Exception:
                            pass

                    if len(recent_gsh_parts) < 10:
                        posted_mp4s = sorted(
                            (p for p in posted_dir.glob("*.mp4") if p.is_file()),
                            key=lambda f: f.stat().st_mtime,
                            reverse=True,
                        )
                        for p_f in posted_mp4s:
                            _append_recent_gsh(parse_gausohai_ep_and_part(p_f.name))
                            if len(recent_gsh_parts) >= 10:
                                break

                    last_ep_num = recent_gsh_parts[0][0] if recent_gsh_parts else None
                    last_part_num = recent_gsh_parts[0][1] if recent_gsh_parts else None
                    # Cooldown 3 bài gần nhất để KHÔNG BAO GIỜ bị so le 1:1 (A -> B -> A -> B)
                    recent_cooldown_eps: Set[int] = {ep for ep, _ in recent_gsh_parts[:3]}

                    opened_gsh_eps = {
                        int(sid[4:7])
                        for sid in posted_ids
                        if len(sid) == 9 and sid.startswith("8800") and sid.isdigit()
                    }
                    # Mở sóng 4 tập đầu tiên, sau đó cứ 4 phần tiếp theo mới mở 1 Tập Mới Phần 1 (tỷ lệ 4:1, KHÔNG so le 1:1)
                    want_part1 = len(opened_gsh_eps) < 4 or (
                        len(recent_gsh_parts) >= 4
                        and all(int(pt) >= 2 for _, pt in recent_gsh_parts[:4])
                    )

                    gsh_splitter = GauTrucDenYouTubeSplitter(project_root=self.project_root)
                    cat_views = {
                        int(e.get("ep_num") or 0): int(e.get("view_count") or 0)
                        for e in gsh_splitter.get_full_catalog()
                    }
                    queue = gsh_splitter.build_interleaved_queue(posted_ids)
                    cont_queue_specs = [
                        p
                        for p in queue
                        if int(p["part_num"]) >= 2 and int(p["ep_num"]) not in recent_cooldown_eps
                    ]
                    min_pending_part_num = min(
                        (int(p["part_num"]) for p in cont_queue_specs), default=2
                    )

                    # Chỉ giữ chuẩn gautrucden_v1_s trong ready/ và chọn phần nhỏ nhất chưa đăng của mỗi tập
                    lowest_part_by_ep_gsh: Dict[int, Tuple[int, Path]] = {}
                    for c_file in valid_candidates:
                        ep_p = parse_gausohai_ep_and_part(c_file.name)
                        if ep_p and "gautrucden_v1_s" in c_file.name.lower():
                            ep_no, pt_no = ep_p
                            if (
                                ep_no not in lowest_part_by_ep_gsh
                                or pt_no < lowest_part_by_ep_gsh[ep_no][0]
                            ):
                                lowest_part_by_ep_gsh[ep_no] = (pt_no, c_file)
                        else:
                            c_file.unlink(missing_ok=True)

                    exact_wave_cands: List[Tuple[Tuple[int, int], Path]] = []
                    fallback_wave_cands: List[Tuple[Tuple[int, int], Path]] = []
                    for ep_no, (pt_no, c_file) in lowest_part_by_ep_gsh.items():
                        if ep_no in recent_cooldown_eps:
                            continue
                        is_p1 = pt_no == 1
                        v_cnt = cat_views.get(ep_no, 0)
                        sort_key = (pt_no, -v_cnt)
                        if is_p1 == want_part1 and (want_part1 or pt_no <= min_pending_part_num + 1):
                            exact_wave_cands.append((sort_key, c_file))
                        else:
                            fallback_wave_cands.append((sort_key, c_file))

                    exact_wave_cands.sort(key=lambda x: x[0])
                    fallback_wave_cands.sort(key=lambda x: x[0])

                    if exact_wave_cands:
                        target_video = exact_wave_cands[0][1]
                    elif fallback_wave_cands:
                        target_video = fallback_wave_cands[0][1]
                    else:
                        desired_specs = [
                            p
                            for p in queue
                            if int(p["ep_num"]) not in recent_cooldown_eps
                            and ((int(p["part_num"]) == 1) == want_part1)
                        ]
                        if not desired_specs:
                            desired_specs = [
                                p
                                for p in queue
                                if int(p["ep_num"]) not in recent_cooldown_eps
                            ]
                        if desired_specs:
                            chosen_spec = dict(desired_specs[0])
                            logger.info(
                                f"[{ch_folder}] [On-Demand Render Gấu Trúc Đen] Đang xuất ngay Tập {chosen_spec['ep_num']} "
                                f"Phần {chosen_spec['part_num']}/{chosen_spec['total_parts']} ({chosen_spec.get('title')})..."
                            )
                            self._refresh_browser_process_lock()
                            rendered = gsh_splitter.render_single_part(
                                part_spec=chosen_spec,
                                target_ready_dir=ready_dir,
                            )
                            self._refresh_browser_process_lock()
                            try:
                                from .crawler import StoryAudioCrawler

                                cr_sync = StoryAudioCrawler(project_root=self.project_root)
                                cr_sync.clear_macl_xattr(Path(rendered["final_video"]))
                                cr_sync.save_to_history(
                                    video_id=rendered["video_id"],
                                    sound_id=rendered["sound_id"],
                                    platform="tiktok",
                                    genre="channel_1_ai_trending",
                                    source_url=rendered["source_url"],
                                    output_filename=rendered["filename"],
                                    channel="video_AI_hay",
                                )
                                cr_sync.append_to_master_csv(
                                    filename=rendered["filename"],
                                    source_url=rendered["source_url"],
                                    caption=rendered["caption"],
                                    hashtags=rendered["hashtags"],
                                    status="READY",
                                    channel="video_AI_hay",
                                    posted_at="",
                                )
                            except Exception:
                                pass
                            target_video = Path(rendered["final_video"])
                        else:
                            target_video = None

                    if target_video and target_video.exists():
                        sel_ep = parse_gausohai_ep_and_part(target_video.name) or (0, 0)
                        logger.info(
                            f"[{ch_folder}] [Multi-Episode Wave 4:1 Guard (No 1:1 Alternating)] "
                            f"Chọn đăng: Gấu Trúc Đen Tập {sel_ep[0]} Phần {sel_ep[1]} "
                            f"(cooldown={sorted(recent_cooldown_eps)} | bài trước: Tập {last_ep_num} Phần {last_part_num})"
                        )
                except Exception as gsh_int_exc:
                    logger.warning(f"[{ch_folder}] Lỗi Gấu Trúc Đen Interleaved Guard: {gsh_int_exc}")
                    target_video = None
            else:
                target_video = valid_candidates[0]

        if not target_video:
            if ch_folder == "audio_dem_khuyen":
                try:
                    from .bongcuc_splitter import BongCucYouTubeSplitter

                    BongCucYouTubeSplitter(project_root=self.project_root).build_interleaved_queue(
                        posted_ids
                    )
                except Exception:
                    pass
            return None

        caption = ""
        hashtags = ""
        m_id = re.search(
            r"(?:tiktok|douyin|youtube_shorts|facebook_reels|instagram_reels|bilibili|xiaohongshu)_story_([A-Za-z0-9_-]+)",
            target_video.stem,
            re.IGNORECASE,
        )
        vid_id = re.split(r"_(?:bongcuc|gautrucden)_", m_id.group(1))[0] if m_id else ""

        csv_path = self.project_root / "output" / "danh_sach_video.csv"
        if csv_path.exists():
            try:
                with open(csv_path, mode="r", newline="", encoding="utf-8", errors="replace") as rf:
                    for row in csv.DictReader(rf):
                        fn = (row.get("filename") or "").strip()
                        s_url = (row.get("source_url") or "").strip()
                        if Path(fn).name == target_video.name or (
                            vid_id and (vid_id in fn or vid_id in s_url)
                        ):
                            row_cap = (row.get("caption") or "").strip()
                            row_tags = (row.get("hashtags") or "").strip()
                            if row_cap and not self.crawler._is_generic_fallback_caption(row_cap):
                                caption = row_cap
                                hashtags = row_tags
                            elif not caption:
                                caption = row_cap
                                hashtags = row_tags
            except Exception:
                pass

        is_bongcuc_missing_title = (
            ch_folder == "audio_dem_khuyen"
            and ("bongcuc_" in target_video.name.lower() or "9900" in target_video.name)
            and (
                "bông cúc" in (caption or "").lower()
                or "bc số" in (caption or "").lower()
                or "bongcuc" in (hashtags or "").lower()
                or "phần " not in (caption or "").lower()
            )
        ) or (
            ch_folder == "video_AI_hay"
            and ("gautrucden_" in target_video.name.lower() or "8800" in target_video.name)
            and (
                "gấu sợ hãi" in (caption or "").lower()
                or "gausohai" in (hashtags or "").lower()
                or "phần " not in (caption or "").lower()
            )
        )
        if (
            self.crawler.is_raw_filename_caption(caption, target_video.stem)
            or not hashtags.strip()
            or is_bongcuc_missing_title
            or self.crawler._is_generic_fallback_caption(caption)
        ):
            rec_cap, rec_tags, _ = self.crawler.recover_contextual_caption_and_hashtags(
                ch_folder, target_video.name
            )
            if (
                self.crawler.is_raw_filename_caption(caption, target_video.stem)
                or is_bongcuc_missing_title
                or (self.crawler._is_generic_fallback_caption(caption) and not self.crawler._is_generic_fallback_caption(rec_cap))
            ):
                caption = rec_cap
            if not hashtags.strip() or is_bongcuc_missing_title:
                hashtags = rec_tags
            try:
                self.crawler._upgrade_master_csv_schema()
            except Exception:
                pass

        # --- KÍCH HOẠT SKILL DÒNG 1: KIỂM TRA & CƯỠNG CHẾ 6 LỚP BỘ LỌC CHỐNG PHÁT HIỆN TIKTOK TRƯỚC KHI ĐĂNG ---
        try:
            from .stream1_antidetect_skill import Stream1SixLayerAntiDetectSkill

            skill_6layer = Stream1SixLayerAntiDetectSkill(project_root=self.project_root)
            passed_6l, target_video, caption, hashtags, rep_6l = (
                skill_6layer.verify_and_enforce_before_upload(
                    video_path=target_video,
                    channel_folder=ch_folder,
                    caption=caption,
                    hashtags=hashtags,
                    last_posted_bongcuc_ep=locals().get("last_ep_num"),
                )
            )
            self._last_6layer_report = rep_6l
            if not passed_6l:
                logger.warning(
                    f"[{ch_folder}] [Skill 6 Lớp Anti-Detect] Video {target_video.name} không vượt qua kiểm tra 6 lớp -> Bỏ qua và chọn ứng viên kế tiếp..."
                )
                if target_video.exists():
                    target_video.unlink(missing_ok=True)
                return self._get_next_ready_video_for_channel(ch_folder)
        except Exception as sk_exc:
            logger.debug(f"[{ch_folder}] Bỏ qua lỗi phụ trong Skill 6 Lớp: {sk_exc}")

        return target_video, caption, hashtags


    def _was_channel_uploaded_recently(
        self, ch_folder: str, min_interval_minutes: float = 180.0
    ) -> Optional[float]:
        """
        Kiểm tra 3 nguồn độc lập (`posted_schedule_history.json`, `uploaded_videos_registry.json`,
        `remote_telemetry/intel_status_report.json`) xem kênh `ch_folder` có vừa đăng bài trong vòng
        `min_interval_minutes` phút (mặc định 180 phút = 3 tiếng) gần đây hay không.
        Trả về số phút đã trôi qua nếu vừa đăng gần đây, hoặc `None` nếu đã đủ giãn cách an toàn.
        """
        now_dt = datetime.now()
        timestamps_to_check: List[str] = []

        # 1. Kiểm tra posted_schedule_history.json (chỉ tính các khóa xác nhận đã đăng thành công, không tính khóa tạm remote_post_)
        try:
            hist = self._load_post_history()
            for k, ts_val in hist.items():
                k_str = str(k)
                if (
                    ch_folder in k_str
                    and "filter24h" not in k_str
                    and not k_str.startswith("remote_post_")
                    and isinstance(ts_val, str)
                ):
                    timestamps_to_check.append(ts_val.strip())
        except Exception:
            pass

        # 2. Kiểm tra uploaded_videos_registry.json
        try:
            reg_path = self.project_root / "uploaded_videos_registry.json"
            if reg_path.exists():
                reg_data = json.loads(reg_path.read_text(encoding="utf-8"))
                for item in reg_data.get("history") or []:
                    if isinstance(item, dict) and item.get("channel") == ch_folder and item.get("timestamp"):
                        timestamps_to_check.append(str(item["timestamp"]).strip())
        except Exception:
            pass

        # 3. Kiểm tra remote_telemetry/intel_status_report.json
        try:
            rep_path = self.project_root / "remote_telemetry" / "intel_status_report.json"
            if rep_path.exists():
                rep_data = json.loads(rep_path.read_text(encoding="utf-8"))
                for ev in rep_data.get("recent_events") or []:
                    if (
                        isinstance(ev, dict)
                        and ev.get("event_type") == "upload"
                        and ev.get("channel") == ch_folder
                        and ev.get("timestamp")
                    ):
                        timestamps_to_check.append(str(ev["timestamp"]).strip())
        except Exception:
            pass

        min_elapsed: Optional[float] = None
        for ts_str in timestamps_to_check:
            try:
                dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
                diff_min = (now_dt - dt).total_seconds() / 60.0
                if 0.0 <= diff_min < min_interval_minutes:
                    if min_elapsed is None or diff_min < min_elapsed:
                        min_elapsed = diff_min
            except Exception:
                pass
        return min_elapsed


    def upload_one_channel_video(
        self,
        channel_arg: str,
        headless: bool = False,
        force: bool = False,
        delete_match_keywords: Optional[List[str]] = None,
        delete_top_before_upload: bool = False,
        skip_warmup: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Tự động đăng 1 video từ `output/<ten_kenh>/ready/` lên kênh TikTok tương ứng với đầy đủ 5 bước:
        1. Mở Chrome thật với profile riêng `browser_profiles/<ten_kenh>/` + Stealth JS + Human Warm-up.
        2. Vào trang `tiktokstudio/upload?from=webapp&tab=video`, nạp file video.
        3. Xóa sạch tên file mặc định trong ô `Mô tả`, gõ Kịch bản ngắn + chọn từng Hashtag Hot Search.
        4. Tự động cấu hình mục `Cài đặt` & `Kiểm tra`:
           - `Thời điểm đăng`: `Bây giờ`
           - `Người có thể nhìn thấy bài đăng này`: `Mọi người` (Công khai)
           - `Nội dung tải lên chất lượng cao`: `ON`
           - `Cho phép người dùng`: `Bình luận` (`ON`)
           - `Khai báo nội dung bài đăng` & `Nội dung do AI tạo`: `OFF`
           - `Kiểm tra bản quyền nhạc` & `Kiểm tra nội dung nhanh`: `ON`
        5. Đợi tải video đạt 100%, bấm chính xác nút đỏ `Đăng` (`post_video_button`), xác nhận bài đăng thành công
           rồi chuyển sang trang `tiktokstudio/content` (mục `Bài đăng`) để đảm bảo `Quyền riêng tư` là `Mọi người` (Công khai).
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài đặt `playwright`. Vui lòng chạy: `./venv/bin/pip install playwright && ./venv/bin/playwright install chromium`"
            ) from exc

        profile = resolve_channel_profile(channel_arg)
        ch_folder = profile["folder_name"]

        if not force:
            recent_min = self._was_channel_uploaded_recently(ch_folder, min_interval_minutes=180.0)
            if recent_min is not None:
                logger.info(
                    f"[{ch_folder}] [Anti-Double-Post Guard] Kênh vừa đăng bài cách đây {recent_min:.1f} phút (< 180 phút). "
                    "Chặn tuyệt đối đăng 2 video liên tiếp!"
                )
                return None

        user_data_dir = self.profiles_root / ch_folder
        synced_sess = self.project_root / "remote_telemetry" / "sessions" / f"{ch_folder}.bin"
        if not user_data_dir.exists() and synced_sess.exists():
            user_data_dir.mkdir(parents=True, exist_ok=True)
        if not user_data_dir.exists():
            logger.warning(
                f"Kênh [{ch_folder}] chưa có phiên đăng nhập tại {user_data_dir}. "
                f"Hãy chạy: `./venv/bin/python main.py --login-channel {ch_folder}` trước."
            )
            return None

        next_item = self._get_next_ready_video_for_channel(ch_folder)
        if not next_item:
            logger.info(
                f"[{ch_folder}] Kho ready/ đã lọc hết các bản cũ/trùng nội dung -> Đang tự động cào ngay 1 video triệu view mới cho [{ch_folder}]..."
            )
            try:
                from .main import run_crawl_story_mode

                run_crawl_story_mode(channel=ch_folder, limit=1)
                next_item = self._get_next_ready_video_for_channel(ch_folder)
            except Exception as cr_exc:
                logger.warning(f"[{ch_folder}] Lỗi phụ khi cào bổ sung khẩn cấp: {cr_exc}")
        if not next_item:
            logger.info(f"Kênh [{ch_folder}] hiện không có video nào trong ready/ để đăng.")
            return None

        video_path, caption, hashtags = next_item
        cfg = self.ensure_schedule_config()
        from .master_switch_controller import MasterSwitchController

        interact_on = MasterSwitchController(project_root=self.project_root).is_interact_enabled("tiktok")
        do_warmup = interact_on and bool(cfg.get("warmup_before_upload", True)) and not skip_warmup

        logger.info(
            f"=== BẮT ĐẦU TỰ ĐỘNG ĐĂNG TIKTOK (FULL SETTING + CÔNG KHAI) KÊNH [{ch_folder}] ===\n"
            f"  + File: {video_path.name}\n"
            f"  + Kịch bản ngắn: {caption}\n"
            f"  + Hashtags Hot Search: {hashtags}"
        )

        # Làm mới dấu thời gian khóa trình duyệt ngay sau khi cào/render xong để Watchdog không tính gộp thời gian render
        self._refresh_browser_process_lock()

        with sync_playwright() as p:
            context = self._launch_stealth_chrome_context(
                playwright_obj=p, user_data_dir=user_data_dir, headless=headless
            )

            try:
                page = context.pages[0] if context.pages else context.new_page()

                # Bước 1: Human Warm-up lướt dạo trang chủ TikTok trước khi đăng
                if do_warmup:
                    self._simulate_human_warmup(page, ch_folder)

                deleted_posts: List[str] = []
                if delete_match_keywords or delete_top_before_upload:
                    deleted_posts = self._delete_post_in_content_page(
                        page=page,
                        ch_folder=ch_folder,
                        match_keywords=delete_match_keywords,
                        delete_top_if_no_match=delete_top_before_upload,
                    )

                # Bước 2: Truy cập TikTok Studio Upload đúng URL giao diện WebApp (có retry chống lỗi ngắt mạng tạm thời)
                for _nav_attempt in range(3):
                    try:
                        page.goto(
                            "https://www.tiktok.com/tiktokstudio/upload?from=webapp&tab=video",
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                        if "chromewebdata" not in (page.url or "").lower():
                            break
                    except Exception as _nav_err:
                        if _nav_attempt == 2:
                            raise _nav_err
                    time.sleep(3.0)
                time.sleep(random.uniform(4.5, 7.0))

                if "login" in page.url.lower():
                    raise RuntimeError(
                        f"Phiên đăng nhập TikTok của kênh [{ch_folder}] đã hết hạn. "
                        f"Vui lòng chạy lại `./venv/bin/python main.py --login-channel {ch_folder}`."
                    )

                # Bước 3: Chọn file video đưa vào input[type="file"]
                if not video_path.exists():
                    retry_item = self._get_next_ready_video_for_channel(ch_folder)
                    if retry_item:
                        video_path, caption, hashtags = retry_item
                file_input = page.locator('input[type="file"]').first
                file_input.wait_for(state="attached", timeout=30000)
                file_input.set_input_files(str(video_path))
                logger.info(
                    f"[{ch_folder}] Đã nạp file video vào TikTok Studio. Đang chờ tải lên 100% và tự động đóng các hộp thoại hướng dẫn ('Đã hiểu')..."
                )

                # Bước 4: Đợi video tải lên đạt 100% TRƯỚC, kiểm tra đúng nút đỏ `post_video_button` (không nhầm với 'Bài đăng' ở menu trái!)
                ready_to_post = False
                for _ in range(120):
                    time.sleep(2)
                    self._dismiss_all_studio_popovers(page, ch_folder)
                    try:
                        if not self._is_video_upload_100_percent_done(page):
                            continue
                        btn_info = self._find_exact_post_button(page, scroll_into_view=False)
                        if btn_info and btn_info.get("found") and btn_info.get("enabled"):
                            ready_to_post = True
                            break
                    except Exception:
                        pass

                if not ready_to_post:
                    raise RuntimeError(
                        f"[{ch_folder}] Quá thời gian chờ tải video lên TikTok Studio (240s)."
                    )

                self._refresh_browser_process_lock()

                # Đợi thêm 1.5s sau khi tải xong 100% để bắt trọn mọi popup 'Đã hiểu' xuất hiện muộn
                time.sleep(1.5)
                self._dismiss_all_studio_popovers(page, ch_folder)

                # Bước 5: Xóa sạch tên file mặc định (20261025_...) trong ô Mô tả & gõ Kịch bản ngắn + Hashtags Hot Search
                self._clear_and_type_caption_hashtags(
                    page=page,
                    ch_folder=ch_folder,
                    caption=caption,
                    hashtags_str=hashtags,
                    video_stem=video_path.stem,
                )

                # Bước 6: Tự động nhận diện AIGC cho từng video và cấu hình toàn bộ mục Cài đặt & Kiểm tra
                self._dismiss_all_studio_popovers(page, ch_folder)
                need_aigc_label = self.detect_video_aigc_disclosure_needed(
                    video_path=video_path,
                    caption=caption,
                    hashtags=hashtags,
                    ch_folder=ch_folder,
                )
                self._configure_upload_settings_public(page, ch_folder, aigc_on=need_aigc_label)
                self._dismiss_all_studio_popovers(page, ch_folder)

                # Cuộn khung form xuống cuối trang tới đúng nút đỏ `Đăng` (post_video_button) và click
                self._scroll_upload_form(page, 900)
                time.sleep(random.uniform(1.0, 1.5))
                self._dismiss_all_studio_popovers(page, ch_folder)

                # Chụp ảnh màn hình ngay trước khi bấm Đăng để có thể kiểm tra từ xa
                try:
                    telem_dir = self.project_root / "remote_telemetry"
                    telem_dir.mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(telem_dir / f"latest_upload_{ch_folder}_before_post.jpg"),
                        type="jpeg",
                        quality=60,
                    )
                except Exception:
                    pass

                self._refresh_browser_process_lock()
                post_btn_clicked = False
                btn_info = self._find_exact_post_button(page, scroll_into_view=True)
                time.sleep(0.8)
                btn_info = self._find_exact_post_button(page, scroll_into_view=False) or btn_info
                if btn_info and btn_info.get("found"):
                    click_x = float(btn_info["x"])
                    click_y = float(btn_info["y"])
                    logger.info(
                        f"[{ch_folder}] Đang bấm nút đỏ ĐĂNG ('{btn_info.get('text')}', data-e2e='{btn_info.get('dataE2e')}') tại ({click_x:.1f}, {click_y:.1f})..."
                    )
                    self._os_click_viewport(page, click_x, click_y)
                    post_btn_clicked = True
                    time.sleep(0.6)
                    try:
                        still_there = self._find_exact_post_button(page, scroll_into_view=False)
                        if still_there and still_there.get("found") and still_there.get("enabled"):
                            page.mouse.click(float(still_there["x"]), float(still_there["y"]))
                            time.sleep(0.4)
                            page.evaluate(
                                """() => {
                                    const b = document.querySelector('button[data-e2e="post_video_button"], [data-e2e="post_video_button"]');
                                    if (b && !b.disabled) b.click();
                                }"""
                            )
                    except Exception:
                        pass
                else:
                    exact_loc = page.locator('button[data-e2e="post_video_button"]').first
                    exact_loc.scroll_into_view_if_needed()
                    exact_loc.click()
                    post_btn_clicked = True

                logger.info(
                    f"[{ch_folder}] Đã bấm nút ĐĂNG! Đang chờ TikTok Studio xác nhận đăng bài hoàn tất..."
                )

                # Đợi đến khi TikTok thực sự gửi bài đăng thành công (chuyển sang tiktokstudio/content hoặc hiện thông báo đã đăng)
                post_confirmed = False
                for wait_idx in range(30):
                    time.sleep(1.5)
                    self._dismiss_all_studio_popovers(page, ch_folder)
                    self._click_confirm_modal_if_present(page)

                    if "tiktokstudio/content" in page.url:
                        post_confirmed = True
                        break

                    # Kiểm tra thông báo đăng thành công trên màn hình
                    try:
                        is_done_toast = page.evaluate(
                            """() => {
                                const txt = document.body ? (document.body.innerText || '') : '';
                                return /Video đã được đăng|Đã đăng video|Quản lý bài đăng của bạn|Đăng thêm video khác|Your video is being uploaded|Video published/i.test(txt);
                            }"""
                        )
                        if is_done_toast:
                            post_confirmed = True
                            break
                    except Exception:
                        pass

                    # Nếu nút đỏ Đăng vẫn còn nguyên và đang enabled (chưa ăn click do cửa sổ lệch/mất focus), bấm kết hợp OS + Playwright + DOM click
                    if wait_idx in (5, 11, 17, 23):
                        still_btn = self._find_exact_post_button(page, scroll_into_view=True)
                        if still_btn and still_btn.get("found") and still_btn.get("enabled"):
                            logger.info(
                                f"[{ch_folder}] Nút ĐĂNG vẫn đang chờ trên màn hình (lần thử {wait_idx}), thực hiện click đa tầng (OS + Viewport + DOM) vào post_video_button..."
                            )
                            sx, sy = float(still_btn["x"]), float(still_btn["y"])
                            self._os_click_viewport(page, sx, sy)
                            try:
                                page.mouse.click(sx, sy)
                                page.evaluate(
                                    """() => {
                                        const b = document.querySelector('button[data-e2e="post_video_button"], [data-e2e="post_video_button"]');
                                        if (b && !b.disabled) b.click();
                                    }"""
                                )
                            except Exception:
                                pass

                if not post_confirmed:
                    still_unclicked = self._find_exact_post_button(page, scroll_into_view=False)
                    if still_unclicked and still_unclicked.get("found") and still_unclicked.get("enabled"):
                        try:
                            page.locator('button[data-e2e="post_video_button"]').first.click(force=True, timeout=5000)
                            for _ in range(8):
                                time.sleep(1.5)
                                self._click_confirm_modal_if_present(page)
                                if "tiktokstudio/content" in page.url:
                                    post_confirmed = True
                                    break
                        except Exception:
                            pass

                if post_confirmed:
                    logger.info(f"[{ch_folder}] TikTok Studio đã xác nhận ĐĂNG VIDEO THÀNH CÔNG!")
                else:
                    still_on_upload_btn = self._find_exact_post_button(page, scroll_into_view=False)
                    if "tiktokstudio/upload" in (page.url or "") and still_on_upload_btn and still_on_upload_btn.get("found"):
                        raise RuntimeError(
                            f"[{ch_folder}] Nút ĐĂNG vẫn chưa được gửi thành công trên trang upload (post_confirmed=False) — hủy ghi nhận giả để thử lại!"
                        )
                    logger.warning(
                        f"[{ch_folder}] Đã hết thời gian chờ phản hồi sau khi bấm Đăng, tiến hành kiểm tra tại trang Quản lý Bài đăng..."
                    )

                # Ghi nhận vào sổ cái chống đăng trùng & chuyển ngay sang posted/ sau khi đã xác nhận gửi bài
                self._record_uploaded_video_in_registry(ch_folder, video_path, caption)
                marked = self.crawler.mark_video_posted(str(video_path))

                # Bước 7 (Ảnh chụp màn hình 5): Tại trang tiktok.com/tiktokstudio/content,
                # kiểm tra cột "Quyền riêng tư" và chuyển mọi bài đang ở "Chỉ mình tôi" sang "Mọi người" (Công khai)!
                switched_public = self._ensure_posts_public_in_content_page(page, ch_folder)
                logger.info(
                    f"[{ch_folder}] Hoàn tất kiểm tra trang Bài đăng (đã chuyển {switched_public} bài từ 'Chỉ mình tôi' -> 'Mọi người')."
                )

                # Chụp ảnh màn hình kết quả sau khi đăng & đồng bộ báo cáo từ xa lên GitHub
                self._save_and_push_telemetry(
                    page=page,
                    event_type="upload",
                    ch_folder=ch_folder,
                    details={
                        "video_file": video_path.name,
                        "caption": caption,
                        "hashtags": hashtags,
                        "deleted_posts": deleted_posts,
                        "post_confirmed": post_confirmed,
                        "switched_public_count": switched_public,
                        "final_url": page.url,
                    },
                )

                dwell_sec = random.randint(8, 15)
                time.sleep(dwell_sec)

            except Exception as up_exc:
                if "[CG-PRECHECK-BLOCKED]" in str(up_exc):
                    logger.warning(
                        f"[{ch_folder}] {up_exc} -> Đang xóa file rủi ro {video_path.name} và ghi nhận vào sổ cái chặn vĩnh viễn!"
                    )
                    try:
                        self._record_uploaded_video_in_registry(ch_folder, video_path, caption)
                        video_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                elif locals().get("post_btn_clicked") and (
                    locals().get("post_confirmed")
                    or "closed" in str(up_exc).lower()
                    or "targetclosed" in str(up_exc).lower()
                ):
                    try:
                        logger.warning(
                            f"[{ch_folder}] Trình duyệt đóng ngay sau khi đã bấm nút ĐĂNG ({up_exc}) -> Vẫn ghi nhận video đã đăng để chống đăng lặp lần 2!"
                        )
                        self._record_uploaded_video_in_registry(ch_folder, video_path, caption)
                        self.crawler.mark_video_posted(str(video_path))
                    except Exception:
                        pass
                try:
                    self._save_and_push_telemetry(
                        page=page,
                        event_type="upload_error",
                        ch_folder=ch_folder,
                        details={"video_file": video_path.name, "error": str(up_exc)},
                    )
                except Exception:
                    pass
                raise
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        logger.info(f"[{ch_folder}] HOÀN TẤT ĐĂNG TỰ ĐỘNG CÔNG KHAI & ĐỒNG BỘ POSTED: {marked['filename']}")
        return marked

