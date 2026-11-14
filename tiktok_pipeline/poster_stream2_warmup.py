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
from .growth_24h_filter import TikTok24hFilterEngine
from .utils import logger


class PosterStream2WarmupMixin:
    """Dòng 2 (Bộ lọc tương tác 24h) và chế độ lướt FYP nuôi 10 kênh như người thật."""

    def run_24h_filter_for_channel(
        self,
        channel_arg: str,
        video_count: int = 10,
        headless: bool = False,
    ) -> Dict[str, Any]:
        """
        Thực thi tự động "Bộ lọc 24 giờ" (Xem hết video -> Thả tim -> Sao chép liên kết ->
        Thêm vào yêu thích -> Follow chủ kênh -> Bình luận có ý nghĩa xác nhận đã follow)
        cho 1 kênh hoặc toàn bộ 4 kênh (`all`).
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài đặt `playwright`. Vui lòng chạy: `./venv/bin/pip install playwright && ./venv/bin/playwright install chromium`"
            ) from exc

        if channel_arg.strip().lower() == "all":
            target_channels = [p["folder_name"] for p in CHANNEL_PROFILES.values()]
        elif "," in channel_arg:
            target_channels = [
                resolve_channel_profile(c.strip())["folder_name"]
                for c in channel_arg.split(",")
                if c.strip()
            ]
        else:
            target_channels = [resolve_channel_profile(channel_arg)["folder_name"]]

        summary: Dict[str, Any] = {}
        for ch_folder in target_channels:
            try:
                from .master_switch_controller import MasterSwitchController

                if not MasterSwitchController(project_root=self.project_root).is_interact_enabled("tiktok"):
                    logger.info("[Master Switch] Khóa Master/Tương tác vừa TẮT -> Dừng mở trình duyệt các kênh kế tiếp!")
                    break
                import importlib
                from . import growth_24h_filter as _g24_mod

                importlib.reload(_g24_mod)
                self.filter_24h = _g24_mod.TikTok24hFilterEngine(
                    project_root=self.project_root,
                    desktop=self.desktop,
                    human_move_fn=self._human_move_mouse,
                    os_click_fn=self._os_click_viewport,
                )
                in_cd, cd_reason = self.filter_24h.is_in_cooldown(ch_folder)
                if in_cd:
                    logger.info(
                        f"[{ch_folder}] [Dòng 2 - Chế Độ Xả Phạt Tự Động] Bỏ qua mở trình duyệt kênh [{ch_folder}] ({cd_reason}) -> "
                        "Sẽ tự động mở lại ngay khi hết thời gian xả phạt!"
                    )
                    summary[ch_folder] = {
                        "status": "COOLDOWN",
                        "cooldown_reason": cd_reason,
                        "interacted_count": 0,
                        "records": [],
                    }
                    continue
            except Exception:
                pass

            user_data_dir = self.profiles_root / ch_folder
            if not user_data_dir.exists():
                logger.info(
                    f"Kênh [{ch_folder}] chưa có thư mục profile tại {user_data_dir}, đang khởi tạo profile mới..."
                )
                user_data_dir.mkdir(parents=True, exist_ok=True)

            logger.info(
                f"=== BẮT ĐẦU CHẠY TỰ ĐỘNG 'BỘ LỌC 24 GIỜ' CHO KÊNH [{ch_folder}] ({video_count} video mục tiêu) ==="
            )
            with sync_playwright() as p:
                context = self._launch_stealth_chrome_context(
                    playwright_obj=p, user_data_dir=user_data_dir, headless=headless
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    records = self.filter_24h.run_session_on_page(
                        page=page, ch_folder=ch_folder, video_count=video_count
                    )
                    summary[ch_folder] = {
                        "status": "OK",
                        "interacted_count": len(records),
                        "records": records,
                    }
                    self._save_and_push_telemetry(
                        page=page,
                        event_type="filter24h",
                        ch_folder=ch_folder,
                        details={
                            "interacted_count": len(records),
                            "own_profile_stats": getattr(self.filter_24h, "last_own_profile_stats", {}),
                            "records": records,
                        },
                    )
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass

        return summary


    def check_and_run_stream2_filter24h_schedule(self) -> Dict[str, Any]:
        """
        [DÒNG HOẠT ĐỘNG 2 — MỞ 4 KÊNH CHẠY BỘ LỌC 24 GIỜ (7 LẦN/NGÀY × 10 VIDEO/KÊNH)]:
        Tự động mở lần lượt 4 kênh theo 7 khung giờ mỗi ngày (`channel_filter_24h_times`),
        mỗi kênh tương tác `filter_24h_videos_per_session` (mặc định 10 video).
        Nếu Cửa sổ 1 (`--stream-upload`) bị dừng khi vắng nhà, tự động hồi sinh Cửa sổ 1 từ xa.
        """
        from .master_switch_controller import MasterSwitchController

        try:
            self._ensure_intel_bridge_daemon_running()
        except Exception:
            pass
        try:
            self._auto_revive_stream1_upload_if_dead()
        except Exception:
            pass
        try:
            self._auto_revive_stream2_filter24h_if_dead()
        except Exception:
            pass

        ms_ctrl = MasterSwitchController(project_root=self.project_root)
        if ms_ctrl.is_upload_enabled("tiktok"):
            try:
                import importlib
                from . import community_guidelines_guard as _cg_mod
                from . import bongcuc_splitter as _bc_mod
                from . import gausohai_splitter as _gsh_mod
                from . import crawler as _cr_mod
                from . import stream1_antidetect_skill as _s1_mod

                importlib.reload(_cg_mod)
                importlib.reload(_bc_mod)
                importlib.reload(_gsh_mod)
                importlib.reload(_cr_mod)
                importlib.reload(_s1_mod)
                self.crawler = _cr_mod.StoryAudioCrawler(project_root=self.project_root)
            except Exception:
                pass
            try:
                self.check_and_run_stream1_upload_schedule()
            except Exception as up_exc:
                logger.error(f"[Dòng 1 via Watchdog] Lỗi kiểm tra lịch đăng video: {up_exc}")

        if not ms_ctrl.is_interact_enabled("tiktok"):
            return {}

        cfg = self.ensure_schedule_config()
        filter_enabled = bool(cfg.get("filter_24h_enabled", True))
        filter_count = max(1, min(int(cfg.get("filter_24h_videos_per_session", 3)), 3))
        filter_times_map: Dict[str, List[str]] = cfg.get("channel_filter_24h_times") or {}
        max_jitter = int(cfg.get("random_jitter_minutes", 9))
        headless = bool(cfg.get("headless", False))

        history = self._load_post_history()
        filter_results: Dict[str, Any] = {}

        # 0b. Kiểm tra lệnh kích hoạt chạy thử Bộ lọc 24h từ xa (Remote Trigger)
        remote_trig = cfg.get("remote_trigger_filter_24h")
        if isinstance(remote_trig, dict) and remote_trig.get("trigger_id"):
            import platform as _plat

            target_arch = str(remote_trig.get("target_arch") or "").strip()
            if not target_arch or _plat.machine() == target_arch:
                trig_id = str(remote_trig["trigger_id"]).strip()
                trig_key = f"remote_trig_{trig_id}"
                if trig_id and trig_key not in history:
                    lock_fh = self._acquire_browser_process_lock()
                    try:
                        history = self._load_post_history()
                        if trig_key not in history:
                            trig_ch = str(remote_trig.get("channel") or "all").strip()
                            trig_cnt = int(remote_trig.get("video_count") or filter_count)
                            history[trig_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self._save_post_history(history)
                            logger.info(
                                f"[Dòng 2 - Bộ lọc 24h] Nhận lệnh chạy ngay từ máy Silicon ({trig_id}) -> "
                                f"Kênh [{trig_ch}], số lượng {trig_cnt} video/kênh!"
                            )
                            res = self.run_24h_filter_for_channel(trig_ch, video_count=trig_cnt, headless=headless)
                            filter_results[trig_key] = res
                    except Exception as exc:
                        logger.error(f"[Dòng 2 - Bộ lọc 24h] Lỗi khi chạy Remote Trigger ({trig_id}): {exc}")
                    finally:
                        self._release_browser_process_lock(lock_fh)

        if not filter_enabled:
            return filter_results

        # 0c. Chế độ Xả Phạt Tự Động 72 giờ (Auto-Cooldown & Auto-Resume sau 3 ngày):
        cooldown_until_str = str(cfg.get("filter_24h_cooldown_until") or "").strip()
        if cooldown_until_str:
            try:
                cd_dt = datetime.strptime(cooldown_until_str, "%Y-%m-%d %H:%M:%S")
                now_dt = datetime.now()
                if now_dt < cd_dt:
                    last_log = getattr(self, "_last_cooldown_log_ts", 0.0)
                    if time.time() - last_log >= 1800.0:
                        rem_hours = (cd_dt - now_dt).total_seconds() / 3600.0
                        logger.info(
                            f"[Dòng 2 - Chế Độ Xả Phạt 72h] Đang tạm nghỉ Bộ lọc 24h đến {cooldown_until_str} "
                            f"(còn {rem_hours:.1f} giờ) -> Sẽ TỰ ĐỘNG MỞ LẠI ngay khi đủ 3 ngày!"
                        )
                        self._last_cooldown_log_ts = time.time()
                    return filter_results
                elif not getattr(self, "_cooldown_resume_logged", False):
                    logger.info(
                        f"[Dòng 2 - Tự Động Mở Lại Sau 3 Ngày] Đã vượt qua mốc xả phạt {cooldown_until_str} -> "
                        "Tự động kích hoạt lại Bộ lọc 24 giờ với nhịp độ an toàn (3 lần/ngày × 3 video)!"
                    )
                    self._cooldown_resume_logged = True
            except Exception:
                pass

        default_3_slots = ["09:30", "15:00", "21:00"]
        active_ch_set = set(
            cfg.get("active_filter_24h_channels")
            or cfg.get("active_channels")
            or ["video_AI_hay", "baby_AI", "audio_dem_khuyen", "tu_chan_gioi"]
        )
        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
            if ch_folder not in active_ch_set:
                continue
            try:
                in_cd, _ = self.filter_24h.is_in_cooldown(ch_folder)
                if in_cd:
                    continue
            except Exception:
                pass
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            now_minutes = now.hour * 60 + now.minute
            history = self._load_post_history()

            f_slots = filter_times_map.get(ch_folder) or default_3_slots
            for round_idx, f_slot in enumerate(f_slots, start=1):
                f_key = f"{today_str}_filter24h_{ch_folder}_{f_slot}"
                if f_key in history:
                    continue
                try:
                    fh, fm = map(int, f_slot.split(":"))
                except Exception:
                    continue
                f_jitter = self._compute_daily_jitter_minutes(today_str, f"f24_{ch_folder}", f_slot, max_jitter)
                eff_f_min = max(0, min(23 * 60 + 59, fh * 60 + fm + f_jitter))

                # Cửa sổ kích hoạt 120 phút kèm giãn cách IP tối thiểu 15 phút giữa 2 kênh liên tiếp trên cùng máy
                if 0 <= (now_minutes - eff_f_min) <= 120:
                    last_any_ts = str(history.get("last_any_filter24h_ts") or "").strip()
                    if last_any_ts:
                        try:
                            last_dt = datetime.strptime(last_any_ts, "%Y-%m-%d %H:%M:%S")
                            gap_min = (now - last_dt).total_seconds() / 60.0
                            if 0 <= gap_min < 15.0:
                                continue
                        except Exception:
                            pass

                    lock_fh = self._acquire_browser_process_lock()
                    try:
                        history = self._load_post_history()
                        if f_key in history:
                            continue
                        now_claim_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        history[f_key] = now_claim_str
                        history["last_any_filter24h_ts"] = now_claim_str
                        self._save_post_history(history)
                        eff_hh, eff_mm = eff_f_min // 60, eff_f_min % 60
                        logger.info(
                            f"[Dòng 2 - Bộ lọc 24h (Lần {round_idx}/{len(f_slots)} trong ngày)] "
                            f"Mở kênh [{ch_folder}] lúc {eff_hh:02d}:{eff_mm:02d} (khung {f_slot}) -> Chạy tối đa {filter_count} video (max 1 Follow)!"
                        )
                        res = self.run_24h_filter_for_channel(
                            ch_folder, video_count=filter_count, headless=headless
                        )
                        filter_results[f_key] = res
                        history = self._load_post_history()
                        history["last_any_filter24h_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self._save_post_history(history)
                        logger.info(
                            f"[Dòng 2 - Bộ lọc 24h] Đã hoàn tất phiên [{ch_folder}] (khung {f_slot}) -> "
                            "Nghỉ giãn cách IP tối thiểu 15 phút trước khi mở kênh kế tiếp..."
                        )
                        return filter_results
                    except Exception as exc:
                        logger.error(
                            f"[Dòng 2 - Bộ lọc 24h] Lỗi chạy kênh [{ch_folder}] khung {f_slot}: {exc}"
                        )
                    finally:
                        self._release_browser_process_lock(lock_fh)

        return filter_results


    def _focus_and_play_tiktok_video(self, page: Any, ch_folder: str) -> Tuple[float, float]:
        """
        Đóng thanh tìm kiếm/địa chỉ của Chrome (nếu đang mở) bằng cách click vào vùng nền tối trung tính (x=295, y=420)
        để KHÔNG làm tạm dừng (pause) video đang tự chạy trên FYP, và chỉ click vào tâm video nếu video đang thực sự bị paused.
        """
        vid_x, vid_y = 560.0, 450.0
        try:
            self.desktop.activate_chrome()
        except Exception:
            pass
        try:
            page.bring_to_front()
        except Exception:
            pass

        # 1. Đóng dropdown thanh địa chỉ / ô tìm kiếm bằng phím Escape và blur mọi thẻ input
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        # Click vào vùng nền tối trung tính giữa sidebar trái và khung video (x=295, y=420) để lấy focus cửa sổ mà không pause video
        try:
            self._os_click_viewport(page, 295.0, 420.0)
            page.wait_for_timeout(350)
        except Exception:
            pass

        try:
            info = page.evaluate(
                """() => {
                    try {
                        if (document.activeElement && ['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
                            document.activeElement.blur();
                        }
                    } catch (e) {}
                    const videos = Array.from(document.querySelectorAll('video'));
                    for (const v of videos) {
                        const r = v.getBoundingClientRect();
                        if (r.width >= 120 && r.height >= 150 && r.top >= -40 && r.top < window.innerHeight * 0.7) {
                            try {
                                v.muted = false;
                                v.volume = 0.5;
                                if (v.paused) v.play().catch(() => {});
                            } catch (e) {}
                            return {
                                x: r.left + r.width * 0.45,
                                y: r.top + r.height * 0.48,
                                paused: Boolean(v.paused)
                            };
                        }
                    }
                    return null;
                }"""
            )
            if isinstance(info, dict) and info.get("x") and info.get("y"):
                vid_x = float(info["x"])
                vid_y = float(info["y"])
                if info.get("paused"):
                    self._os_click_viewport(page, vid_x, vid_y)
                    page.wait_for_timeout(400)
        except Exception:
            pass

        return vid_x, vid_y


    def _scroll_to_next_fyp_video(self, page: Any, vid_x: float, vid_y: float) -> None:
        """
        Chuyển sang đúng 1 video tiếp theo trên trang Đề xuất (FYP) TikTok:
        - Dùng phím `ArrowDown` chuẩn của TikTok Web FYP trước (tránh gọi đồng thời cả wheel + ArrowDown gây nhảy cóc 2 video).
        - Chỉ dùng con lăn chuột `page.mouse.wheel` làm dự phòng nếu sau 0.9s video hoạt động chưa chuyển sang clip mới.
        """
        prev_sig = ""
        try:
            prev_sig = str(
                page.evaluate(
                    """() => {
                        if (document.activeElement && ['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
                            document.activeElement.blur();
                        }
                        const videos = Array.from(document.querySelectorAll('video'));
                        for (const v of videos) {
                            const r = v.getBoundingClientRect();
                            if (r.height > 150 && r.top >= -50 && r.top < window.innerHeight * 0.65) {
                                return (v.currentSrc || v.src || '') + '|' + Math.round(r.top);
                            }
                        }
                        return '';
                    }"""
                )
                or ""
            )
        except Exception:
            pass

        try:
            jitter_x = vid_x + random.uniform(-35, 35)
            jitter_y = vid_y + random.uniform(-35, 35)
            self._human_move_mouse(page, jitter_x, jitter_y)
            page.wait_for_timeout(150)
            page.keyboard.press("ArrowDown")
        except Exception:
            try:
                self.desktop.key_press_os("arrow-down")
            except Exception:
                pass

        page.wait_for_timeout(random.randint(950, 1350))

        # Kiểm tra xem đã sang video mới chưa; nếu chưa thì mới cuộn con lăn chuột 1 nhịp
        try:
            state_after = page.evaluate(
                """(oldSig) => {
                    const videos = Array.from(document.querySelectorAll('video'));
                    for (const v of videos) {
                        const r = v.getBoundingClientRect();
                        if (r.height > 150 && r.top >= -50 && r.top < window.innerHeight * 0.65) {
                            const curSig = (v.currentSrc || v.src || '') + '|' + Math.round(r.top);
                            return { changed: Boolean(!oldSig || curSig !== oldSig), paused: Boolean(v.paused) };
                        }
                    }
                    return { changed: true, paused: false };
                }""",
                prev_sig,
            )
            if isinstance(state_after, dict) and not state_after.get("changed"):
                page.mouse.wheel(0, random.randint(680, 780))
                page.wait_for_timeout(random.randint(900, 1250))
        except Exception:
            pass

        # Đảm bảo video mới đang chạy (không bị paused)
        try:
            is_paused = page.evaluate(
                """() => {
                    const videos = Array.from(document.querySelectorAll('video'));
                    for (const v of videos) {
                        const r = v.getBoundingClientRect();
                        if (r.height > 150 && r.top >= -50 && r.top < window.innerHeight * 0.65) {
                            if (v.paused) {
                                try { v.play().catch(() => {}); } catch (e) {}
                            }
                            return Boolean(v.paused);
                        }
                    }
                    return false;
                }"""
            )
            if is_paused:
                self._os_click_viewport(page, vid_x, vid_y)
                page.wait_for_timeout(350)
        except Exception:
            pass


    def warmup_browse_channel(self, ch_folder: str, browse_seconds: int = 150) -> bool:
        """
        Lướt FYP TikTok tự nhiên bằng CoreGraphics CGEvent:
        - Tự động nhận diện và lướt qua nhanh các video Quảng cáo (`Được tài trợ` / `Sponsored`).
        - Xem video tự nhiên 9-17s kèm di chuyển chuột Bezier và tương tác thật (Thả tim / Follow tự nhiên bằng chuột phần cứng Single-Click).
        - Chụp ảnh telemetry ngay sau khi xem/tương tác xong video cuối trước khi cuộn sang video 00:01.
        """
        user_data_dir = self.profiles_root / ch_folder
        if not user_data_dir.exists():
            user_data_dir.mkdir(parents=True, exist_ok=True)

        lock_fh = self._acquire_browser_process_lock(max_wait_seconds=5)
        if lock_fh is None:
            logger.info(f"[Warmup FYP] [{ch_folder}] Trình duyệt đang bận ở phiên khác, bỏ qua lượt này.")
            return False

        import threading

        def _force_kill_hung_warmup() -> None:
            logger.warning(
                f"[Warmup Watchdog] [{ch_folder}] Phiên lướt FYP vượt quá thời gian cho phép ({browse_seconds + 90}s) — tự động đóng Chrome treo!"
            )
            self._cleanup_stale_chrome_profile_locks(user_data_dir)

        watchdog_timer = threading.Timer(float(browse_seconds + 90), _force_kill_hung_warmup)
        watchdog_timer.daemon = True
        watchdog_timer.start()

        try:
            from playwright.sync_api import sync_playwright

            logger.info(
                f"=== [DÒNG 2 - LƯỚT FYP NUÔI KÊNH] MỞ KÊNH [{ch_folder}] LƯỚT VIDEO ĐỀ XUẤT ({browse_seconds}s) ==="
            )
            with sync_playwright() as p:
                context = self._launch_stealth_chrome_context(
                    playwright_obj=p, user_data_dir=user_data_dir, headless=False
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()

                    page.goto("https://www.tiktok.com/foryou?lang=vi-VN", wait_until="domcontentloaded", timeout=35000)
                    page.wait_for_timeout(random.randint(3600, 5000))

                    try:
                        self.filter_24h.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
                    except Exception:
                        pass

                    vid_x, vid_y = self._focus_and_play_tiktok_video(page, ch_folder)
                    logger.info(
                        f"[Warmup FYP] [{ch_folder}] Đã lấy focus màn hình phát video ({vid_x:.0f}, {vid_y:.0f}) & bắt đầu lướt FYP..."
                    )

                    start = time.time()
                    scroll_count = 0
                    ads_skipped = 0
                    fyp_liked_count = 0
                    fyp_followed_count = 0
                    recent_follows_24h = self.filter_24h._count_recent_channel_follows(ch_folder, hours=24.0)

                    while time.time() - start < browse_seconds:
                        if scroll_count > 0:
                            self._scroll_to_next_fyp_video(page, vid_x, vid_y)

                        scroll_count += 1
                        clip_meta = page.evaluate(
                            """() => {
                                let isSponsored = false;
                                let duration = 0;
                                const videos = Array.from(document.querySelectorAll('video'));
                                for (const v of videos) {
                                    const r = v.getBoundingClientRect();
                                    if (r.height > 150 && r.top >= -50 && r.top < window.innerHeight * 0.65) {
                                        duration = (v.duration && isFinite(v.duration)) ? v.duration : 0;
                                        const card = v.closest('[data-e2e="recommend-list-item-container"], article, div[class*="DivItemContainer"]') || document.body;
                                        const txt = (card.innerText || '').toLowerCase();
                                        if (txt.includes('được tài trợ') || txt.includes('sponsored') || duration > 420) {
                                            isSponsored = true;
                                        }
                                        break;
                                    }
                                }
                                return { isSponsored, duration };
                            }"""
                        ) or {}

                        if clip_meta.get("isSponsored"):
                            ads_skipped += 1
                            logger.info(
                                f"[Warmup FYP] [{ch_folder}] ⏭️ Phát hiện video Quảng cáo (Được tài trợ, dài {float(clip_meta.get('duration') or 0):.0f}s) -> Lướt qua sau 1.8s..."
                            )
                            page.wait_for_timeout(random.randint(1500, 2100))
                            continue

                        watch_time = random.uniform(9.0, 16.5)
                        logger.info(
                            f"[Warmup FYP] [{ch_folder}] ▶️ Đang xem video đề xuất #{scroll_count} ({watch_time:.1f}s)..."
                        )
                        page.wait_for_timeout(int(watch_time * 1000))

                        if random.random() < 0.45:
                            self._human_move_mouse(
                                page,
                                vid_x + random.uniform(-75, 75),
                                vid_y + random.uniform(-75, 75),
                            )
                            page.wait_for_timeout(random.randint(350, 700))

                        # Tương tác người thật trên FYP: Thả tim tối đa 1 video/phiên & Follow tối đa 1 kênh/phiên (nếu kênh chưa đủ quota ngày)
                        if fyp_liked_count == 0 and scroll_count >= 2 and random.random() < 0.55:
                            try:
                                if not getattr(self.filter_24h, "_session_digg_rate_limited", False):
                                    if self.filter_24h._step2_like_video(page, ch_folder):
                                        fyp_liked_count += 1
                                        logger.info(f"[Warmup FYP] [{ch_folder}] ❤️ Đã thả tim tự nhiên 1 video hợp gu trên FYP.")
                            except Exception:
                                pass

                        if (
                            fyp_followed_count == 0
                            and recent_follows_24h < 3
                            and scroll_count >= 2
                            and random.random() < 0.65
                        ):
                            try:
                                f_info = self.filter_24h._check_follow_state(page)
                                if f_info and not f_info.get("alreadyFollowing") and f_info.get("x") and f_info.get("y"):
                                    fx, fy = float(f_info["x"]), float(f_info["y"])
                                    self._human_move_mouse(page, fx, fy)
                                    page.wait_for_timeout(random.randint(250, 450))
                                    self._os_click_viewport(page, fx, fy)
                                    page.wait_for_timeout(3800)
                                    self.filter_24h.solve_tiktok_rotation_captcha_if_present(page, ch_folder)
                                    f_after = self.filter_24h._check_follow_state(page)
                                    if f_after and f_after.get("alreadyFollowing"):
                                        fyp_followed_count += 1
                                        recent_follows_24h += 1
                                        logger.info(f"[Warmup FYP] [{ch_folder}] ➕ Đã bấm Follow tự nhiên 1 kênh trên FYP (đã xác nhận dấu tích).")
                            except Exception:
                                pass

                    # Nếu kênh đang thiếu tương tác chéo / chưa có Follow thực tế trong 24h qua,
                    # kết hợp chạy ngay 1 video 'Bộ lọc 24h' (Xem hết + Tim + Lưu + Bình luận + Follow trên trang Hồ sơ /@creator)
                    if recent_follows_24h < 2:
                        try:
                            logger.info(
                                f"[Warmup FYP -> Bộ lọc 24h] [{ch_folder}] Kênh mới có {recent_follows_24h} follow/24h -> "
                                "Chạy bổ sung 1 video tương tác chéo #tuongtaccheo để tăng Follow thật & kéo tương tác trả lễ!"
                            )
                            f24_records = self.filter_24h.run_session_on_page(page, ch_folder, video_count=1)
                            if any(r.get("followed") for r in (f24_records or [])):
                                fyp_followed_count += 1
                                recent_follows_24h += 1
                        except Exception as f24_exc:
                            logger.debug(f"[Warmup FYP] [{ch_folder}] Bỏ qua bước bổ sung 24h: {f24_exc}")

                    logger.info(
                        f"[Warmup FYP] [{ch_folder}] ✅ Hoàn tất lướt {scroll_count} video FYP (bỏ qua {ads_skipped} QC, Tim={fyp_liked_count}, Follow={fyp_followed_count}) trong {time.time() - start:.0f}s!"
                    )
                    try:
                        self._save_and_push_telemetry(
                            page=page,
                            event_type="warmup_fyp",
                            ch_folder=ch_folder,
                            details={
                                "scrolled_videos": scroll_count,
                                "ads_skipped": ads_skipped,
                                "fyp_liked": fyp_liked_count,
                                "fyp_followed": fyp_followed_count,
                                "elapsed_seconds": round(time.time() - start, 1),
                                "final_url": page.url,
                            },
                        )
                    except Exception:
                        pass
                    return scroll_count > 0
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
        except Exception as exc:
            logger.error(f"[Warmup FYP] [{ch_folder}] Lỗi: {exc}")
            return False
        finally:
            try:
                watchdog_timer.cancel()
            except Exception:
                pass
            try:
                self._cleanup_stale_chrome_profile_locks(user_data_dir)
            except Exception:
                pass
            self._release_browser_process_lock(lock_fh)


    def check_and_run_warmup_browse(self) -> None:
        """
        Tự động mở các kênh lướt FYP bất cứ khi nào Dòng 2 đang rảnh:
        - Chọn kênh lâu nhất chưa lướt (xoay vòng đều cả 10 kênh suốt cả ngày)
        - Giãn cách giữa 2 kênh liên tiếp: `warmup_gap_seconds` (mặc định 180s = 3 phút)
        - Giãn cách tối thiểu cùng 1 kênh: `warmup_channel_cooldown_seconds` (mặc định 1800s = 30 phút)
        - Hỗ trợ `warmup_reset_id` trong `schedule_config.json` để kích hoạt mở kênh lướt ngay lập tức từ xa.
        """
        if platform.machine() != "x86_64":
            return

        from .master_switch_controller import MasterSwitchController

        if not MasterSwitchController(project_root=self.project_root).is_interact_enabled("tiktok"):
            return

        cfg = self.ensure_schedule_config()
        all_filter_channels = cfg.get("active_filter_24h_channels", [])
        if not all_filter_channels:
            return

        browse_seconds = int(cfg.get("warmup_browse_seconds", 150))
        gap_between_channels = int(cfg.get("warmup_gap_seconds", 180))
        per_channel_cooldown = int(cfg.get("warmup_channel_cooldown_seconds", 1800))
        max_per_day = int(cfg.get("warmup_max_per_day", 24))

        # Đọc lịch sử lướt từ file JSON
        history_file = self.profiles_root / ".warmup_browse_history.json"
        history: Dict[str, Any] = {}
        if history_file.exists():
            try:
                history = json.loads(history_file.read_text(encoding="utf-8"))
            except Exception:
                history = {}

        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now.strftime("%Y-%m-%d")

        # Kiểm tra lệnh kích hoạt lướt ngay lập tức từ máy Silicon (warmup_reset_id)
        reset_id = str(cfg.get("warmup_reset_id") or "").strip()
        force_immediate = False
        if reset_id and history.get("_last_reset_id") != reset_id:
            logger.info(
                f"[Warmup FYP] Nhận lệnh kích hoạt lướt video ngay lập tức từ Silicon ({reset_id}) -> Mở kênh tiếp theo ngay!"
            )
            history["_last_reset_id"] = reset_id
            history.pop("_last_any_warmup_ts", None)
            for ch in all_filter_channels:
                if isinstance(history.get(ch), dict):
                    history[ch].pop(f"count_{today_str}", None)
            force_immediate = True
            try:
                history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        # Kiểm tra gap giữa 2 kênh liên tiếp
        if not force_immediate:
            last_any_ts_str = history.get("_last_any_warmup_ts", "")
            if last_any_ts_str:
                try:
                    last_any_ts = datetime.strptime(last_any_ts_str, "%Y-%m-%d %H:%M:%S")
                    elapsed_any = (now - last_any_ts).total_seconds()
                    if 0 <= elapsed_any < gap_between_channels:
                        return
                except Exception:
                    pass

        # Tìm kênh phù hợp: ưu tiên kênh đang có 0 follow trong 24h qua (6 kênh mới) hoặc kênh chưa lướt lâu nhất
        best_ch = None
        best_priority: Tuple[int, datetime] = (999, datetime.max)
        for ch in all_filter_channels:
            ch_data = history.get(ch, {})
            if not isinstance(ch_data, dict):
                ch_data = {}
            today_count = int(ch_data.get(f"count_{today_str}", 0))
            if not force_immediate and today_count >= max_per_day:
                continue
            try:
                recent_f = self.filter_24h._count_recent_channel_follows(ch, hours=24.0)
            except Exception:
                recent_f = 0
            follow_tier = 0 if recent_f == 0 else (1 if recent_f < 2 else 2)
            last_ts_str = str(ch_data.get("last_ts") or "").strip()
            if last_ts_str:
                try:
                    last_ts = datetime.strptime(last_ts_str, "%Y-%m-%d %H:%M:%S")
                    if not force_immediate and (now - last_ts).total_seconds() < per_channel_cooldown:
                        continue
                    cand_prio = (follow_tier, last_ts)
                    if cand_prio < best_priority:
                        best_ch = ch
                        best_priority = cand_prio
                except Exception:
                    cand_prio = (follow_tier, datetime.min)
                    if cand_prio < best_priority:
                        best_ch = ch
                        best_priority = cand_prio
            else:
                cand_prio = (follow_tier, datetime.min)
                if cand_prio < best_priority:
                    best_ch = ch
                    best_priority = cand_prio
                    if follow_tier == 0:
                        break

        if not best_ch:
            return

        prev_any_ts = history.get("_last_any_warmup_ts", "")
        history["_last_any_warmup_ts"] = now_str
        try:
            history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        logger.info(f"[Warmup Auto] Chọn kênh [{best_ch}] để mở TikTok lướt FYP ({browse_seconds}s)...")
        completed = self.warmup_browse_channel(best_ch, browse_seconds=browse_seconds)
        if history_file.exists():
            try:
                history = json.loads(history_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        if not completed:
            if prev_any_ts:
                history["_last_any_warmup_ts"] = prev_any_ts
            else:
                history.pop("_last_any_warmup_ts", None)
            try:
                history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            logger.warning(
                f"[Warmup Auto] Phiên lướt kênh [{best_ch}] chưa hoàn tất trọn vẹn -> Giữ nguyên vị trí ưu tiên để lướt bù ở lượt kế tiếp!"
            )
            return

        # Cập nhật lịch sử sau khi lướt xong trọn vẹn
        finish_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ch_data = history.get(best_ch, {})
        if not isinstance(ch_data, dict):
            ch_data = {}
        ch_data["last_ts"] = finish_str
        ch_data[f"count_{today_str}"] = int(ch_data.get(f"count_{today_str}", 0)) + 1
        history[best_ch] = ch_data
        history["_last_any_warmup_ts"] = finish_str
        try:
            history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        logger.info(
            f"[Warmup Auto] Đã hoàn tất lướt kênh [{best_ch}] -> Nghỉ {gap_between_channels // 60} phút trước khi mở kênh kế tiếp..."
        )

