import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .utils import logger

# Bản đồ mã phím cứng macOS (Virtual KeyCodes từ d-wwei/macos-desktop-control)
MACOS_KEY_CODE_MAP: Dict[str, int] = {
    "return": 36,
    "enter": 36,
    "escape": 53,
    "esc": 53,
    "tab": 48,
    "space": 49,
    "delete": 51,
    "backspace": 51,
    "fwd-delete": 117,
    "arrow-up": 126,
    "arrow-down": 125,
    "arrow-left": 123,
    "arrow-right": 124,
    "home": 115,
    "end": 119,
    "page-up": 116,
    "page-down": 121,
    "capslock": 57,
}

MACOS_CHAR_TO_KEY_CODE: Dict[str, int] = {
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4, "i": 34, "j": 38,
    "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15, "s": 1,
    "t": 17, "u": 32, "v": 9, "w": 13, "x": 7, "y": 16, "z": 6,
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23,
    "6": 22, "7": 26, "8": 28, "9": 25,
    " ": 49, "-": 27, "=": 24, "[": 33, "]": 30, "\\": 42,
    ";": 41, "'": 39, ",": 43, ".": 47, "/": 44, "`": 50,
}

COMPRESSION_PRESETS: Dict[str, Dict[str, Any]] = {
    "none": {"maxWidth": None, "quality": 100, "format": "png"},
    "low": {"maxWidth": 2048, "quality": 85, "format": "jpeg"},
    "medium": {"maxWidth": 1280, "quality": 70, "format": "jpeg"},
    "high": {"maxWidth": 800, "quality": 50, "format": "jpeg"},
}


class MacOSDesktopController:
    """
    Bộ điều khiển máy tính macOS nguyên bản ở tầng Hệ điều hành (tích hợp từ `d-wwei/macos-desktop-control`):
    - Điều khiển chuột, bàn phím, cuộn trang và hộp thoại chọn file của macOS thông qua
      `CoreGraphics` (`CGEventCreateMouseEvent`, `CGEventCreateKeyboardEvent`, `CGEventCreateScrollWheelEvent`,
      `CGWindowListCopyWindowInfo`) và `System Events` ở tầng phần cứng OS (`kCGHIDEventTap` & `CGEventPostToPid`).
    - Giúp quá trình tự động đăng video lên TikTok tạo ra 100% sự kiện phần cứng thật (`isTrusted = true`)
      từ hệ điều hành macOS thay vì sự kiện giả lập của trình duyệt, lách hoàn toàn thuật toán phát hiện
      tự động hóa (`mssdk` / `secsdk`) của TikTok.
    """

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = (
            Path(project_root).resolve()
            if project_root
            else Path(__file__).resolve().parent.parent
        )
        self.mcp_server_path = (
            self.project_root / "integrations" / "macos-desktop-control" / "src" / "index.js"
        )
        self.has_cliclick = shutil.which("cliclick") is not None

    @staticmethod
    def run_osascript(script: str, timeout: int = 20) -> str:
        """Thực thi AppleScript nguyên bản trên macOS."""
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return (res.stdout or "").strip()

    @staticmethod
    def run_jxa(script: str, timeout: int = 20) -> str:
        """Thực thi JavaScript for Automation (JXA) với cầu nối Objective-C `CoreGraphics`."""
        res = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return (res.stdout or "").strip()

    def ensure_app_focus(self, app_name: str = "Google Chrome", delay_sec: float = 0.25) -> None:
        """Đưa cửa sổ ứng dụng lên tiền cảnh (Foreground Focus) như người dùng bấm vào."""
        self.run_osascript(
            f"""
            tell application "{app_name}" to activate
            delay 0.15
            tell application "System Events"
                tell process "{app_name}"
                    set frontmost to true
                    try
                        perform action "AXRaise" of window 1
                    end try
                end tell
            end tell
            """
        )
        if delay_sec > 0:
            time.sleep(delay_sec)

    def list_windows(self) -> List[Dict[str, Any]]:
        """
        Liệt kê toàn bộ cửa sổ đang hiển thị trên màn hình macOS kèm `windowId`, `pid`, `bounds`
        bằng `CGWindowListCopyWindowInfo` (từ `macos-desktop-control`).
        """
        jxa_code = """
        ObjC.import("CoreGraphics");
        var opts = $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements;
        var cfList = $.CGWindowListCopyWindowInfo(opts, 0);
        var bridged = ObjC.castRefToObject(cfList);
        var items = [];
        for (var i = 0; i < bridged.count; i++) {
            var d = bridged.objectAtIndex(i);
            var layer = ObjC.unwrap(d.objectForKey("kCGWindowLayer"));
            if (layer !== 0) continue;
            var app = ObjC.unwrap(d.objectForKey("kCGWindowOwnerName")) || "";
            var wid = ObjC.unwrap(d.objectForKey("kCGWindowNumber"));
            var pid = ObjC.unwrap(d.objectForKey("kCGWindowOwnerPID"));
            var wName = d.objectForKey("kCGWindowName") ? ObjC.unwrap(d.objectForKey("kCGWindowName")) : "";
            var bounds = d.objectForKey("kCGWindowBounds");
            items.push({
                app: app,
                windowId: wid,
                pid: pid,
                name: wName,
                bounds: {
                    x: ObjC.unwrap(bounds.objectForKey("X")),
                    y: ObjC.unwrap(bounds.objectForKey("Y")),
                    w: ObjC.unwrap(bounds.objectForKey("Width")),
                    h: ObjC.unwrap(bounds.objectForKey("Height"))
                }
            });
        }
        JSON.stringify(items);
        """
        raw = self.run_jxa(jxa_code)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return []

    def get_window_info(
        self, app_name: str = "Google Chrome", title_substring: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Lấy thông tin cửa sổ (`windowId`, `pid`, `bounds: {x, y, w, h}`) của ứng dụng mục tiêu."""
        windows = self.list_windows()
        for w in windows:
            if app_name.lower() not in (w.get("app") or "").lower():
                continue
            if title_substring and title_substring.lower() not in (w.get("name") or "").lower():
                continue
            if w.get("bounds", {}).get("w", 0) < 200:
                continue
            return w
        return None

    def get_mouse_position(self) -> Tuple[float, float]:
        """Lấy tọa độ con trỏ chuột hiện tại trên màn hình macOS qua `CoreGraphics`."""
        jxa_code = """
        ObjC.import("CoreGraphics");
        var evt = $.CGEventCreate($());
        var loc = $.CGEventGetLocation(evt);
        JSON.stringify({x: loc.x, y: loc.y});
        """
        raw = self.run_jxa(jxa_code)
        try:
            data = json.loads(raw)
            return float(data["x"]), float(data["y"])
        except Exception:
            return 500.0, 400.0

    def move_mouse_smooth_os(
        self, target_x: float, target_y: float, steps: int = 18
    ) -> None:
        """
        Di chuyển con trỏ chuột thật của hệ điều hành macOS theo đường cong Bezier mềm mại
        sử dụng `CGEventCreateMouseEvent` (`kCGEventMouseMoved = 5`) phát trực tiếp vào `kCGHIDEventTap`.
        """
        start_x, start_y = self.get_mouse_position()
        steps = max(8, steps)
        ctrl_x = (start_x + target_x) / 2.0 + random.uniform(-45.0, 45.0)
        ctrl_y = (start_y + target_y) / 2.0 + random.uniform(-35.0, 35.0)

        points: List[Tuple[int, int]] = []
        for i in range(1, steps + 1):
            t = i / steps
            # Đường cong Quadratic Bezier kèm nhiễu vi mô tay người
            bx = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + (t ** 2) * target_x
            by = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + (t ** 2) * target_y
            points.append((int(round(bx)), int(round(by))))

        pts_json = json.dumps(points)
        jxa_code = f"""
        ObjC.import("CoreGraphics");
        var src = $.CGEventSourceCreate($.kCGEventSourceStateCombinedSessionState);
        var pts = {pts_json};
        for (var i = 0; i < pts.length; i++) {{
            var pt = $.CGPointMake(pts[i][0], pts[i][1]);
            var moveEvt = $.CGEventCreateMouseEvent(src, 5, pt, 0);
            $.CGEventPost($.kCGHIDEventTap, moveEvt);
            delay(0.009);
        }}
        "ok";
        """
        self.run_jxa(jxa_code)

    def fast_bezier_click_os(self, screen_x: float, screen_y: float, clicks: int = 1) -> None:
        """
        Di chuyển chuột phần cứng macOS theo đường cong Bezier (8 bước ~40ms) và click thật (`kCGHIDEventTap`)
        chỉ trong 1 lời gọi JXA duy nhất để vừa không gây lag, vừa tạo sự kiện phần cứng thật (`screenY != clientY`).
        """
        ix, iy = int(round(screen_x)), int(round(screen_y))
        ctrl_dx = round(random.uniform(-28.0, 28.0), 1)
        ctrl_dy = round(random.uniform(-22.0, 22.0), 1)
        jxa_code = f"""
        ObjC.import("CoreGraphics");
        try {{
            var sys = Application("System Events");
            var cProc = sys.processes.byName("Google Chrome");
            if (!cProc.frontmost()) {{
                cProc.frontmost = true;
                delay(0.06);
            }}
        }} catch (e) {{}}
        var src = $.CGEventSourceCreate($.kCGEventSourceStateHIDSystemState);
        var curEvt = $.CGEventCreate($());
        var loc = $.CGEventGetLocation(curEvt);
        var sx = loc.x, sy = loc.y;
        var tx = {ix}, ty = {iy};
        var cx = (sx + tx) / 2.0 + ({ctrl_dx});
        var cy = (sy + ty) / 2.0 + ({ctrl_dy});
        var steps = 8;
        for (var i = 1; i <= steps; i++) {{
            var t = i / steps;
            var bx = Math.round((1 - t) * (1 - t) * sx + 2 * (1 - t) * t * cx + t * t * tx);
            var by = Math.round((1 - t) * (1 - t) * sy + 2 * (1 - t) * t * cy + t * t * ty);
            var mpt = $.CGPointMake(bx, by);
            var mevt = $.CGEventCreateMouseEvent(src, 5, mpt, 0);
            $.CGEventPost($.kCGHIDEventTap, mevt);
            delay(0.0045);
        }}
        var fpt = $.CGPointMake(tx, ty);
        for (var c = 0; c < {int(clicks)}; c++) {{
            var downEvt = $.CGEventCreateMouseEvent(src, 1, fpt, 0);
            $.CGEventSetIntegerValueField(downEvt, 1, c + 1);
            $.CGEventPost($.kCGHIDEventTap, downEvt);
            delay(0.025);
            var upEvt = $.CGEventCreateMouseEvent(src, 2, fpt, 0);
            $.CGEventSetIntegerValueField(upEvt, 1, c + 1);
            $.CGEventPost($.kCGHIDEventTap, upEvt);
            if (c + 1 < {int(clicks)}) delay(0.03);
        }}
        "ok";
        """
        self.run_jxa(jxa_code)

    def click_os(
        self,
        screen_x: float,
        screen_y: float,
        button: str = "left",
        clicks: int = 1,
        smooth_move: bool = True,
        pid: Optional[int] = None,
    ) -> None:
        """
        Thực hiện click chuột thật ở tầng hệ điều hành macOS (`CoreGraphics CGEvent`):
        - Nếu `pid=None` (Foreground): Di chuyển chuột thật trên màn hình và gửi sự kiện `kCGHIDEventTap`.
        - Nếu `pid` được truyền vào (Background Mode của `macos-desktop-control`): Gửi thẳng `CGEventPostToPid(pid)`.
        """
        ix, iy = int(round(screen_x)), int(round(screen_y))
        if pid is None and smooth_move:
            self.move_mouse_smooth_os(ix, iy, steps=random.randint(14, 24))
            time.sleep(random.uniform(0.05, 0.14))

        evt_map = {
            "left": (1, 2, 0),
            "right": (3, 4, 1),
            "middle": (25, 26, 2),
        }
        down_type, up_type, btn_num = evt_map.get(button, (1, 2, 0))
        post_stmt = (
            f"$.CGEventPostToPid({int(pid)}, evt);"
            if pid is not None
            else "$.CGEventPost($.kCGHIDEventTap, evt);"
        )

        jxa_code = f"""
        ObjC.import("CoreGraphics");
        var src = $.CGEventSourceCreate($.kCGEventSourceStateCombinedSessionState);
        var pt = $.CGPointMake({ix}, {iy});
        for (var c = 0; c < {int(clicks)}; c++) {{
            var evt = $.CGEventCreateMouseEvent(src, {down_type}, pt, {btn_num});
            if (c > 0) $.CGEventSetIntegerValueField(evt, 1, c + 1);
            {post_stmt}
            delay(0.035);
            evt = $.CGEventCreateMouseEvent(src, {up_type}, pt, {btn_num});
            if (c > 0) $.CGEventSetIntegerValueField(evt, 1, c + 1);
            {post_stmt}
            delay(0.035);
        }}
        "ok";
        """
        self.run_jxa(jxa_code)

    def scroll_os(
        self,
        dy: int,
        dx: int = 0,
        screen_x: Optional[float] = None,
        screen_y: Optional[float] = None,
        pid: Optional[int] = None,
    ) -> None:
        """Cuộn chuột bằng `CGEventCreateScrollWheelEvent` ở tầng hệ điều hành macOS."""
        if screen_x is not None and screen_y is not None and pid is None:
            self.move_mouse_smooth_os(screen_x, screen_y, steps=12)
            time.sleep(0.08)

        post_stmt = (
            f"$.CGEventPostToPid({int(pid)}, evt);"
            if pid is not None
            else "$.CGEventPost($.kCGHIDEventTap, evt);"
        )
        jxa_code = f"""
        ObjC.import("CoreGraphics");
        var src = $.CGEventSourceCreate($.kCGEventSourceStateCombinedSessionState);
        var evt = $.CGEventCreateScrollWheelEvent(src, 0, 2, {int(dy)}, {int(dx)});
        {post_stmt}
        "ok";
        """
        self.run_jxa(jxa_code)

    def key_press_os(
        self,
        key: str,
        modifiers: Optional[List[str]] = None,
        pid: Optional[int] = None,
    ) -> None:
        """Nhấn phím hoặc tổ hợp phím (`Cmd+A`, `Cmd+V`, `Cmd+Shift+G`, `Return`, `Escape`...) qua `CGEvent`."""
        k_lower = key.lower()
        kc = MACOS_KEY_CODE_MAP.get(k_lower)
        if kc is None and len(k_lower) == 1:
            kc = MACOS_CHAR_TO_KEY_CODE.get(k_lower)
        if kc is None:
            kc = 36

        mod_bits = {"cmd": 1 << 20, "alt": 1 << 19, "ctrl": 1 << 18, "shift": 1 << 17}
        flags = 0
        for m in modifiers or []:
            flags |= mod_bits.get(m.lower(), 0)

        post_stmt = (
            f"$.CGEventPostToPid({int(pid)}, evt);"
            if pid is not None
            else "$.CGEventPost($.kCGHIDEventTap, evt);"
        )
        flag_stmt = f"$.CGEventSetFlags(evt, {flags});" if flags else ""

        jxa_code = f"""
        ObjC.import("CoreGraphics");
        var src = $.CGEventSourceCreate($.kCGEventSourceStateCombinedSessionState);
        var evt = $.CGEventCreateKeyboardEvent(src, {kc}, true);
        {flag_stmt}
        {post_stmt}
        delay(0.025);
        evt = $.CGEventCreateKeyboardEvent(src, {kc}, false);
        {flag_stmt}
        {post_stmt}
        "ok";
        """
        self.run_jxa(jxa_code)

    def paste_unicode_text_os(self, text: str, pid: Optional[int] = None) -> None:
        """
        Nhập văn bản Tiếng Việt Unicode thông qua Clipboard hệ thống (`pbcopy` + `CGEvent Cmd+V`)
        và khôi phục Clipboard cũ (cơ chế `bgTypeText` của `d-wwei/macos-desktop-control`).
        """
        saved_clip = ""
        try:
            saved_clip = subprocess.run(
                ["pbpaste"], capture_output=True, text=True, check=False
            ).stdout
        except Exception:
            pass

        subprocess.run(["pbcopy"], input=text, text=True, check=False)
        time.sleep(0.06)
        self.key_press_os("v", modifiers=["cmd"], pid=pid)
        time.sleep(0.15)

        try:
            subprocess.run(["pbcopy"], input=saved_clip, text=True, check=False)
        except Exception:
            pass

    def type_text_humanly_os(
        self, text: str, pid: Optional[int] = None, chunk_by_word: bool = True
    ) -> None:
        """
        Gõ văn bản Tiếng Việt tự nhiên ở tầng hệ điều hành macOS:
        Chia từng từ/cụm từ để dán và gõ khoảng trắng bằng bàn phím cứng `CGEvent` với nhịp nghỉ tự nhiên.
        """
        words = text.strip().split(" ")
        if not chunk_by_word or len(words) <= 1:
            self.paste_unicode_text_os(text, pid=pid)
            return

        for idx, word in enumerate(words):
            if not word:
                continue
            self.paste_unicode_text_os(word, pid=pid)
            if idx < len(words) - 1:
                time.sleep(random.uniform(0.04, 0.11))
                self.key_press_os("space", pid=pid)
            if any(word.endswith(p) for p in (".", ",", "!", "?", "—", ":")):
                time.sleep(random.uniform(0.14, 0.30))
            else:
                time.sleep(random.uniform(0.05, 0.14))

    def select_file_in_macos_finder_dialog(self, file_path: Path) -> bool:
        """
        Điều khiển hộp thoại chọn file gốc của macOS Finder (`Open File Dialog`):
        1. Nhấn `Cmd + Shift + G` để mở thanh nhập đường dẫn trực tiếp (`Go to Folder`).
        2. Dán đường dẫn tuyệt đối của file `.mp4` và nhấn `Return`.
        3. Nhấn `Return` lần 2 để bấm nút `Open` chọn file đưa vào trình duyệt.
        """
        abs_path = str(Path(file_path).resolve())
        logger.info(f"[macOS-Desktop-Control] Đang chọn file qua hộp thoại Finder gốc của macOS: {abs_path}")
        time.sleep(1.2)
        self.key_press_os("g", modifiers=["cmd", "shift"])
        time.sleep(0.9)
        self.paste_unicode_text_os(abs_path)
        time.sleep(0.6)
        self.key_press_os("return")
        time.sleep(0.9)
        self.key_press_os("return")
        time.sleep(1.2)
        return True

    def capture_window_screenshot(
        self,
        app_name: str = "Google Chrome",
        compression: str = "medium",
        output_path: Optional[Path] = None,
    ) -> Optional[Path]:
        """
        Chụp ảnh màn hình cửa sổ ứng dụng không cần chiếm quyền điều khiển (`screencapture -l<windowId>`)
        kèm nén thông minh bằng `sips` (tính năng v3.1 của `d-wwei/macos-desktop-control`).
        """
        win = self.get_window_info(app_name=app_name)
        raw_png = Path(tempfile.gettempdir()) / f"mcp_win_{int(time.time() * 1000)}.png"
        try:
            if win and win.get("windowId"):
                subprocess.run(
                    ["screencapture", "-x", "-o", f"-l{win['windowId']}", str(raw_png)],
                    check=False,
                    timeout=10,
                )
            else:
                subprocess.run(["screencapture", "-x", str(raw_png)], check=False, timeout=10)

            if not raw_png.exists():
                return None

            preset = COMPRESSION_PRESETS.get(compression, COMPRESSION_PRESETS["medium"])
            if preset["format"] == "png" and not preset["maxWidth"]:
                if output_path:
                    shutil.move(str(raw_png), str(output_path))
                    return output_path
                return raw_png

            ext = "jpg" if preset["format"] == "jpeg" else preset["format"]
            final_path = output_path or raw_png.with_suffix(f".{ext}")
            sips_args = ["sips"]
            if preset["maxWidth"]:
                sips_args.extend(["--resampleWidth", str(preset["maxWidth"])])
            sips_args.extend(
                [
                    "-s",
                    "format",
                    "jpeg" if preset["format"] == "jpeg" else "png",
                    "-s",
                    "formatOptions",
                    str(preset["quality"]),
                    str(raw_png),
                    "--out",
                    str(final_path),
                ]
            )
            subprocess.run(sips_args, capture_output=True, check=False, timeout=10)
            raw_png.unlink(missing_ok=True)
            return final_path if final_path.exists() else None
        except Exception as exc:
            logger.debug(f"Lỗi chụp màn hình cửa sổ {app_name}: {exc}")
            raw_png.unlink(missing_ok=True)
            return None

    def convert_viewport_to_screen_coords(
        self, page: Any, viewport_x: float, viewport_y: float
    ) -> Tuple[float, float]:
        """
        Chuyển đổi tọa độ phần tử trong trang web `(viewport_x, viewport_y)` sang tọa độ tuyệt đối
        trên màn hình macOS `(screen_x, screen_y)` để click bằng chuột phần cứng `CGEvent`.
        """
        try:
            metrics = page.evaluate(
                """() => ({
                    screenX: window.screenX || window.screenLeft || 0,
                    screenY: window.screenY || window.screenTop || 0,
                    outerHeight: window.outerHeight || 860,
                    innerHeight: window.innerHeight || 780,
                    outerWidth: window.outerWidth || 1366,
                    innerWidth: window.innerWidth || 1366
                })"""
            )
            chrome_top_bar = max(0, float(metrics["outerHeight"]) - float(metrics["innerHeight"]))
            chrome_left_border = max(0, (float(metrics["outerWidth"]) - float(metrics["innerWidth"])) / 2.0)
            sx = float(metrics["screenX"]) + chrome_left_border + float(viewport_x)
            sy = float(metrics["screenY"]) + chrome_top_bar + float(viewport_y)
            return sx, sy
        except Exception:
            win = self.get_window_info("Google Chrome")
            if win and "bounds" in win:
                return float(win["bounds"]["x"]) + viewport_x, float(win["bounds"]["y"]) + 82.0 + viewport_y
            return viewport_x, viewport_y + 82.0
