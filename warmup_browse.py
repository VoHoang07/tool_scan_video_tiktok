#!/usr/bin/env python3
"""
Warm-up: Lướt FYP TikTok cho các kênh mới để TikTok nhận diện account thật.
Mỗi kênh lướt 2-3 phút, xem video tự nhiên, scroll ngẫu nhiên.
"""
import sys
import time
import random
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright
from tiktok_pipeline.auto_poster import TikTokAutoPoster

BROWSE_SECONDS_PER_CHANNEL = 150  # 2.5 phút mỗi kênh
GAP_BETWEEN_CHANNELS = 30  # 30s nghỉ giữa các kênh


def warmup_channel(poster: TikTokAutoPoster, ch_folder: str) -> None:
    user_data_dir = poster.profiles_root / ch_folder
    if not user_data_dir.exists():
        user_data_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"[{ch_folder}] Bắt đầu warm-up lướt FYP...")

    with sync_playwright() as p:
        context = poster._launch_stealth_chrome_context(
            playwright_obj=p, user_data_dir=user_data_dir, headless=False
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()

            # Vào trang chủ FYP
            page.goto("https://www.tiktok.com/foryou", wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(3, 5))

            start = time.time()
            scroll_count = 0
            while time.time() - start < BROWSE_SECONDS_PER_CHANNEL:
                # Xem video hiện tại 5-15 giây
                watch_time = random.uniform(5, 15)
                logger.info(f"  [{ch_folder}] Xem video #{scroll_count + 1} ({watch_time:.0f}s)...")
                time.sleep(watch_time)

                # Đôi khi like (5% xác suất)
                if random.random() < 0.05:
                    try:
                        like_btn = page.locator('[data-e2e="like-icon"]').first
                        if like_btn.is_visible(timeout=1000):
                            like_btn.click()
                            logger.info(f"  [{ch_folder}] ❤️ Liked!")
                            time.sleep(random.uniform(0.5, 1.5))
                    except Exception:
                        pass

                # Scroll xuống video tiếp theo
                page.keyboard.press("ArrowDown")
                scroll_count += 1
                time.sleep(random.uniform(0.8, 2.0))

            elapsed = time.time() - start
            logger.info(f"[{ch_folder}] ✅ Warm-up xong! Đã xem {scroll_count} video trong {elapsed:.0f}s")

        except Exception as e:
            logger.error(f"[{ch_folder}] Lỗi: {e}")
        finally:
            try:
                context.close()
            except Exception:
                pass


def main():
    channels = [
        "meo_vat_gia_dinh",      # K5
        "suc_khoe_duong_sinh",   # K6
        "phu_nu_khi_chat",       # K7
        "an_vat_moi_ngay",       # K8
        "nuoi_con_eq",           # K9
        "phu_kien_tien_ich",     # K10
    ]

    # Cho phép chọn kênh cụ thể qua CLI
    if len(sys.argv) > 1:
        channels = sys.argv[1:]

    poster = TikTokAutoPoster(project_root=PROJECT_ROOT)

    logger.info(f"=== WARM-UP {len(channels)} KÊNH: {', '.join(channels)} ===")

    for i, ch in enumerate(channels):
        logger.info(f"\n{'='*50}")
        logger.info(f"[{i+1}/{len(channels)}] Kênh: {ch}")
        logger.info(f"{'='*50}")

        warmup_channel(poster, ch)

        if i < len(channels) - 1:
            gap = GAP_BETWEEN_CHANNELS + random.uniform(-5, 10)
            logger.info(f"Nghỉ {gap:.0f}s trước kênh tiếp theo...")
            time.sleep(gap)

    logger.info("\n=== HOÀN TẤT WARM-UP TẤT CẢ KÊNH! ===")


if __name__ == "__main__":
    main()
