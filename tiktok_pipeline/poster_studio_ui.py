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


class PosterStudioUIMixin:
    """Thao tác giao diện DOM trên TikTok Studio (nhập caption, công tắc kiểm duyệt, bấm nút Đăng)."""

    def _dismiss_all_studio_popovers(self, page: Any, ch_folder: str) -> int:
        """
        Tự động phát hiện và bấm đóng tất cả hộp thoại hướng dẫn / giới thiệu tính năng mới của TikTok Studio
        (ví dụ: 'Bật kiểm tra nội dung tự động?' -> nút 'Bật'/'Hủy', 'Bổ sung tính năng chỉnh sửa mới' -> nút đỏ 'Đã hiểu', 'Got it', 'OK', 'Đóng').
        """
        dismissed = 0
        for _ in range(5):
            try:
                btn_pos = page.evaluate(
                    """() => {
                        // 0. Ưu tiên xử lý hộp thoại TUXModal-overlay ('Bật kiểm tra nội dung tự động?' trên kênh mới)
                        // TUYỆT ĐỐI KHÔNG quét [data-floating-ui-portal] (vì đó là menu dropdown Quyền riêng tư) và KHÔNG bấm 'Hủy' trong hộp thoại xác nhận đăng!
                        const modals = Array.from(document.querySelectorAll('.TUXModal-overlay, [role="dialog"]'));
                        for (const m of modals) {
                            const mTxt = (m.innerText || '').trim();
                            if (
                                !mTxt ||
                                mTxt.includes('Xóa video này') ||
                                mTxt.includes('Xóa bài đăng') ||
                                mTxt.includes('Delete this video') ||
                                mTxt.includes('Đăng ngay') ||
                                mTxt.includes('Tiếp tục đăng') ||
                                mTxt.includes('Post now') ||
                                mTxt.includes('Continue to post') ||
                                mTxt.includes('Quản lý bài đăng') ||
                                mTxt.includes('Tiếp tục chỉnh sửa')
                            ) {
                                continue;
                            }
                            const mBtns = Array.from(m.querySelectorAll('button, [role="button"]'));
                            const prefOrder = ['Bật', 'Turn on', 'Đã hiểu', 'Got it', 'OK', 'Đóng', 'Close'];
                            for (const pLabel of prefOrder) {
                                for (const b of mBtns) {
                                    const bTxt = (b.innerText || b.textContent || '').trim();
                                    if (bTxt === pLabel) {
                                        const r = b.getBoundingClientRect();
                                        if (r.width > 20 && r.height > 14) {
                                            try { b.click(); } catch (e) {}
                                            return {
                                                label: 'Modal:' + bTxt,
                                                x: r.left + r.width / 2,
                                                y: r.top + r.height / 2
                                            };
                                        }
                                    }
                                }
                            }
                        }

                        const targetLabels = [
                            'Đã hiểu', 'Got it', 'Tôi đã hiểu', 'Bỏ qua', 'Skip',
                            'Để sau', 'Not now', 'Đóng', 'Close', 'OK', 'Tiếp theo', 'Hoàn tất',
                            'Bật', 'Turn on'
                        ];
                        const hintKeywords = [
                            'Bổ sung tính năng', 'tính năng chỉnh sửa mới', 'chuyên nghiệp và cuốn hút',
                            'Hướng dẫn', 'Mẹo', 'Tính năng mới',
                            'Bật kiểm tra nội dung tự động', 'Kiểm tra bản quyền nhạc', 'Kiểm tra nội dung nhanh'
                        ];
                        const allEls = Array.from(document.querySelectorAll('button, div[role="button"], span, div'));

                        // 1. Ưu tiên tìm nút 'Đã hiểu' / 'Got it' / 'Bật' (click trực tiếp bằng JS + trả về tọa độ)
                        for (const el of allEls) {
                            if (el.children.length > 2) continue;
                            const txt = (el.innerText || el.textContent || '').trim();
                            if (!targetLabels.includes(txt)) continue;
                            const btn = el.closest('button') || el.closest('[role="button"]') || el;
                            const r = btn.getBoundingClientRect();
                            if (r.width > 24 && r.height > 16 && r.top > 50) {
                                try { btn.click(); } catch (e) {}
                                try { el.click(); } catch (e) {}
                                return {
                                    label: txt,
                                    x: r.left + r.width / 2,
                                    y: Math.min(window.innerHeight - 10, Math.max(10, r.top + Math.min(r.height / 2, 16)))
                                };
                            }
                        }

                        // 2. Nếu có thẻ chứa 'Bổ sung tính năng chỉnh sửa mới' hoặc 'Bật kiểm tra nội dung tự động', tìm mọi nút bên trong nó
                        for (const container of Array.from(document.querySelectorAll('div, section, aside'))) {
                            const cTxt = (container.innerText || '').trim();
                            if (cTxt.length > 900 || !hintKeywords.some(k => cTxt.includes(k))) continue;
                            const btns = Array.from(container.querySelectorAll('button, [role="button"]'));
                            for (const b of btns) {
                                const r = b.getBoundingClientRect();
                                if (r.width > 20 && r.height > 14) {
                                    try { b.click(); } catch (e) {}
                                    return {
                                        label: (b.innerText || 'PopoverButton').trim(),
                                        x: r.left + r.width / 2,
                                        y: Math.min(window.innerHeight - 10, Math.max(10, r.top + 14))
                                    };
                                }
                            }
                        }
                        return null;
                    }"""
                )
                if btn_pos and "x" in btn_pos and "y" in btn_pos:
                    dismissed += 1
                    logger.info(
                        f"[{ch_folder}] [Studio Popover] Đã tự động bấm tắt hộp thoại hướng dẫn: '{btn_pos.get('label')}'"
                    )
                    try:
                        self._os_click_viewport(page, float(btn_pos["x"]), float(btn_pos["y"]))
                    except Exception:
                        pass
                    time.sleep(0.8)
                else:
                    break
            except Exception:
                break
        return dismissed


    def _simulate_human_warmup(self, page: Any, ch_folder: str) -> None:
        """
        Mô phỏng người dùng thật mở trang chủ TikTok, di chuột và lướt xem 2-3 video (12-22 giây)
        kết hợp thực thi 1 lượt "Bộ lọc 24 giờ" (Xem hết video + Thả tim + Sao chép liên kết +
        Thêm yêu thích + Follow + Bình luận) trước khi bấm vào trang Upload để vừa tăng follow chéo,
        vừa tạo điểm tin cậy hành vi (Behavioral Trust Score) cao nhất cho `mssdk`.
        """
        try:
            logger.info(f"[{ch_folder}] [Anti-Detect Warmup] Đang lướt dạo trang chủ TikTok 15s như người dùng thật...")
            page.goto("https://www.tiktok.com/?lang=vi-VN", wait_until="domcontentloaded", timeout=45000)
            time.sleep(random.uniform(3.0, 5.0))
            self._human_move_mouse(page, random.uniform(420, 780), random.uniform(320, 560))

            for _ in range(random.randint(2, 3)):
                page.mouse.wheel(0, random.randint(450, 750))
                time.sleep(random.uniform(3.2, 6.0))
                self._human_move_mouse(page, random.uniform(380, 820), random.uniform(280, 600))

            cfg = self.ensure_schedule_config()
            if cfg.get("filter_24h_enabled", True):
                logger.info(
                    f"[{ch_folder}] [Bộ lọc 24h Warmup] Kết hợp chạy 1 video tương tác chéo 'Bộ lọc 24 giờ' trước khi đăng bài..."
                )
                self.filter_24h.run_session_on_page(page, ch_folder, video_count=1)
        except Exception as exc:
            logger.debug(f"[{ch_folder}] Bỏ qua bước warmup nhẹ: {exc}")


    def _scroll_upload_form(self, page: Any, delta_y: int) -> None:
        """
        Cuộn khung nội dung chính bên phải của trang TikTok Studio Upload (`rect.left > 240`),
        đảm bảo không cuộn nhầm vào thanh menu bên trái (`Bài đăng`).
        """
        try:
            page.mouse.move(680, 500)
            page.mouse.wheel(0, delta_y)
        except Exception:
            pass
        try:
            page.evaluate(
                """(dy) => {
                    window.scrollBy({ top: dy, behavior: 'smooth' });
                    const containers = Array.from(document.querySelectorAll('div, main, section'));
                    for (const el of containers) {
                        const st = window.getComputedStyle(el);
                        if ((st.overflowY === 'auto' || st.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 40) {
                            const r = el.getBoundingClientRect();
                            if (r.left >= 180 && r.width > 400) {
                                el.scrollTop += dy;
                            }
                        }
                    }
                }""",
                delta_y,
            )
        except Exception:
            pass


    def _move_caret_to_end_of_editor(self, page: Any, ensure_trailing_space: bool = False) -> None:
        """
        Đưa con trỏ soạn thảo (caret) về chính xác vị trí cuối cùng của ô `Mô tả` DraftJS trên macOS.
        Tuyệt đối KHÔNG dùng `caption_box.click()` (vì click vào tâm 50%/50% sẽ chèn chữ vào giữa câu đầu)
        và KHÔNG dùng phím `End` (trên macOS phím `End` cuộn trang làm mất focus thay vì đưa trỏ về cuối câu).
        """
        try:
            needs_space = page.evaluate(
                """() => {
                    const editor = document.querySelector(
                        'div.public-DraftEditor-content[contenteditable="true"], ' +
                        'div[contenteditable="true"][role="textbox"], ' +
                        'div[contenteditable="true"]'
                    );
                    if (!editor) return false;
                    editor.scrollIntoView({ block: 'center', inline: 'nearest' });
                    editor.focus();
                    const sel = window.getSelection();
                    if (sel) {
                        const range = document.createRange();
                        range.selectNodeContents(editor);
                        range.collapse(false);
                        sel.removeAllRanges();
                        sel.addRange(range);
                    }
                    const raw = editor.innerText || editor.textContent || '';
                    return raw.length > 0 && !/\\s$/.test(raw.replace(/\\n+$/, ''));
                }"""
            )
            time.sleep(0.08)
            page.keyboard.press("Meta+ArrowDown")
            time.sleep(0.05)
            page.keyboard.press("Meta+ArrowRight")
            if ensure_trailing_space and needs_space:
                time.sleep(0.05)
                page.keyboard.type(" ", delay=40)
        except Exception:
            pass


    def _select_hashtag_suggestion_if_open(self, page: Any, tag_word: str) -> bool:
        """
        Kiểm tra xem menu gợi ý Hashtag thực sự của TikTok Studio có đang mở bên dưới ô `Mô tả` hay không.
        Loại trừ tuyệt đối nút thanh công cụ `# Hashtag` / `@ Nhắc đến`.
        Nếu có mục gợi ý khớp với `#tag_word`, click chọn trực tiếp để tạo thẻ Hashtag Pill chuẩn có thể bấm vào được.
        """
        try:
            pos = page.evaluate(
                """(kw) => {
                    const kwLower = (kw || '').toLowerCase().replace(/^#+/, '');
                    if (!kwLower) return null;
                    const editor = document.querySelector(
                        'div.public-DraftEditor-content[contenteditable="true"], ' +
                        'div[contenteditable="true"][role="textbox"], ' +
                        'div[contenteditable="true"]'
                    );
                    const edRect = editor ? editor.getBoundingClientRect() : { top: 100, bottom: 260, left: 260 };

                    const candidates = Array.from(
                        document.querySelectorAll(
                            '[role="option"], [role="listbox"] li, [role="listbox"] div, ' +
                            '[class*="hashtag" i] li, [class*="hashtag" i] div, ' +
                            '[class*="suggestion" i] li, [class*="suggestion" i] div, ' +
                            '[class*="mention" i] li, [class*="mention" i] div'
                        )
                    );
                    const matches = [];
                    for (const el of candidates) {
                        if (editor && editor.contains(el)) continue;
                        if (el.children.length > 5) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 45 || r.height < 18 || r.height > 72) continue;
                        if (r.top < edRect.top - 10 || r.top > edRect.bottom + 340) continue;

                        const txt = (el.innerText || el.textContent || '').trim();
                        if (!txt) continue;
                        const tLower = txt.toLowerCase();

                        // Loại trừ tuyệt đối nút công cụ '# Hashtag' và '@ Nhắc đến' nằm ngay dưới ô Mô tả
                        if (
                            /^#\\s*hashtag$/i.test(txt) ||
                            /^hashtag$/i.test(txt) ||
                            /^#\\s+/.test(txt) ||
                            tLower.includes('nhắc đến') ||
                            tLower.includes('mention')
                        ) {
                            continue;
                        }

                        const firstToken = tLower.split(/\\s+/)[0].replace(/^#+/, '');
                        if (firstToken === kwLower || firstToken.startsWith(kwLower)) {
                            matches.push({
                                x: r.left + Math.min(90, r.width / 2),
                                y: r.top + r.height / 2,
                                exact: firstToken === kwLower ? 2 : 1,
                                top: r.top,
                                label: txt.slice(0, 50)
                            });
                        }
                    }
                    if (matches.length === 0) return null;
                    matches.sort((a, b) => (b.exact - a.exact) || (a.top - b.top));
                    return matches[0];
                }""",
                tag_word,
            )
            if pos and "x" in pos and "y" in pos:
                page.mouse.click(float(pos["x"]), float(pos["y"]), delay=45)
                time.sleep(0.35)
                return True
        except Exception:
            pass
        return False


    def _clear_and_type_caption_hashtags(
        self,
        page: Any,
        ch_folder: str,
        caption: str,
        hashtags_str: str,
        video_stem: str,
    ) -> None:
        """
        Xóa sạch tên file mặc định (`20261025_152239_tiktok_story_...`) mà TikTok Studio tự điền vào ô `Mô tả`,
        đảm bảo 100% `caption` là mô tả tiếng Việt theo ngữ cảnh, sau đó đưa con trỏ về ĐÚNG CUỐI CÂU
        (`_move_caret_to_end_of_editor`) và gắn từng thẻ `#hashtag` Hot Search tách biệt bằng khoảng trắng
        để tất cả hashtag đều xanh/bấm vào ra danh sách xu hướng trên TikTok.
        """
        if self.crawler.is_raw_filename_caption(caption, video_stem) or not (hashtags_str or "").strip():
            rec_cap, rec_tags, _ = self.crawler.recover_contextual_caption_and_hashtags(
                ch_folder, video_stem
            )
            if self.crawler.is_raw_filename_caption(caption, video_stem):
                caption = rec_cap
            if not (hashtags_str or "").strip():
                hashtags_str = rec_tags

        self._dismiss_all_studio_popovers(page, ch_folder)
        self._scroll_upload_form(page, -1500)
        time.sleep(0.8)

        caption_box = page.locator(
            'div.public-DraftEditor-content[contenteditable="true"], div[contenteditable="true"][role="textbox"], div[contenteditable="true"]'
        ).first
        caption_box.wait_for(state="visible", timeout=25000)
        caption_box.scroll_into_view_if_needed()

        # Đợi TikTok tự điền tên file mặc định vào ô Mô tả rồi tắt mọi popup hướng dẫn
        time.sleep(random.uniform(1.2, 2.0))
        self._dismiss_all_studio_popovers(page, ch_folder)

        box_bounds = caption_box.bounding_box()
        if box_bounds:
            self._human_move_mouse(
                page,
                box_bounds["x"] + box_bounds["width"] * random.uniform(0.3, 0.6),
                box_bounds["y"] + box_bounds["height"] * random.uniform(0.3, 0.7),
            )

        # Xóa sạch tên file mặc định bằng phím tắt macOS chuẩn (Meta+A -> Backspace)
        for attempt in range(6):
            self._dismiss_all_studio_popovers(page, ch_folder)
            try:
                caption_box.click(click_count=3, timeout=3500)
                time.sleep(0.15)
                page.keyboard.press("Backspace")
            except Exception:
                self._dismiss_all_studio_popovers(page, ch_folder)
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.2)
                    caption_box.click(force=True, timeout=3500)
                except Exception:
                    self._move_caret_to_end_of_editor(page, ensure_trailing_space=False)
            time.sleep(0.15)
            page.keyboard.press("Meta+A")
            time.sleep(0.1)
            page.keyboard.press("Backspace")
            time.sleep(0.1)
            page.keyboard.press("Control+A")
            time.sleep(0.1)
            page.keyboard.press("Backspace")

            remaining = ""
            try:
                remaining = (caption_box.inner_text() or "").strip()
            except Exception:
                pass

            if remaining:
                self._move_caret_to_end_of_editor(page, ensure_trailing_space=False)
                for _ in range(min(120, len(remaining) + 18)):
                    page.keyboard.press("Backspace")
                page.keyboard.press("Meta+ArrowUp")
                page.keyboard.press("Meta+ArrowLeft")
                for _ in range(min(60, len(remaining) + 10)):
                    page.keyboard.press("Delete")
                time.sleep(0.2)
                try:
                    remaining = (caption_box.inner_text() or "").strip()
                except Exception:
                    remaining = ""

            if not remaining or (
                video_stem not in remaining
                and "tiktok_story" not in remaining
                and "douyin_story" not in remaining
                and not re.search(r"\b20\d{6}_\d{6}\b", remaining)
            ):
                break
            logger.debug(f"[{ch_folder}] Xóa lại tên file mặc định lần {attempt + 1}: '{remaining}'")

        time.sleep(random.uniform(0.35, 0.6))

        # Gõ Kịch bản mô tả tiếng Việt theo ngữ cảnh vào ô Mô tả đang trống
        clean_caption = caption.strip()
        caption_box.click()
        time.sleep(0.2)
        page.keyboard.insert_text(clean_caption)
        time.sleep(0.25)
        self._move_caret_to_end_of_editor(page, ensure_trailing_space=True)
        time.sleep(random.uniform(0.35, 0.6))

        # Chuẩn hóa danh sách #hashtag
        raw_tokens = [t.strip() for t in hashtags_str.split() if t.strip()]
        tags: List[str] = []
        for t in raw_tokens:
            norm_t = t if t.startswith("#") else f"#{t}"
            if len(norm_t) > 1 and norm_t.lower() not in [x.lower() for x in tags]:
                tags.append(norm_t)
        if not tags:
            tags = ["#xuhuong", "#xuhuongtiktok", "#fyp", "#tiktokvn"]

        # Gõ từng #hashtag ở ĐÚNG CUỐI VĂN BẢN (có dấu cách phía trước '#'):
        # 1. Đưa con trỏ về cuối câu bằng JS Selection + Meta+ArrowDown (TUYỆT ĐỐI không click vào tâm ô làm nhảy trỏ vào giữa chữ!)
        # 2. Gõ '#' + từ khóa để mở gợi ý Hashtag thực của TikTok Studio.
        # 3. Nếu có mục gợi ý khớp từ khóa trong dropdown -> click chọn để gắn thẻ Hashtag Pill.
        # 4. Đưa con trỏ về cuối và gõ dấu cách ' ' để khóa thẻ hashtag và chuẩn bị cho thẻ tiếp theo.
        for tag in tags:
            tag_word = tag.lstrip("#").strip()
            if not tag_word:
                continue
            self._move_caret_to_end_of_editor(page, ensure_trailing_space=True)
            time.sleep(0.1)
            page.keyboard.type("#", delay=55)
            time.sleep(0.22)
            page.keyboard.type(tag_word, delay=random.randint(35, 58))
            time.sleep(random.uniform(1.0, 1.35))

            picked = self._select_hashtag_suggestion_if_open(page, tag_word)
            if not picked:
                self._move_caret_to_end_of_editor(page, ensure_trailing_space=False)
                page.keyboard.type(" ", delay=55)
            else:
                time.sleep(0.25)
                self._move_caret_to_end_of_editor(page, ensure_trailing_space=True)
            time.sleep(random.uniform(0.2, 0.35))

        # Kiểm tra xác nhận nội dung trong ô Mô tả đã có đủ Kịch bản tiếng Việt + tất cả Hashtag tách rời ở cuối câu
        try:
            final_text = (caption_box.inner_text() or "").strip()
            has_broken_midword_tag = bool(re.search(r"[^\s#]#[a-zA-Z0-9_]", final_text))
            tag_count_in_text = len(re.findall(r"(?:^|\s)#[a-zA-Z0-9_]+", final_text))
            if (
                not final_text
                or self.crawler.is_raw_filename_caption(final_text, video_stem)
                or has_broken_midword_tag
                or tag_count_in_text < min(3, len(tags))
            ):
                logger.info(
                    f"[{ch_folder}] Chuẩn hóa lại khối Mô tả + Hashtag ở cuối câu (tag_count={tag_count_in_text}, midword={has_broken_midword_tag})..."
                )
                self._move_caret_to_end_of_editor(page, ensure_trailing_space=False)
                page.keyboard.press("Meta+A")
                time.sleep(0.1)
                page.keyboard.press("Backspace")
                time.sleep(0.15)
                page.keyboard.insert_text(clean_caption + " ")
                time.sleep(0.2)
                for tag in tags:
                    tw = tag.lstrip("#").strip()
                    if not tw:
                        continue
                    self._move_caret_to_end_of_editor(page, ensure_trailing_space=True)
                    page.keyboard.type(f"#{tw}", delay=35)
                    time.sleep(0.85)
                    if not self._select_hashtag_suggestion_if_open(page, tw):
                        self._move_caret_to_end_of_editor(page, ensure_trailing_space=False)
                        page.keyboard.type(" ", delay=45)
                final_text = (caption_box.inner_text() or "").strip()
            logger.info(
                f"[{ch_folder}] Đã nhập xong Mô tả & {len(tags)} Hashtag chuẩn vào TikTok Studio: \"{final_text[:160]}...\""
            )
        except Exception as exc:
            logger.debug(f"[{ch_folder}] Kiểm tra lại văn bản ô Mô tả: {exc}")


    def _click_public_option_in_open_dropdown(
        self, page: Any, exclude_y: Optional[float] = None
    ) -> bool:
        """
        Tìm và click chính xác tùy chọn `Mọi người` (`Everyone` / `Công khai`) nằm bên trong
        menu dropdown Quyền riêng tư đang mở (có chứa đồng thời `Bạn bè` / `Chỉ mình tôi`).
        Đảm bảo không bao giờ click nhầm vào nút trigger hoặc nút của hàng khác, đồng thời
        click đa tầng (OS + Playwright mouse + DOM click) để chắc chắn menu nhận chọn 100%.
        """
        pos = page.evaluate(
            """(exclY) => {
                const targetLabels = ['Mọi người', 'Công khai', 'Everyone', 'Public'];
                const siblingLabels = ['Bạn bè', 'Chỉ mình tôi', 'Chỉ mình', 'Friends', 'Only me', 'Private'];
                const allElements = Array.from(document.querySelectorAll('[role="option"], li, div, span, button, p'));
                const candidates = [];
                for (const el of allElements) {
                    if (el.children.length > 3) continue;
                    const txt = (el.innerText || '').trim();
                    if (!targetLabels.includes(txt)) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 10 || rect.height <= 8) continue;
                    const cy = rect.top + rect.height / 2;
                    if (exclY !== null && exclY !== undefined && Math.abs(cy - exclY) < 14) {
                        continue;
                    }

                    let parent = el.parentElement;
                    let isMenuOption = false;
                    let inPortal = Boolean(el.closest('[data-floating-ui-portal], [role="listbox"], [role="menu"], ul'));
                    for (let depth = 0; depth < 6 && parent; depth++) {
                        const pTxt = (parent.innerText || '').trim();
                        const pRect = parent.getBoundingClientRect();
                        if (
                            siblingLabels.some(s => pTxt.includes(s)) &&
                            pTxt.length < 220 &&
                            pRect.width >= 60 &&
                            pRect.width <= 420 &&
                            pRect.height >= 55 &&
                            pRect.height <= 340
                        ) {
                            isMenuOption = true;
                            break;
                        }
                        parent = parent.parentElement;
                    }
                    if (isMenuOption) {
                        const clickTarget = el.closest('[role="option"], li, [role="menuitem"], button') || el;
                        const cRect = clickTarget.getBoundingClientRect();
                        candidates.push({
                            x: cRect.left + cRect.width / 2,
                            y: cRect.top + cRect.height / 2,
                            inPortal: inPortal ? 1 : 0,
                            area: rect.width * rect.height
                        });
                    }
                }
                if (candidates.length === 0) return null;
                candidates.sort((a, b) => (b.inPortal - a.inPortal) || (a.area - b.area));
                return candidates[0];
            }""",
            exclude_y,
        )
        if pos and "x" in pos and "y" in pos:
            px, py = float(pos["x"]), float(pos["y"])
            self._human_move_mouse(page, px, py)
            time.sleep(0.2)
            try:
                page.mouse.click(px, py)
            except Exception:
                self._os_click_viewport(page, px, py)
            return True
        return False


    def _set_switch_near_label(
        self, page: Any, label_keywords: List[str], desired_on: bool
    ) -> bool:
        """
        Tìm nút gạt (switch/toggle) nằm cùng dòng với nhãn `label_keywords` (ví dụ:
        `Nội dung tải lên chất lượng cao`, `Khai báo nội dung bài đăng`, `Nội dung do AI tạo`,
        `Kiểm tra bản quyền nhạc`, `Kiểm tra nội dung nhanh`) và bật/tắt chính xác theo `desired_on`.
        """
        try:
            for attempt in range(2):
                action = page.evaluate(
                    """({ keywords, wantOn }) => {
                        const els = Array.from(document.querySelectorAll('span, div, label, p, h3, h4'));
                        let targetLabelRect = null;
                        let bestArea = 999999999;
                        for (const el of els) {
                            if (el.children.length > 3) continue;
                            const txt = (el.innerText || el.textContent || '').trim();
                            if (!txt || txt.length > 90) continue;
                            if (keywords.some(k => txt.includes(k))) {
                                const r = el.getBoundingClientRect();
                                const area = r.width * r.height;
                                if (r.width > 10 && r.height > 8 && r.height < 65 && area < bestArea) {
                                    bestArea = area;
                                    targetLabelRect = r;
                                }
                            }
                        }
                        if (!targetLabelRect) return { found: false };

                        const switches = Array.from(
                            document.querySelectorAll('[role="switch"], input[type="checkbox"], button[role="switch"], [class*="Switch"], [class*="switch"], [class*="TuxSwitch"]')
                        );
                        let bestSw = null;
                        let bestDist = 9999;
                        const labelMidY = targetLabelRect.top + targetLabelRect.height / 2;
                        for (const sw of switches) {
                            const r = sw.getBoundingClientRect();
                            if (r.width < 16 || r.width > 95 || r.height < 10 || r.height > 52) continue;
                            const dy = Math.abs((r.top + r.height / 2) - labelMidY);
                            const dx = r.left - targetLabelRect.left;
                            if (dy <= 42 && dx > -10 && dx < 720) {
                                const roleBonus = sw.getAttribute('role') === 'switch' ? -6 : 0;
                                if (dy + roleBonus < bestDist) {
                                    bestDist = dy + roleBonus;
                                    bestSw = { el: sw, rect: r };
                                }
                            }
                        }
                        if (!bestSw) return { found: false };

                        const swEl = bestSw.el;
                        const swContainer = swEl.closest('[role="switch"]') || swEl.querySelector('[role="switch"]') || swEl;
                        const cbInput = swContainer.querySelector('input[type="checkbox"]') || (swEl.tagName === 'INPUT' ? swEl : null);
                        const ariaChecked = swContainer.getAttribute('aria-checked') || swEl.getAttribute('aria-checked');
                        const dataState = swContainer.getAttribute('data-state') || swEl.getAttribute('data-state') || '';

                        let isOn = null;
                        if (ariaChecked === 'true' || (cbInput && cbInput.checked === true) || dataState === 'checked') {
                            isOn = true;
                        } else if (ariaChecked === 'false' || (cbInput && cbInput.checked === false) || dataState === 'unchecked') {
                            isOn = false;
                        }

                        // Kiểm tra vị trí nút tròn (thumb) bên trong switch (bên phải = ON, bên trái = OFF)
                        if (isOn === null) {
                            const children = Array.from(swContainer.querySelectorAll('span, div'));
                            for (const ch of children) {
                                const cr = ch.getBoundingClientRect();
                                if (cr.width >= 8 && cr.width < bestSw.rect.width - 4 && cr.height >= 8 && cr.height <= bestSw.rect.height + 2) {
                                    const thumbCenterX = cr.left + cr.width / 2;
                                    const swCenterX = bestSw.rect.left + bestSw.rect.width / 2;
                                    if (Math.abs(thumbCenterX - swCenterX) > 1.5) {
                                        isOn = thumbCenterX > swCenterX;
                                        break;
                                    }
                                }
                            }
                        }

                        // Kiểm tra màu nền của switch (Màu xanh/đỏ có độ bão hòa cao = ON; màu xám = OFF)
                        if (isOn === null) {
                            const checkEls = [swContainer, swEl, swEl.parentElement].filter(Boolean);
                            for (const ce of checkEls) {
                                const bg = (window.getComputedStyle(ce).backgroundColor || '');
                                const m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
                                if (m) {
                                    const r = parseInt(m[1], 10), g = parseInt(m[2], 10), b = parseInt(m[3], 10);
                                    const sat = Math.max(r, g, b) - Math.min(r, g, b);
                                    if (sat > 25) {
                                        isOn = true;
                                        break;
                                    } else if (sat <= 15 && r > 120 && r < 248) {
                                        isOn = false;
                                    }
                                }
                            }
                        }

                        if (isOn === null) {
                            const cls = ((swContainer.className || '') + ' ' + (swEl.className || '')).toLowerCase();
                            if (cls.includes('unchecked') || cls.includes('disabled')) {
                                isOn = false;
                            } else if (/\b(is-checked|switch-checked|checked)\b/.test(cls)) {
                                isOn = true;
                            } else {
                                isOn = false;
                            }
                        }

                        return {
                            found: true,
                            isOn: Boolean(isOn),
                            needClick: Boolean(isOn) !== Boolean(wantOn),
                            x: bestSw.rect.left + bestSw.rect.width / 2,
                            y: bestSw.rect.top + bestSw.rect.height / 2
                        };
                    }""",
                    {"keywords": label_keywords, "wantOn": desired_on},
                )
                if not action or not action.get("found"):
                    return False
                if not action.get("needClick"):
                    return True

                cx, cy = float(action["x"]), float(action["y"])
                self._human_move_mouse(page, cx, cy)
                time.sleep(random.uniform(0.18, 0.35))
                if attempt == 0:
                    page.mouse.click(cx, cy)
                else:
                    self._os_click_viewport(page, cx, cy)
                time.sleep(random.uniform(0.55, 0.85))
            return True
        except Exception as exc:
            logger.debug(f"Bỏ qua chỉnh switch {label_keywords}: {exc}")
        return False


    @staticmethod
    def detect_video_aigc_disclosure_needed(
        video_path: Path, caption: str = "", hashtags: str = "", ch_folder: str = ""
    ) -> bool:
        """
        Tự động nhận diện từng video trước khi đăng (bất kể ở kênh nào) xem có cần BẬT nút
        'Nội dung do AI tạo' (AIGC Disclosure) hay không:
        - Nếu video thuộc diện mô phỏng nhân vật/bối cảnh AI rõ rệt (baby_AI, video_AI_hay, hoặc chứa
          từ khóa/hashtag #babyai, #hoathinhai, #phimnganai, #aianimation, #aitutien, 'AI ') -> BẬT (True).
        - Nếu là video mẹo vặt đời sống, sức khỏe, tâm lý, kinh doanh, truyện kể Audio không mô phỏng người thật AI -> TẮT (False).
        """
        combined = f"{ch_folder} {video_path.name} {caption} {hashtags}".lower()
        ai_markers = (
            "baby_ai",
            "video_ai_hay",
            "#babyai",
            "#hoathinhai",
            "#phimnganai",
            "#aianimation",
            "#aitutien",
            "#khamphaai",
            "câu chuyện ai",
            "hoạt hình ai",
            "em bé ai",
            "ai 생성",
            "aigc",
        )
        return any(m in combined for m in ai_markers)


    def _configure_upload_settings_public(
        self, page: Any, ch_folder: str, aigc_on: bool = False
    ) -> None:
        """
        Tự động cài đặt toàn bộ mục `Cài đặt` và `Kiểm tra` trên trang Upload của TikTok Studio:
        1. `Thời điểm đăng`: Chọn `Bây giờ`.
        2. `Người có thể nhìn thấy bài đăng này`: Bắt buộc chọn `Mọi người` (Công khai).
        3. `Nội dung tải lên chất lượng cao`: BẬT (`ON`).
        4. `Cho phép người dùng`: Tích chọn `Bình luận`.
        5. `Khai báo nội dung bài đăng`: TẮT (`OFF`).
        6. `Nội dung do AI tạo`: Tự động BẬT (`ON`) nếu `aigc_on=True` (video có AI), ngược lại TẮT (`OFF`).
        7. `Kiểm tra bản quyền nhạc` & `Kiểm tra nội dung nhanh`: BẬT (`ON`).
        """
        logger.info(
            f"[{ch_folder}] Đang tự động cấu hình mục Cài đặt (Công khai = 'Mọi người', Nhãn AIGC = {'BẬT' if aigc_on else 'TẮT'})..."
        )
        try:
            # Cuộn xuống phần Cài đặt trong khung form chính bên phải
            self._scroll_upload_form(page, 480)
            time.sleep(random.uniform(1.0, 1.6))

            # 1. Thời điểm đăng -> Bây giờ
            try:
                now_radio = page.locator('label:has-text("Bây giờ"), span:has-text("Bây giờ")').first
                if now_radio.count() > 0 and now_radio.is_visible():
                    now_radio.click()
                    time.sleep(0.3)
            except Exception:
                pass

            # 2. Người có thể nhìn thấy bài đăng này -> Mọi người (Công khai)
            self._verify_upload_privacy_is_everyone(page, ch_folder)

            # 2.5. Mở rộng "Hiển thị thêm" / "Show more" nếu đang bị thu gọn để hiện đầy đủ cài đặt
            try:
                show_more_loc = page.locator(
                    'div:has-text("Hiển thị thêm"), span:has-text("Hiển thị thêm"), button:has-text("Hiển thị thêm"), span:has-text("Show more")'
                )
                for idx in range(min(show_more_loc.count(), 5)):
                    el = show_more_loc.nth(idx)
                    if el.is_visible():
                        txt = (el.inner_text() or "").strip()
                        if txt in ("Hiển thị thêm", "Show more"):
                            el.click()
                            time.sleep(0.5)
                            break
            except Exception:
                pass

            # 3. Nội dung tải lên chất lượng cao -> BẬT (ON)
            self._set_switch_near_label(
                page, ["Nội dung tải lên chất lượng cao", "High-quality uploads"], desired_on=True
            )

            # 4. Cho phép người dùng -> Đảm bảo "Bình luận" được tích chọn
            try:
                page.evaluate(
                    """() => {
                        const labels = Array.from(document.querySelectorAll('label, span, div'));
                        for (const lb of labels) {
                            if (lb.children.length > 3) continue;
                            const txt = (lb.innerText || '').trim();
                            if (txt === 'Bình luận' || txt === 'Comment' || txt === 'Comments') {
                                const parent = lb.closest('label') || lb.parentElement;
                                if (parent) {
                                    const cb = parent.querySelector('input[type="checkbox"]');
                                    if (cb && !cb.checked) {
                                        parent.click();
                                    }
                                }
                                break;
                            }
                        }
                    }"""
                )
            except Exception:
                pass

            # 5. Khai báo nội dung bài đăng -> TẮT (OFF); Nội dung do AI tạo -> BẬT/TẮT tự động theo từng video (aigc_on)
            self._set_switch_near_label(
                page, ["Khai báo nội dung bài đăng", "Disclose post content"], desired_on=False
            )
            self._set_switch_near_label(
                page, ["Nội dung do AI tạo", "AI-generated content"], desired_on=bool(aigc_on)
            )

            # Kiểm tra lại lần nữa trước khi cuộn xuống để chắc chắn 100% vẫn là 'Mọi người'
            self._verify_upload_privacy_is_everyone(page, ch_folder)

            # Cuộn xuống phần Kiểm tra & nút Đăng
            self._scroll_upload_form(page, 520)
            time.sleep(random.uniform(0.8, 1.3))

            # 6. Kiểm tra bản quyền nhạc & Kiểm tra nội dung nhanh -> BẬT (ON) trước khi đăng
            music_chk = self._set_switch_near_label(
                page, ["Kiểm tra bản quyền nhạc", "Music copyright check"], desired_on=True
            )
            lite_chk = self._set_switch_near_label(
                page,
                [
                    "Kiểm tra nội dung nhanh",
                    "Kiểm tra nội dung và tính tinh gọn",
                    "Content check lite",
                ],
                desired_on=True,
            )
            logger.info(
                f"[{ch_folder}] Đã bật Kiểm tra trước khi đăng: Kiểm tra bản quyền nhạc={music_chk}, Kiểm tra nội dung nhanh={lite_chk}. Đang chờ TikTok hoàn tất kiểm tra nhanh..."
            )
            # Chờ Kiểm tra nội dung nhanh & bản quyền nhạc chạy xong trước khi bấm Đăng (~6-18 giây)
            for _ in range(10):
                time.sleep(1.8)
                try:
                    still_checking = page.evaluate(
                        """() => {
                            const bodyTxt = (document.body && document.body.innerText) || '';
                            return /Đang kiểm tra|Checking in progress|Checking\\.\\.\\./i.test(bodyTxt);
                        }"""
                    )
                    if not still_checking:
                        break
                except Exception:
                    break

            # Kiểm tra kết quả từ "Kiểm tra nội dung nhanh": nếu TikTok Studio cảnh báo vi phạm Quy tắc Cộng đồng / hạn chế đề xuất -> HỦY ĐĂNG NGAY!
            precheck_issue = page.evaluate(
                """() => {
                    const bodyTxt = (document.body && document.body.innerText) || '';
                    const m = bodyTxt.match(/Vi phạm Quy tắc Cộng đồng|Không đủ điều kiện cho bảng tin Dành cho bạn|Phát hiện vấn đề về nội dung|Community Guidelines violation|Ineligible for the For You feed/i);
                    return m ? m[0] : '';
                }"""
            )
            if precheck_issue:
                raise RuntimeError(
                    f"[CG-PRECHECK-BLOCKED] TikTok Studio 'Kiểm tra nội dung nhanh' cảnh báo: '{precheck_issue}' -> Hủy đăng & cách ly video!"
                )

        except Exception as exc:
            if "[CG-PRECHECK-BLOCKED]" in str(exc):
                raise
            logger.warning(f"[{ch_folder}] Lưu ý nhỏ khi chỉnh Cài đặt trang Upload: {exc}")


    def _verify_upload_privacy_is_everyone(self, page: Any, ch_folder: str) -> None:
        """
        Kiểm tra mục `Người có thể nhìn thấy bài đăng này` trên trang Upload:
        - Nếu ĐÃ ở trạng thái `Mọi người` / `Everyone` (Công khai) -> TUYỆT ĐỐI KHÔNG bấm mở menu dropdown
          (tránh để menu xổ xuống đè lên các nút switch phía dưới làm bấm nhầm sang `Chỉ mình tôi`!).
        - Chỉ khi trạng thái hiện tại là `Chỉ mình tôi` / `Bạn bè` thì mới bấm mở dropdown và chọn `Mọi người`.
        """
        try:
            for _attempt in range(2):
                privacy_trigger = page.evaluate(
                    """() => {
                        const labels = ['Người có thể nhìn thấy bài đăng này', 'Ai có thể xem video này', 'Who can view this post'];
                        const allEls = Array.from(document.querySelectorAll('span, div, p, label, h3, h4'));
                        let labelRect = null;
                        for (const el of allEls) {
                            if (el.children.length > 3) continue;
                            const txt = (el.innerText || '').trim();
                            if (labels.some(l => txt.includes(l))) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 20 && r.height > 8) {
                                    labelRect = r;
                                    break;
                                }
                            }
                        }
                        if (!labelRect) return null;

                        const validTexts = [
                            'Mọi người', 'Công khai', 'Bạn bè', 'Chỉ mình tôi', 'Chỉ mình',
                            'Everyone', 'Public', 'Friends', 'Only me', 'Private'
                        ];
                        const candidates = Array.from(document.querySelectorAll('button, [role="combobox"], [role="button"], div'));
                        let best = null;
                        let bestDist = 9999;
                        for (const c of candidates) {
                            if (c.children.length > 5) continue;
                            const txt = (c.innerText || '').trim();
                            if (!validTexts.some(v => txt === v || txt.startsWith(v))) {
                                continue;
                            }
                            const r = c.getBoundingClientRect();
                            if (r.width < 70 || r.width > 520 || r.height < 22 || r.height > 70) continue;
                            const dy = r.top - labelRect.bottom;
                            const dx = Math.abs(r.left - labelRect.left);
                            if (dy >= -10 && dy <= 95 && dx <= 120) {
                                if (dy < bestDist) {
                                    bestDist = dy;
                                    best = {
                                        text: txt,
                                        x: r.left + r.width / 2,
                                        y: r.top + r.height / 2
                                    };
                                }
                            }
                        }
                        return best;
                    }"""
                )
                if not privacy_trigger:
                    return
                cur_priv = str(privacy_trigger.get("text") or "").strip()
                if any(cur_priv.startswith(p) for p in ("Mọi người", "Công khai", "Everyone", "Public")):
                    if _attempt == 0:
                        logger.info(
                            f"[{ch_folder}] 'Người có thể nhìn thấy bài đăng này' đã ở sẵn '{cur_priv}' (Công khai) -> Giữ nguyên an toàn."
                        )
                    return

                logger.info(
                    f"[{ch_folder}] Phát hiện 'Người có thể nhìn thấy bài đăng này' đang là '{cur_priv}' -> Đang chuyển sang 'Mọi người'..."
                )
                tx, ty = float(privacy_trigger["x"]), float(privacy_trigger["y"])
                self._human_move_mouse(page, tx, ty)
                time.sleep(0.25)
                page.mouse.click(tx, ty)
                time.sleep(0.85)
                clicked_pub = self._click_public_option_in_open_dropdown(page, exclude_y=ty)
                if not clicked_pub:
                    page.keyboard.press("Escape")
                time.sleep(0.75)
        except Exception as exc:
            logger.debug(f"[{ch_folder}] Kiểm tra quyền riêng tư trang Upload: {exc}")


    @staticmethod
    def _is_video_upload_100_percent_done(page: Any) -> bool:
        """
        Kiểm tra xem tiến trình tải file video lên TikTok Studio đã đạt 100% chưa
        (không còn hiển thị phần trăm đang tải như `47.12%` hay `Còn ... giây`).
        """
        try:
            uploading_state = page.evaluate(
                """() => {
                    const bodyText = document.body ? (document.body.innerText || '') : '';
                    // Nếu đang hiện phần trăm dạng 47.12% kèm "Còn ... giây" hoặc "MB/"
                    const hasProgressPct = /\\b\\d{1,2}\\.\\d{1,2}%\\b/.test(bodyText);
                    const hasRemainingSec = /Còn\\s+\\d+\\s*(giây|phút)/i.test(bodyText);
                    const isUploadingLabel = /Đang tải lên|Uploading/i.test(bodyText);
                    return hasProgressPct || hasRemainingSec || isUploadingLabel;
                }"""
            )
            return not bool(uploading_state)
        except Exception:
            return True


    def _find_exact_post_button(
        self, page: Any, scroll_into_view: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Tìm CHÍNH XÁC nút đỏ `Đăng` (`button[data-e2e="post_video_button"]` hoặc nút có nhãn đúng bằng `'Đăng'` / `'Post'`)
        nằm ở cuối biểu mẫu Upload (bên phải thanh menu, `rect.left > 220`).
        TUYỆT ĐỐI KHÔNG BAO GIỜ chọn nhầm nút `'Bài đăng'` ở thanh menu bên trái hay nút `'Hủy bỏ'` / `'Thoát'`.
        """
        try:
            return page.evaluate(
                """(doScroll) => {
                    let targetBtn = document.querySelector('button[data-e2e="post_video_button"], [data-e2e="post_video_button"]');
                    if (!targetBtn) {
                        const allBtns = Array.from(document.querySelectorAll('button, [role="button"]'));
                        for (const b of allBtns) {
                            if (b.closest('nav, aside')) continue;
                            const e2e = (b.getAttribute('data-e2e') || '').toLowerCase();
                            if (e2e.includes('cancel') || e2e.includes('discard') || e2e.includes('draft')) continue;
                            const txt = (b.innerText || b.textContent || '').trim();
                            if (txt !== 'Đăng' && txt !== 'Post') continue;
                            const r = b.getBoundingClientRect();
                            if (r.left < 220 || r.width < 50 || r.height < 24) continue;
                            targetBtn = b;
                            break;
                        }
                    }
                    if (!targetBtn) return null;
                    if (doScroll) {
                        try {
                            targetBtn.scrollIntoView({ block: 'center', inline: 'nearest' });
                        } catch (e) {}
                    }
                    const rect = targetBtn.getBoundingClientRect();
                    const disabledAttr = targetBtn.getAttribute('disabled');
                    const ariaDisabled = targetBtn.getAttribute('aria-disabled');
                    const cls = (targetBtn.className || '').toString();
                    const isDisabled = (
                        targetBtn.disabled === true ||
                        disabledAttr !== null ||
                        ariaDisabled === 'true' ||
                        /disabled|loading/i.test(cls)
                    );
                    return {
                        found: rect.width > 20 && rect.height > 15,
                        enabled: !isDisabled,
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        width: rect.width,
                        height: rect.height,
                        text: (targetBtn.innerText || targetBtn.textContent || '').trim(),
                        dataE2e: targetBtn.getAttribute('data-e2e') || ''
                    };
                }""",
                scroll_into_view,
            )
        except Exception:
            return None


    def _click_confirm_modal_if_present(self, page: Any) -> bool:
        """
        Xử lý thông minh hộp thoại popup của TikTok Studio:
        1. Nếu vô tình hiện hộp thoại hỏi 'Bạn có muốn rời khỏi / Hủy bỏ bài đăng?' -> Bấm ngay 'Tiếp tục chỉnh sửa' / 'Ở lại'
           để TUYỆT ĐỐI KHÔNG BAO GIỜ thoát trang khi chưa đăng!
        2. Nếu hiện hộp thoại xác nhận đăng bài ('Đăng ngay', 'Tiếp tục đăng', 'Post now', 'Continue to post') -> Bấm xác nhận đăng!
        """
        try:
            pos = page.evaluate(
                """() => {
                    const stayLabels = [
                        'Tiếp tục chỉnh sửa', 'Ở lại', 'Quay lại', 'Keep editing', 'Stay'
                    ];
                    const confirmPostLabels = [
                        'Đăng ngay', 'Tiếp tục đăng', 'Post now', 'Continue to post'
                    ];
                    const forbiddenExitLabels = [
                        'Thoát', 'Rời khỏi', 'Hủy bỏ', 'Hủy', 'Leave', 'Discard', 'Exit'
                    ];
                    const modals = Array.from(
                        document.querySelectorAll('[role="dialog"], [class*="Modal"], [class*="modal"], [class*="Dialog"], [class*="Popup"]')
                    );
                    if (modals.length === 0) return null;
                    for (const m of modals) {
                        const mRect = m.getBoundingClientRect();
                        if (mRect.width < 100 || mRect.height < 60) continue;
                        const btns = Array.from(m.querySelectorAll('button, [role="button"]'));

                        // Ưu tiên 1: Nếu là hộp thoại cảnh báo thoát/hủy -> bấm 'Tiếp tục chỉnh sửa' / 'Ở lại'
                        for (const b of btns) {
                            const txt = (b.innerText || b.textContent || '').trim();
                            if (stayLabels.some(l => txt === l || txt.includes(l))) {
                                const r = b.getBoundingClientRect();
                                if (r.width > 20 && r.height > 15) {
                                    return { x: r.left + r.width / 2, y: r.top + r.height / 2, text: txt };
                                }
                            }
                        }

                        // Ưu tiên 2: Nếu là hộp thoại xác nhận Đăng ngay / Tiếp tục đăng
                        for (const b of btns) {
                            const txt = (b.innerText || b.textContent || '').trim();
                            if (forbiddenExitLabels.some(f => txt === f || txt.includes(f))) continue;
                            if (confirmPostLabels.some(l => txt === l || txt.includes(l))) {
                                const r = b.getBoundingClientRect();
                                if (r.width > 20 && r.height > 15) {
                                    return { x: r.left + r.width / 2, y: r.top + r.height / 2, text: txt };
                                }
                            }
                        }
                    }
                    return null;
                }"""
            )
            if pos and "x" in pos and "y" in pos:
                logger.info(f"[Modal Handler] Tự động bấm nút hộp thoại: '{pos.get('text')}'")
                self._human_move_mouse(page, float(pos["x"]), float(pos["y"]))
                time.sleep(random.uniform(0.25, 0.5))
                page.mouse.click(float(pos["x"]), float(pos["y"]))
                time.sleep(random.uniform(1.0, 1.8))
                return True
        except Exception:
            pass
        return False

