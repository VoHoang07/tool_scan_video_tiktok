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


class PosterCleanupMixin:
    """Quản lý trang tiktokstudio/content: xóa bài trùng/vi phạm và mở công khai bài viết."""

    def _ensure_posts_public_in_content_page(self, page: Any, ch_folder: str) -> int:
        """
        BƯỚC SETTING QUAN TRỌNG SAU KHI BẤM ĐĂNG (Ảnh chụp màn hình 5 - `tiktok.com/tiktokstudio/content`):
        1. Chuyển tới trang quản lý `https://www.tiktok.com/tiktokstudio/content` (mục `Bài đăng`).
        2. Tìm tất cả các bài đăng có cột `Quyền riêng tư` đang bị để ở `Chỉ mình tôi` / `Chỉ mình ...` / `Bạn bè`
           (kể cả bài vừa mới đăng và các bài đăng trước đó vừa xét duyệt xong).
        3. Bấm vào nút dropdown `Chỉ mình ...` ở từng bài và chọn `Mọi người` (Công khai).
        4. Trả về tổng số bài đăng đã được chuyển sang `Mọi người` (Công khai).
        """
        switched_count = 0
        try:
            if "tiktokstudio/content" not in page.url:
                logger.info(
                    f"[{ch_folder}] Đang mở trang Quản lý Bài đăng (tiktok.com/tiktokstudio/content) để kiểm tra & bật Công khai ('Mọi người')..."
                )
                page.goto(
                    "https://www.tiktok.com/tiktokstudio/content",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )

            for pass_idx in range(3):
                time.sleep(random.uniform(4.5, 6.5))

                # Tìm tất cả nút Quyền riêng tư đang là "Chỉ mình...", "Chỉ mình tôi", "Bạn bè", "Only me", "Private"
                # Khử trùng lặp theo từng dòng (Math.abs(d.y - item.y) < 30) để mỗi hàng chỉ bấm đúng 1 lần!
                private_buttons = page.evaluate(
                    """() => {
                        const privatePrefixes = ['Chỉ mình', 'Bạn bè', 'Only me', 'Private', 'Friends'];
                        const allEls = Array.from(
                            document.querySelectorAll('button, [role="button"], [role="combobox"], div, span')
                        );
                        const raw = [];
                        for (const el of allEls) {
                            if (el.children.length > 4) continue;
                            const txt = (el.innerText || '').trim();
                            if (!txt || txt.length > 26) continue;
                            if (!privatePrefixes.some(p => txt.startsWith(p) || txt === p)) continue;
                            const targetEl = el.closest('button, [role="button"], [role="combobox"]') || el;
                            const rect = targetEl.getBoundingClientRect();
                            // Các hàng bài đăng trong bảng nằm bên dưới thanh công cụ lọc (rect.top > 205)
                            if (rect.width < 45 || rect.width > 210 || rect.height < 18 || rect.height > 60 || rect.top < 205) {
                                continue;
                            }
                            raw.push({
                                text: txt,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                top: rect.top
                            });
                        }
                        raw.sort((a, b) => a.top - b.top);
                        const deduped = [];
                        for (const item of raw) {
                            if (!deduped.some(d => Math.abs(d.y - item.y) < 30)) {
                                deduped.push(item);
                            }
                        }
                        return deduped;
                    }"""
                )

                if not private_buttons:
                    if pass_idx == 0:
                        logger.info(
                            f"[{ch_folder}] Tất cả bài đăng trên trang Quản lý đều đã ở trạng thái 'Mọi người' (Công khai)!"
                        )
                    break

                logger.info(
                    f"[{ch_folder}] [Lượt {pass_idx + 1}] Phát hiện {len(private_buttons)} bài đăng đang ở trạng thái riêng tư ({[b['text'] for b in private_buttons]}). Đang chuyển sang 'Mọi người' (Công khai)..."
                )

                for btn in private_buttons:
                    bx, by = float(btn["x"]), float(btn["y"])
                    self._human_move_mouse(page, bx, by)
                    time.sleep(0.2)
                    try:
                        page.mouse.click(bx, by)
                    except Exception:
                        self._os_click_viewport(page, bx, by)
                    time.sleep(random.uniform(0.95, 1.4))

                    opened_pub = self._click_public_option_in_open_dropdown(page, exclude_y=by)
                    if not opened_pub:
                        try:
                            self._os_click_viewport(page, bx, by)
                            time.sleep(0.95)
                            opened_pub = self._click_public_option_in_open_dropdown(page, exclude_y=by)
                        except Exception:
                            pass

                    if opened_pub:
                        switched_count += 1
                        logger.info(
                            f"[{ch_folder}] -> Đã chọn 'Mọi người' (Công khai) cho bài đăng (trước đó: '{btn['text']}')."
                        )
                        time.sleep(random.uniform(1.2, 2.0))
                        self._click_confirm_modal_if_present(page)
                    else:
                        page.keyboard.press("Escape")
                        time.sleep(0.5)

                # Đợi vài giây rồi tải lại bảng Bài đăng để kiểm tra xác nhận đã chuyển sang 'Mọi người'
                if pass_idx < 2:
                    time.sleep(random.uniform(3.5, 5.0))
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=35000)
                    except Exception:
                        pass

            # Nếu là kênh audio_dem_khuyen: tự động quét danh sách bài đang còn sống trên trang tiktokstudio/content
            # để phát hiện thêm video nào bị xóa/mất trên kênh và đưa vào danh sách khôi phục từ từ
            if ch_folder == "audio_dem_khuyen":
                try:
                    from .bongcuc_splitter import make_bongcuc_story_id, parse_bongcuc_ep_and_part

                    row_texts = page.evaluate(
                        """() => {
                            const els = Array.from(document.querySelectorAll('div, span, a, p'));
                            const out = [];
                            for (const el of els) {
                                if (el.children.length > 4) continue;
                                const t = (el.innerText || '').trim();
                                if (t.length >= 8 && t.length <= 260 && (t.includes('Phần') || t.includes('#phan') || t.includes('Số'))) {
                                    out.push(t);
                                }
                            }
                            return out.slice(0, 200);
                        }"""
                    )
                    live_sids: Set[str] = set()
                    for rt in row_texts or []:
                        ep_pt = parse_bongcuc_ep_and_part(str(rt))
                        if ep_pt:
                            live_sids.add(make_bongcuc_story_id(ep_pt[0], ep_pt[1]))
                    if len(live_sids) >= 3:
                        inv_path = self.project_root / "data" / "audio_dem_khuyen_live_posts.json"
                        inv_path.parent.mkdir(parents=True, exist_ok=True)
                        inv_data: Dict[str, Any] = {}
                        if inv_path.exists():
                            try:
                                inv_data = json.loads(inv_path.read_text(encoding="utf-8"))
                            except Exception:
                                inv_data = {}
                        merged_live = sorted(set(inv_data.get("live_story_ids") or []) | live_sids)
                        inv_data["live_story_ids"] = merged_live
                        inv_data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        inv_path.write_text(json.dumps(inv_data, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass

        except Exception as exc:
            logger.warning(f"[{ch_folder}] Lưu ý khi chuyển quyền riêng tư tại tiktokstudio/content: {exc}")

        return switched_count


    def _deduplicate_existing_posts_in_content_page(self, page: Any, ch_folder: str) -> List[str]:
        """
        Tự động quét bảng `tiktok.com/tiktokstudio/content` để phát hiện các bài đăng bị trùng lặp
        (cùng thời lượng `MM:SS` và cùng nội dung/caption hoặc bản trùng re-upload)
        và tự động bấm `...` -> `Xóa` -> `Xác nhận` để làm sạch kênh.
        """
        deleted_dups: List[str] = []
        if "tiktokstudio/content" not in (page.url or ""):
            return deleted_dups

        for _pass in range(4):
            rows = page.evaluate(
                """() => {
                    const norm = (s) => (s || '').normalize('NFC').trim();
                    const privacyPrefixes = ['Mọi người', 'Chỉ mình', 'Bạn bè', 'Everyone', 'Only me', 'Private', 'Friends'];
                    const isDateText = (s) => /^\\d{1,2}\\s+tháng\\s+\\d{1,2}/i.test(s) || /^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+\\d{1,2}/i.test(s) || /^Đã xuất bản|^Đã lên lịch|^Nội dung đang được xét duyệt/i.test(s);
                    const allEls = Array.from(document.querySelectorAll('button, [role="button"], [role="combobox"], div, span, a, p'));
                    const privacyBtns = [];
                    for (const el of allEls) {
                        if (el.children.length > 4) continue;
                        const txt = norm(el.innerText || el.textContent || '');
                        if (!txt || txt.length > 26) continue;
                        if (!privacyPrefixes.some(p => txt.startsWith(p) || txt === p)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 45 || r.width > 210 || r.height < 18 || r.height > 60 || r.top < 185) continue;
                        const cy = r.top + r.height / 2;
                        if (!privacyBtns.some(b => Math.abs(b.y - cy) < 28)) {
                            privacyBtns.push({ y: cy, top: r.top, left: r.left, right: r.right });
                        }
                    }
                    privacyBtns.sort((a, b) => a.top - b.top);
                    const svgs = Array.from(document.querySelectorAll('svg'));
                    const textNodes = Array.from(document.querySelectorAll('*'));
                    const out = [];
                    for (const pb of privacyBtns) {
                        const rowY = pb.y;
                        let dur = '';
                        let views = 0;
                        const capParts = [];
                        for (const el of textNodes) {
                            if (el.children.length > 2) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width < 6 || r.height < 8 || r.height > 110) continue;
                            const cy = r.top + r.height / 2;
                            if (Math.abs(cy - rowY) > 54) continue;
                            const t = norm(el.innerText || el.textContent || '').replace(/\\s+/g, ' ');
                            if (!t) continue;
                            if (r.left >= 50 && r.left < pb.left - 15 && /^\\d{2}:\\d{2}$/.test(t)) {
                                dur = t;
                            } else if (r.left >= 70 && r.left < pb.left - 15 && t.length >= 12 && t.length <= 240 && !isDateText(t)) {
                                if (!capParts.includes(t)) capParts.push(t);
                            } else if (r.left >= pb.right + 4 && r.left <= pb.right + 160 && /^\\d+$/.test(t)) {
                                views = parseInt(t, 10) || 0;
                            }
                        }
                        const actionCandidates = [];
                        for (const svg of svgs) {
                            const r = svg.getBoundingClientRect();
                            if (r.left < pb.right + 75 || r.left > 1480 || r.width < 8 || r.width > 48 || r.height < 8 || r.height > 48) continue;
                            const cy = r.top + r.height / 2;
                            if (Math.abs(cy - rowY) > 38) continue;
                            const btn = svg.closest('button, [role="button"]') || svg.parentElement || svg;
                            const br = btn.getBoundingClientRect();
                            actionCandidates.push({ x: br.left + br.width / 2, y: br.top + br.height / 2, left: br.left });
                        }
                        actionCandidates.sort((a, b) => b.x - a.x);
                        if (actionCandidates.length > 0) {
                            out.push({
                                rowY: Math.round(rowY),
                                duration: dur,
                                views: views,
                                caption: capParts.join(' | ').slice(0, 260),
                                moreX: Math.round(actionCandidates[0].x * 10) / 10,
                                moreY: Math.round(actionCandidates[0].y * 10) / 10
                            });
                        }
                    }
                    return out;
                }"""
            )
            if not rows or len(rows) < 2:
                break

            dup_row = None
            generic_markers = (
                "thói quen nhỏ mỗi ngày giúp cơ thể khỏe",
                "tín hiệu cảnh báo sớm của cơ thể",
                "bí quyết dưỡng sinh đơn giản tại nhà",
                "sức khỏe chủ động",
                "chăm sóc sức khỏe gia đình",
                "bài học tâm lý hôn nhân sâu sắc giúp giữ lửa",
                "phụ nữ thông minh luôn nắm vững nguyên tắc",
                "bản lĩnh và khí chất của người phụ nữ hiện đại",
                "phụ nữ khí chất",
                "tâm lý hôn nhân",
                "công thức món ngon siêu dễ làm tại nhà",
                "món ăn vặt quốc dân gây nghiện",
                "bí quyết nấu ăn nhanh gọn đậm đà",
                "thiên đường ăn vặt",
                "món ngon dễ làm mỗi ngày",
                "phim ngắn hoạt hình ai siêu cuốn",
                "thế giới ai kỳ ảo với màn biến hình",
                "đừng rời mắt khỏi màn hình",
                "nhật ký gia đình siêu lầy",
                "tình huống dở khóc dở cười",
                "siêu phẩm huyền huyễn tu tiên",
                "thú cưng thành tinh và màn phiêu lưu",
                "lòng người khó đoán chỉ đến khi biến cố",
                "cú quay xe nghẹt thở ở phút cuối",
                "biến cố bất ngờ lúc nửa đêm và sự thật",
                "góc khuất tình cảm và màn đáp trả",
            )
            if ch_folder == "baby_AI":
                for r_item in rows:
                    c_low = (r_item.get("caption") or "").strip().lower()
                    if "ảnh live của iphone" in c_low or "chọn ảnh live" in c_low:
                        dup_row = r_item
                        break

            for i in range(len(rows)):
                if dup_row:
                    break
                for j in range(i + 1, len(rows)):
                    ri, rj = rows[i], rows[j]
                    di, dj = (ri.get("duration") or "").strip(), (rj.get("duration") or "").strip()
                    if not di or di != dj:
                        continue
                    ci = (ri.get("caption") or "").strip().lower()
                    cj = (rj.get("caption") or "").strip().lower()
                    ci_is_gen = any(gm in ci for gm in generic_markers)
                    cj_is_gen = any(gm in cj for gm in generic_markers)
                    same_cap = bool(ci and cj and len(ci) >= 24 and ci[:42] == cj[:42])
                    # BẮT BUỘC phải cùng thời lượng MM:SS VÀ cùng caption cụ thể (không xóa các video khác nhau chỉ vì chung mẫu caption đầu câu)
                    if same_cap and not ci_is_gen and not cj_is_gen:
                        cand = ri if ri.get("views", 0) <= rj.get("views", 0) else rj
                        if int(cand.get("views", 0)) <= 2:
                            dup_row = cand
                            break
                if dup_row:
                    break

            if not dup_row:
                break

            logger.info(
                f"[{ch_folder}] [Anti-Duplicate Content Page] Phát hiện bài đăng trùng lặp trên kênh "
                f"(thời lượng={dup_row.get('duration')}, views={dup_row.get('views')}, caption='{dup_row.get('caption')[:60]}') -> Đang xóa..."
            )
            mx, my = float(dup_row["moreX"]), float(dup_row["moreY"])
            self._os_click_viewport(page, mx, my)
            time.sleep(1.3)
            del_opt = page.evaluate(
                """() => {
                    const norm = (s) => (s || '').normalize('NFC').trim().toLowerCase();
                    for (const el of document.querySelectorAll('*')) {
                        if (el.children.length > 3) continue;
                        const t = norm(el.innerText || el.textContent || '');
                        if (t === 'xóa' || t === 'xoá' || t === 'xóa video' || t === 'xoá video' || t === 'delete' || t === 'delete video') {
                            const r = el.getBoundingClientRect();
                            if (r.width >= 15 && r.width <= 300 && r.height >= 12 && r.height <= 75 && r.top > 80) {
                                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                            }
                        }
                    }
                    return null;
                }"""
            )
            if not del_opt:
                page.mouse.click(mx, my)
                time.sleep(1.2)
                del_opt = page.evaluate(
                    """() => {
                        const norm = (s) => (s || '').normalize('NFC').trim().toLowerCase();
                        for (const el of document.querySelectorAll('*')) {
                            if (el.children.length > 3) continue;
                            const t = norm(el.innerText || el.textContent || '');
                            if (t === 'xóa' || t === 'xoá' || t === 'xóa video' || t === 'xoá video' || t === 'delete' || t === 'delete video') {
                                const r = el.getBoundingClientRect();
                                if (r.width >= 15 && r.width <= 300 && r.height >= 12 && r.height <= 75 && r.top > 80) {
                                    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                                }
                            }
                        }
                        return null;
                    }"""
                )
            if not del_opt:
                break

            dx, dy = float(del_opt["x"]), float(del_opt["y"])
            self._os_click_viewport(page, dx, dy)
            page.mouse.click(dx, dy)
            time.sleep(1.6)
            conf_btn = page.evaluate(
                """() => {
                    const norm = (s) => (s || '').normalize('NFC').trim().toLowerCase();
                    const labels = ['xóa', 'xoá', 'xóa video', 'xoá video', 'delete', 'xác nhận', 'confirm'];
                    for (const b of document.querySelectorAll('button, [role="button"], div, span')) {
                        if (b.children.length > 3) continue;
                        const t = norm(b.innerText || b.textContent || '');
                        if (!labels.includes(t)) continue;
                        const r = b.getBoundingClientRect();
                        if (r.left >= 180 && r.left <= 1050 && r.top >= 150 && r.width >= 36 && r.width <= 420 && r.height >= 22 && r.height <= 75) {
                            return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                        }
                    }
                    return null;
                }"""
            )
            if conf_btn:
                cx, cy = float(conf_btn["x"]), float(conf_btn["y"])
                self._os_click_viewport(page, cx, cy)
                page.mouse.click(cx, cy)
                try:
                    page.evaluate(
                        """({x, y}) => {
                            const el = document.elementFromPoint(x, y);
                            if (el) {
                                const btn = el.closest('button, [role="button"]') || el;
                                btn.click();
                            }
                        }""",
                        {"x": cx, "y": cy},
                    )
                except Exception:
                    pass
                time.sleep(4.0)
                deleted_dups.append(str(dup_row.get("caption") or dup_row.get("duration")))
                logger.info(f"[{ch_folder}] Đã tự động xóa 1 bài đăng trùng trên kênh: {dup_row.get('caption')[:60]}")
            else:
                page.keyboard.press("Escape")
                break

        return deleted_dups


    def _delete_post_in_content_page(
        self,
        page: Any,
        ch_folder: str,
        match_keywords: Optional[List[str]] = None,
        delete_top_if_no_match: bool = False,
    ) -> List[str]:
        """
        Tại trang `https://www.tiktok.com/tiktokstudio/content`, tìm tất cả bài đăng khớp `match_keywords`
        (hoặc bài mới nhất trên cùng nếu `delete_top_if_no_match=True`), bấm nút `...` ở cột `Hành động`
        -> chọn `Xóa` / `Xoá video` (`Delete`) -> xác nhận `Xóa` trong hộp thoại modal.
        """
        deleted_captions: List[str] = []
        try:
            if "tiktokstudio/content" not in (page.url or ""):
                logger.info(
                    f"[{ch_folder}] Đang mở trang Quản lý Bài đăng (tiktok.com/tiktokstudio/content) để kiểm tra và xóa video lỗi..."
                )
                page.goto(
                    "https://www.tiktok.com/tiktokstudio/content",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
            time.sleep(random.uniform(5.2, 6.8))
            self._dismiss_all_studio_popovers(page, ch_folder)

            kw_lower = [k.strip().lower() for k in (match_keywords or []) if k.strip()]
            self._last_delete_debug: List[Any] = []

            for pass_idx in range(4):
                rows_info = page.evaluate(
                    """() => {
                        const norm = (s) => (s || '').normalize('NFC').trim();
                        const privacyPrefixes = [
                            'Mọi người', 'Chỉ mình', 'Bạn bè', 'Everyone', 'Only me', 'Private', 'Friends',
                            'Vi phạm', 'Không đủ điều kiện', 'Hạn chế', 'Đã xóa', 'Ineligible', 'Violation'
                        ];
                        const allEls = Array.from(document.querySelectorAll('button, [role="button"], [role="combobox"], div, span, a, p'));
                        const privacyBtns = [];
                        for (const el of allEls) {
                            if (el.children.length > 4) continue;
                            const txt = norm(el.innerText || el.textContent || '');
                            if (!txt || txt.length > 42) continue;
                            if (!privacyPrefixes.some(p => txt.startsWith(p) || txt === p)) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width < 45 || r.width > 260 || r.height < 16 || r.height > 65 || r.top < 205) continue;
                            const cy = r.top + r.height / 2;
                            if (!privacyBtns.some(b => Math.abs(b.y - cy) < 28)) {
                                privacyBtns.push({ y: cy, top: r.top });
                            }
                        }
                        privacyBtns.sort((a, b) => a.top - b.top);

                        const rows = [];
                        const svgs = Array.from(document.querySelectorAll('svg'));
                        const textNodes = Array.from(document.querySelectorAll('*'));
                        for (const pb of privacyBtns) {
                            const rowY = pb.y;
                            // Gom toàn bộ text mô tả + thời lượng + giờ đăng ở nửa bên trái cùng hàng (r.left từ 80..750)
                            const capParts = [];
                            for (const el of textNodes) {
                                if (el.children.length > 3) continue;
                                const r = el.getBoundingClientRect();
                                if (r.left < 80 || r.left > 750 || r.width < 12 || r.height < 8 || r.height > 120) continue;
                                const cy = r.top + r.height / 2;
                                if (Math.abs(cy - rowY) > 54) continue;
                                const t = norm(el.innerText || el.textContent || '').replace(/\\s+/g, ' ');
                                if (t && t.length <= 240 && !capParts.includes(t)) {
                                    capParts.push(t);
                                }
                            }
                            const bestCaption = capParts.join(' | ').slice(0, 420);

                            // Tìm chính xác icon SVG của nút '...' (nút thứ 4 ngoài cùng bên phải ở cột Hành động, r.left > 1050)
                            const actionCandidates = [];
                            for (const svg of svgs) {
                                const r = svg.getBoundingClientRect();
                                if (r.left < 1050 || r.left > 1420 || r.width < 8 || r.width > 48 || r.height < 8 || r.height > 48) continue;
                                const cy = r.top + r.height / 2;
                                if (Math.abs(cy - rowY) > 36) continue;
                                const btn = svg.closest('button, [role="button"]') || svg.parentElement || svg;
                                const br = btn.getBoundingClientRect();
                                actionCandidates.push({
                                    x: br.left + br.width / 2,
                                    y: br.top + br.height / 2,
                                    left: br.left
                                });
                            }
                            actionCandidates.sort((a, b) => b.x - a.x);
                            if (actionCandidates.length > 0) {
                                rows.push({
                                    rowY: Math.round(rowY),
                                    caption: bestCaption,
                                    moreX: Math.round(actionCandidates[0].x * 10) / 10,
                                    moreY: Math.round(actionCandidates[0].y * 10) / 10,
                                    actionCount: actionCandidates.length
                                });
                            }
                        }
                        return rows;
                    }"""
                )

                logger.info(
                    f"[{ch_folder}] [Pass {pass_idx + 1}] Tìm thấy {len(rows_info or [])} hàng bài đăng trên tiktokstudio/content: {rows_info}"
                )
                if not rows_info:
                    break

                target_row = None
                for r in rows_info:
                    cap_low = (r.get("caption") or "").lower()
                    if kw_lower and any(k in cap_low for k in kw_lower):
                        target_row = r
                        break
                if target_row is None and delete_top_if_no_match and pass_idx == 0:
                    target_row = rows_info[0]

                if not target_row:
                    self._last_delete_debug.append({"pass": pass_idx, "rows": rows_info, "msg": "no_matching_row"})
                    logger.info(f"[{ch_folder}] Không còn bài đăng nào khớp từ khóa cần xóa ({match_keywords}).")
                    break

                mx, my = float(target_row["moreX"]), float(target_row["moreY"])
                logger.info(
                    f"[{ch_folder}] Đang mở menu '...' tại ({mx:.1f}, {my:.1f}) để xóa bài: '{target_row.get('caption')}'"
                )

                find_del_js = """() => {
                    const norm = (s) => (s || '').normalize('NFC').trim().toLowerCase();
                    const els = Array.from(document.querySelectorAll('*'));
                    const matches = [];
                    const popupDump = [];
                    for (const el of els) {
                        if (el.children.length > 4) continue;
                        const txt = norm(el.innerText || el.textContent || '');
                        if (!txt || txt.length > 35) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 12 || r.height < 10 || r.top < 80) continue;
                        if (r.left > 950 && popupDump.length < 25) {
                            popupDump.push({ t: txt, x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) });
                        }
                        if (
                            txt === 'xóa' || txt === 'xoá' ||
                            txt === 'xóa video' || txt === 'xoá video' ||
                            txt === 'xóa bài đăng' || txt === 'xoá bài đăng' ||
                            txt === 'delete' || txt === 'delete video' || txt === 'delete post' ||
                            ((txt.includes('xóa') || txt.includes('xoá') || txt.includes('delete')) && !txt.includes('tìm kiếm'))
                        ) {
                            if (r.width <= 320 && r.height <= 80) {
                                matches.push({
                                    text: txt,
                                    x: r.left + r.width / 2,
                                    y: r.top + r.height / 2,
                                    left: r.left,
                                    childrenCount: el.children.length
                                });
                            }
                        }
                    }
                    matches.sort((a, b) => (a.childrenCount - b.childrenCount) || (b.left - a.left));
                    return { match: matches[0] || null, popupDump: popupDump };
                }"""

                # Bước A: Click 1 lần chuẩn xác bằng OS click vào đúng tâm icon SVG '...'
                self._os_click_viewport(page, mx, my)
                time.sleep(1.4)
                res_del = page.evaluate(find_del_js)
                del_option = res_del.get("match") if isinstance(res_del, dict) else None

                # Bước B: Nếu chưa mở menu, thử hover + Playwright mouse click 1 lần
                if not del_option:
                    page.mouse.move(mx, my)
                    time.sleep(0.8)
                    res_del = page.evaluate(find_del_js)
                    del_option = res_del.get("match") if isinstance(res_del, dict) else None

                if not del_option:
                    page.mouse.click(mx, my)
                    time.sleep(1.3)
                    res_del = page.evaluate(find_del_js)
                    del_option = res_del.get("match") if isinstance(res_del, dict) else None

                self._last_delete_debug.append({
                    "pass": pass_idx,
                    "target_row": target_row,
                    "del_option": del_option,
                    "popup_dump": res_del.get("popupDump") if isinstance(res_del, dict) else [],
                })

                if not del_option:
                    logger.warning(f"[{ch_folder}] Không tìm thấy nút 'Xóa' trong menu tại ({mx:.1f}, {my:.1f}).")
                    break

                dx, dy = float(del_option["x"]), float(del_option["y"])
                logger.info(
                    f"[{ch_folder}] Đã thấy tùy chọn '{del_option['text']}' tại ({dx:.1f}, {dy:.1f}), đang bấm..."
                )
                find_confirm_js = """() => {
                    const norm = (s) => (s || '').normalize('NFC').trim().toLowerCase();
                    const labels = ['xóa', 'xoá', 'xóa video', 'xoá video', 'delete', 'xác nhận', 'confirm'];
                    const btns = Array.from(document.querySelectorAll('button, [role="button"], div, span'));
                    const candidates = [];
                    for (const b of btns) {
                        if (b.children.length > 3) continue;
                        const txt = norm(b.innerText || b.textContent || '');
                        if (!labels.includes(txt)) continue;
                        const r = b.getBoundingClientRect();
                        if (r.width < 36 || r.width > 280 || r.height < 22 || r.height > 75 || r.top < 160) continue;
                        // Ưu tiên nút trong hộp thoại Modal ở giữa màn hình (r.left < 1050), không trùng menu '...' bên phải
                        const isModalCenter = (r.left >= 250 && r.left <= 1050) ? 1 : 0;
                        const isButtonTag = (b.tagName === 'BUTTON' || b.getAttribute('role') === 'button') ? 1 : 0;
                        candidates.push({
                            text: txt,
                            x: r.left + r.width / 2,
                            y: r.top + r.height / 2,
                            left: r.left,
                            top: r.top,
                            isModalCenter: isModalCenter,
                            isButtonTag: isButtonTag
                        });
                    }
                    candidates.sort((a, b) => (b.isModalCenter - a.isModalCenter) || (b.isButtonTag - a.isButtonTag) || (b.top - a.top));
                    return candidates.find(c => c.isModalCenter === 1) || null;
                }"""

                self._os_click_viewport(page, dx, dy)
                time.sleep(1.8)
                confirm_btn = page.evaluate(find_confirm_js)
                if not confirm_btn:
                    page.mouse.click(dx, dy)
                    page.evaluate(
                        """({x, y}) => {
                            const el = document.elementFromPoint(x, y);
                            if (el) el.click();
                        }""",
                        {"x": dx, "y": dy},
                    )
                    time.sleep(1.6)
                    confirm_btn = page.evaluate(find_confirm_js)

                if confirm_btn:
                    cx, cy = float(confirm_btn["x"]), float(confirm_btn["y"])
                    logger.info(
                        f"[{ch_folder}] Đang bấm xác nhận XÓA ('{confirm_btn['text']}') trong modal tại ({cx:.1f}, {cy:.1f})..."
                    )
                    self._os_click_viewport(page, cx, cy)
                    page.mouse.click(cx, cy)
                    try:
                        page.evaluate(
                            """({x, y}) => {
                                const el = document.elementFromPoint(x, y);
                                if (el) {
                                    const btn = el.closest('button, [role="button"]') || el;
                                    btn.click();
                                }
                            }""",
                            {"x": cx, "y": cy},
                        )
                    except Exception:
                        pass
                    time.sleep(4.2)
                    deleted_captions.append(target_row.get("caption") or "deleted_post")
                    logger.info(f"[{ch_folder}] ĐÃ XÓA THÀNH CÔNG bài đăng lỗi: '{target_row.get('caption')}'!")
                else:
                    logger.warning(f"[{ch_folder}] Không thấy nút xác nhận Xóa trong modal.")
                    page.keyboard.press("Escape")
                    time.sleep(1.0)
                    break
        except Exception as exc:
            logger.warning(f"[{ch_folder}] Lỗi khi xóa bài đăng tại tiktokstudio/content: {exc}")

        return deleted_captions


    def delete_channel_posts_by_keywords(
        self,
        channel_arg: str,
        match_keywords: List[str],
        headless: bool = False,
    ) -> List[str]:
        """
        Mở trực tiếp trang `https://www.tiktok.com/tiktokstudio/content` của kênh `channel_arg`,
        xóa toàn bộ các bài đăng lỗi khớp `match_keywords` và đồng bộ ảnh chụp màn hình về `remote_telemetry/`.
        """
        from playwright.sync_api import sync_playwright

        profile = resolve_channel_profile(channel_arg)
        ch_folder = profile["folder_name"]
        user_data_dir = self.profiles_root / ch_folder
        if not user_data_dir.exists():
            logger.warning(f"Kênh [{ch_folder}] chưa có profile đăng nhập tại {user_data_dir}.")
            return []

        deleted: List[str] = []
        with sync_playwright() as p:
            context = self._launch_stealth_chrome_context(
                playwright_obj=p, user_data_dir=user_data_dir, headless=headless
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                clean_kws = [k for k in (match_keywords or []) if k != "__auto_dedup_only__"]
                if clean_kws:
                    deleted.extend(
                        self._delete_post_in_content_page(
                            page=page,
                            ch_folder=ch_folder,
                            match_keywords=clean_kws,
                            delete_top_if_no_match=False,
                        )
                    )
                else:
                    page.goto(
                        "https://www.tiktok.com/tiktokstudio/content",
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                    time.sleep(random.uniform(5.2, 6.8))
                    self._dismiss_all_studio_popovers(page, ch_folder)

                dedup_deleted = self._deduplicate_existing_posts_in_content_page(page, ch_folder)
                if dedup_deleted:
                    deleted.extend(dedup_deleted)
                self._ensure_posts_public_in_content_page(page, ch_folder)
                self._save_and_push_telemetry(
                    page=page,
                    event_type="upload",
                    ch_folder=ch_folder,
                    details={
                        "action": "delete_bad_posts",
                        "match_keywords": match_keywords,
                        "deleted_posts": deleted,
                        "delete_debug": getattr(self, "_last_delete_debug", []),
                        "final_url": page.url,
                    },
                )
                time.sleep(random.uniform(3.0, 5.0))
            finally:
                try:
                    context.close()
                except Exception:
                    pass
        return deleted


    def ensure_channel_posts_public(self, channel_arg: str, headless: bool = False) -> Dict[str, Any]:
        """
        Mở trang `https://www.tiktok.com/tiktokstudio/content` của một kênh (hoặc cả 4 kênh nếu `channel_arg='all'`)
        và tự động chuyển toàn bộ các video đang bị để `Chỉ mình tôi` / `Bạn bè` sang `Mọi người` (Công khai).
        """
        if str(channel_arg).strip().lower() == "all":
            summary: Dict[str, Any] = {}
            for profile in CHANNEL_PROFILES.values():
                ch_folder = profile["folder_name"]
                summary[ch_folder] = self.ensure_channel_posts_public(ch_folder, headless=headless)
            return summary

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài đặt `playwright`. Vui lòng chạy: `./venv/bin/pip install playwright && ./venv/bin/playwright install chromium`"
            ) from exc

        profile = resolve_channel_profile(channel_arg)
        ch_folder = profile["folder_name"]
        user_data_dir = self.profiles_root / ch_folder
        if not user_data_dir.exists():
            logger.warning(f"Kênh [{ch_folder}] chưa đăng nhập tại {user_data_dir}.")
            return {"channel": ch_folder, "switched_to_public": 0, "status": "not_logged_in"}

        with sync_playwright() as p:
            context = self._launch_stealth_chrome_context(
                playwright_obj=p, user_data_dir=user_data_dir, headless=headless
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                switched = self._ensure_posts_public_in_content_page(page, ch_folder)
                self._save_and_push_telemetry(
                    page=page,
                    event_type="upload",
                    ch_folder=ch_folder,
                    details={
                        "action": "ensure_posts_public",
                        "switched_public_count": switched,
                        "final_url": page.url,
                    },
                )
                time.sleep(random.uniform(2.0, 3.5))
                return {"channel": ch_folder, "switched_to_public": switched, "status": "ok"}
            finally:
                try:
                    context.close()
                except Exception:
                    pass

