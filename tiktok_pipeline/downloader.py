import json
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests
import yt_dlp
from curl_cffi import requests as creq

from .utils import ensure_ffmpeg, get_media_duration, logger

DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/16.6 Mobile/15E148 Safari/604.1"
)


def generate_s_v_web_id() -> str:
    """Sinh cookie `s_v_web_id` chuẩn cho Douyin/iesdouyin để vượt qua bước kiểm tra trình duyệt."""
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    base36 = ""
    t = int(time.time() * 1000)
    while t > 0:
        base36 = chars[t % 36] + base36
        t //= 36
    r = ["0"] * 36
    r[8] = r[13] = r[18] = r[23] = "_"
    r[14] = "4"
    for i in range(36):
        if r[i] == "0":
            n = int(16 * random.random())
            r[i] = chars[(3 & n) | 8 if i == 19 else n]
    return "verify_" + base36.lower() + "_" + "".join(r)


def fetch_douyin_guest_cookies() -> Dict[str, str]:
    """
    Tự động lấy cặp cookie khách (`ttwid` và `s_v_web_id`) hợp lệ từ ByteDance
    giúp phân giải Douyin API mà không cần người dùng đăng nhập tài khoản.
    """
    cookies = {"s_v_web_id": generate_s_v_web_id()}
    try:
        resp = requests.post(
            "https://ttwid.bytedance.com/ttwid/union/register/",
            json={
                "region": "cn",
                "aid": 1768,
                "needFid": False,
                "service": "www.ixigua.com",
                "migrate_info": {"ticket": "", "source": "node"},
                "cbUrlProtocol": "https",
                "union": True,
            },
            headers={"User-Agent": DESKTOP_UA, "Referer": "https://www.douyin.com/"},
            timeout=10,
        )
        ttwid = resp.cookies.get("ttwid")
        if ttwid:
            cookies["ttwid"] = ttwid
    except Exception as exc:
        logger.debug(f"Không thể lấy ttwid tự động: {exc}")
    return cookies


class VideoDownloader:
    """
    Hỗ trợ tải video gốc không dính watermark từ cả TikTok và Douyin,
    sau đó lưu `raw_video.mp4` và trích xuất `audio.mp3`.
    """

    def __init__(self, work_dir: Path, cookies_file: Optional[str] = None):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_file = cookies_file or os.getenv("COOKIES_FILE") or None
        self.ffmpeg_bin, _ = ensure_ffmpeg()

    @staticmethod
    def extract_url_from_text(raw_input: str) -> str:
        """
        Bóc tách URL sạch từ chuỗi đầu vào (hỗ trợ cả trường hợp người dùng copy
        nguyên đoạn văn bản chia sẻ từ app Douyin/TikTok).
        """
        match = re.search(r"https?://[^\s\"'<>]+", raw_input.strip())
        if not match:
            raise ValueError(f"Không tìm thấy URL hợp lệ trong chuỗi: {raw_input}")
        url = match.group(0).rstrip(".,;!)")
        return url

    @staticmethod
    def detect_platform(url: str) -> str:
        """Xác định nền tảng từ URL ('douyin' hoặc 'tiktok')."""
        host = urlparse(url).netloc.lower()
        if "douyin.com" in host or "iesdouyin.com" in host:
            return "douyin"
        if "tiktok.com" in host:
            return "tiktok"
        raise ValueError(
            f"URL không thuộc TikTok hoặc Douyin ({host}). "
            "Hỗ trợ: tiktok.com, vt.tiktok.com, vm.tiktok.com, douyin.com, v.douyin.com"
        )

    def resolve_short_url(self, url: str, platform: str) -> Tuple[str, Optional[str]]:
        """
        Giải nén link rút gọn (v.douyin.com, vt.tiktok.com, vm.tiktok.com)
        và trích xuất video_id (aweme_id) nếu có.
        """
        video_id = self._extract_video_id(url)
        parsed = urlparse(url)

        is_short = any(
            short_host in parsed.netloc.lower()
            for short_host in ("v.douyin.com", "vt.tiktok.com", "vm.tiktok.com")
        ) or ("/t/" in parsed.path)

        if not is_short and video_id:
            if platform == "douyin":
                return f"https://www.douyin.com/video/{video_id}", video_id
            return url, video_id

        headers = {
            "User-Agent": MOBILE_UA if platform == "douyin" else DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,vi;q=0.7",
        }

        try:
            logger.info(f"Đang phân giải link rút gọn: {url}")
            with requests.Session() as session:
                resp = session.get(
                    url,
                    headers=headers,
                    allow_redirects=True,
                    timeout=15,
                    stream=True,
                )
                resolved_url = resp.url
                resp.close()

            extracted_id = self._extract_video_id(resolved_url) or video_id
            if platform == "douyin" and extracted_id:
                canonical = f"https://www.douyin.com/video/{extracted_id}"
                logger.info(f"Đã giải nén link Douyin -> {canonical} (ID: {extracted_id})")
                return canonical, extracted_id

            logger.info(f"Đã giải nén link -> {resolved_url}")
            return resolved_url, extracted_id
        except requests.RequestException as exc:
            logger.warning(f"Không thể phân giải redirect tự động ({exc}), giữ nguyên URL gốc.")
            return url, video_id

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        """Trích xuất ID video 19 chữ số từ URL TikTok hoặc Douyin."""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        for key in ("modal_id", "aweme_id", "item_ids", "share_item_id"):
            if key in qs and qs[key]:
                candidate = qs[key][0]
                if candidate.isdigit():
                    return candidate

        patterns = [
            r"/(?:video|note|slides|share/video|share/note|share/slides)/(\d{15,22})",
            r"/v/(\d{15,22})\.html",
            r"modal_id=(\d{15,22})",
            r"/(\d{18,20})(?:[/?#]|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def download(self, raw_url: str) -> Dict[str, Path]:
        """
        Tải video không watermark từ TikTok hoặc Douyin:
        - Lưu video gốc vào `<work_dir>/raw_video.mp4`
        - Trích xuất âm thanh vào `<work_dir>/audio.mp3`
        Trả về dict chứa đường dẫn `video_path`, `audio_path`, `platform`, `video_id`, `title`.
        """
        clean_url = self.extract_url_from_text(raw_url)
        platform = self.detect_platform(clean_url)
        resolved_url, video_id = self.resolve_short_url(clean_url, platform)

        raw_video_path = self.work_dir / "raw_video.mp4"
        audio_path = self.work_dir / "audio.mp3"

        if raw_video_path.exists():
            raw_video_path.unlink()
        if audio_path.exists():
            audio_path.unlink()

        title = f"{platform}_{video_id or 'video'}"
        downloaded = False

        # Bước 1: Với Douyin, ưu tiên cơ chế iesdouyin mobile router API (nhanh, 1080p không watermark, không cần cookie đăng nhập)
        if platform == "douyin":
            try:
                logger.info("Đang tải video Douyin không watermark qua iesdouyin mobile router API...")
                title = self._download_douyin_fallback(clean_url, resolved_url, video_id, raw_video_path)
                downloaded = raw_video_path.exists() and raw_video_path.stat().st_size > 10_000
            except Exception as dy_err:
                logger.warning(f"iesdouyin router API gặp lỗi ({dy_err}). Chuyển sang yt-dlp...")

        # Bước 1b: Với TikTok, ưu tiên API không watermark tốc độ cao (TikWM + Universal Rehydration) trước yt-dlp
        if not downloaded and platform == "tiktok":
            try:
                logger.info("Đang tải video TikTok không watermark qua TikWM API...")
                title = self._download_tiktok_fallback(clean_url, resolved_url, raw_video_path)
                downloaded = raw_video_path.exists() and raw_video_path.stat().st_size > 10_000
            except Exception as tt_err:
                logger.warning(f"TikWM API gặp lỗi ({tt_err}). Thử trích xuất trực tiếp từ TikTok Web Rehydration...")
                try:
                    title = self._download_tiktok_universal_data(resolved_url, raw_video_path)
                    downloaded = raw_video_path.exists() and raw_video_path.stat().st_size > 10_000
                except Exception as uni_err:
                    logger.warning(f"TikTok Web Rehydration gặp lỗi ({uni_err}). Chuyển sang yt-dlp...")

        # Bước 2: Dự phòng tải bằng yt-dlp với User-Agent và cấu hình tối ưu
        if not downloaded:
            try:
                title = self._download_with_ytdlp(resolved_url, platform, raw_video_path)
                downloaded = raw_video_path.exists() and raw_video_path.stat().st_size > 10_000
            except Exception as ytdlp_err:
                logger.warning(f"yt-dlp gặp lỗi trên {platform} ({ytdlp_err}).")

        if not downloaded or not raw_video_path.exists():
            raise RuntimeError(
                f"Không thể tải video từ URL: {raw_url}. "
                "Vui lòng kiểm tra lại đường dẫn hoặc kết nối mạng."
            )

        logger.info(
            f"Đã lưu video gốc: {raw_video_path} "
            f"({raw_video_path.stat().st_size / (1024 * 1024):.2f} MB)"
        )

        # Bước 4: Trích xuất file âm thanh audio.mp3 bằng ffmpeg
        self.extract_audio(raw_video_path, audio_path)

        return {
            "video_path": raw_video_path,
            "audio_path": audio_path,
            "platform": platform,
            "video_id": video_id or "unknown",
            "title": title,
            "resolved_url": resolved_url,
        }

    def _download_with_ytdlp(
        self, url: str, platform: str, output_video_path: Path
    ) -> str:
        """Tải video bằng thư viện yt-dlp với User-Agent và headers phù hợp."""
        user_agent = DESKTOP_UA
        referer = "https://www.douyin.com/" if platform == "douyin" else "https://www.tiktok.com/"

        temp_template = str(self.work_dir / "ytdlp_raw.%(ext)s")
        ydl_opts = {
            "outtmpl": temp_template,
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "quiet": False,
            "no_warnings": True,
            "nocheckcertificate": True,
            "ffmpeg_location": str(Path(self.ffmpeg_bin).parent),
            "http_headers": {
                "User-Agent": user_agent,
                "Referer": referer,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,vi;q=0.6",
            },
            " retries": 3,
            "fragment_retries": 3,
        }

        if self.cookies_file and Path(self.cookies_file).is_file():
            ydl_opts["cookiefile"] = str(self.cookies_file)
        elif platform == "douyin":
            guest_cookies = fetch_douyin_guest_cookies()
            cookie_header = "; ".join(f"{k}={v}" for k, v in guest_cookies.items())
            ydl_opts["http_headers"]["Cookie"] = cookie_header

        logger.info(f"Đang tải video bằng yt-dlp từ: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title") or info.get("id") or "video"

        # Tìm file vừa tải về và chuyển thành raw_video.mp4
        candidates = list(self.work_dir.glob("ytdlp_raw.*"))
        if not candidates:
            raise RuntimeError("yt-dlp không tạo ra file đầu ra.")

        downloaded_file = candidates[0]
        if downloaded_file.suffix.lower() == ".mp4":
            shutil.move(str(downloaded_file), str(output_video_path))
        else:
            # Chuyển đổi sang mp4 chuẩn nếu định dạng khác
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-i",
                str(downloaded_file),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-c:a",
                "aac",
                str(output_video_path),
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            downloaded_file.unlink(missing_ok=True)

        return str(title)

    def _download_douyin_fallback(
        self,
        original_url: str,
        resolved_url: str,
        video_id: Optional[str],
        output_video_path: Path,
    ) -> str:
        """
        Giải pháp chuyên biệt cho Douyin khi yt-dlp bị chặn bởi chữ ký __ac_signature:
        Truy cập trang mobile share (iesdouyin.com/share/video/<id>) với Mobile User-Agent,
        bóc tách JSON `window._ROUTER_DATA` và đổi `/playwm/` -> `/play/` để lấy
        link tải trực tiếp MP4 gốc 1080p không dính logo watermark.
        """
        session = requests.Session()
        guest_cookies = fetch_douyin_guest_cookies()
        for domain in (".iesdouyin.com", ".douyin.com"):
            for ck_name, ck_val in guest_cookies.items():
                session.cookies.set(ck_name, ck_val, domain=domain)

        headers = {
            "User-Agent": MOBILE_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        target_share_url = (
            f"https://www.iesdouyin.com/share/video/{video_id}/"
            if video_id
            else original_url
        )

        resp = session.get(target_share_url, headers=headers, allow_redirects=True, timeout=20)
        resp.raise_for_status()
        html = resp.text

        if not video_id:
            video_id = self._extract_video_id(resp.url)
            if video_id and "iesdouyin.com/share/video" not in resp.url:
                target_share_url = f"https://www.iesdouyin.com/share/video/{video_id}/"
                resp = session.get(target_share_url, headers=headers, allow_redirects=True, timeout=20)
                html = resp.text

        router_match = re.search(
            r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>",
            html,
            re.DOTALL,
        )
        if not router_match:
            raise RuntimeError(
                "Không tìm thấy dữ liệu _ROUTER_DATA từ trang chia sẻ Douyin. "
                "Có thể video đã bị xóa hoặc yêu cầu xác thực."
            )

        router_data = json.loads(router_match.group(1))
        loader_data = router_data.get("loaderData", {})

        item_info = None
        for key, val in loader_data.items():
            if isinstance(val, dict) and "videoInfoRes" in val:
                item_list = val["videoInfoRes"].get("item_list") or []
                if item_list:
                    item_info = item_list[0]
                    break

        if not item_info:
            raise RuntimeError("Không tìm thấy item_list trong dữ liệu trả về của Douyin.")

        title = item_info.get("desc") or f"douyin_{video_id or 'video'}"
        video_obj = item_info.get("video") or {}
        play_addr = video_obj.get("play_addr") or {}
        url_list = play_addr.get("url_list") or []
        uri = play_addr.get("uri")

        candidate_urls = []
        for u in url_list:
            # Thay playwm -> play để loại bỏ watermark hoàn toàn
            no_wm_url = u.replace("/playwm/", "/play/")
            candidate_urls.append(no_wm_url)
        if uri:
            candidate_urls.append(
                f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=1080p&line=0"
            )

        if not candidate_urls:
            raise RuntimeError("Không tìm thấy đường dẫn phát video Douyin.")

        dl_headers = {
            "User-Agent": MOBILE_UA,
            "Referer": "https://www.iesdouyin.com/",
        }

        last_err = None
        for media_url in candidate_urls:
            try:
                logger.info(f"Đang tải stream không watermark từ Douyin CDN...")
                with session.get(
                    media_url,
                    headers=dl_headers,
                    stream=True,
                    allow_redirects=True,
                    timeout=30,
                ) as media_resp:
                    media_resp.raise_for_status()
                    with open(output_video_path, "wb") as f:
                        for chunk in media_resp.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)

                if output_video_path.exists() and output_video_path.stat().st_size > 10_000:
                    return str(title)
            except Exception as exc:
                last_err = exc
                continue

        raise RuntimeError(f"Tải stream Douyin thất bại: {last_err}")

    def _download_tiktok_fallback(
        self, original_url: str, resolved_url: str, output_video_path: Path
    ) -> str:
        """Tải video TikTok không watermark qua API công khai tikwm (có retry khi chạm giới hạn 1 req/s)."""
        api_url = "https://www.tikwm.com/api/"
        headers = {
            "User-Agent": DESKTOP_UA,
            "Accept": "application/json",
        }
        payload = {}
        for attempt in range(3):
            resp = requests.post(
                api_url,
                data={"url": original_url, "hd": 1},
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("code") == 0 and payload.get("data"):
                break
            msg = str(payload.get("msg") or "")
            if "Limit" in msg and attempt < 2:
                time.sleep(1.25 * (attempt + 1))
                continue
            break

        if payload.get("code") != 0 or not payload.get("data"):
            raise RuntimeError(f"TikWM fallback trả về lỗi: {payload.get('msg')}")

        data = payload["data"]
        play_url = data.get("hdplay") or data.get("play")
        if not play_url:
            raise RuntimeError("Không tìm thấy link video không logo từ TikWM fallback.")

        if play_url.startswith("/"):
            play_url = f"https://www.tikwm.com{play_url}"

        with requests.get(play_url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(output_video_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)

        return str(data.get("title") or "tiktok_video")

    def _download_tiktok_universal_data(
        self, resolved_url: str, output_video_path: Path
    ) -> str:
        """
        Trích xuất trực tiếp stream không watermark từ `__UNIVERSAL_DATA_FOR_REHYDRATION__`
        của TikTok Web bằng `curl_cffi` (`safari15_5`).
        """
        resp = creq.get(resolved_url, impersonate="safari15_5", timeout=20)
        resp.raise_for_status()
        match = re.search(
            r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
            resp.text,
            re.DOTALL,
        )
        if not match:
            raise RuntimeError("Không tìm thấy __UNIVERSAL_DATA_FOR_REHYDRATION__ trên trang TikTok.")

        data = json.loads(match.group(1))
        detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
        item = detail.get("itemInfo", {}).get("itemStruct") or {}
        if not item:
            raise RuntimeError(f"TikTok không trả về itemStruct (statusMsg={detail.get('statusMsg')}).")

        video_obj = item.get("video") or {}
        play_addr = video_obj.get("playAddr") or video_obj.get("downloadAddr") or ""
        if not play_addr:
            if item.get("dramaInfo"):
                raise RuntimeError(
                    "Video thuộc gói TikTok ShortDrama yêu cầu tài khoản đăng nhập (COOKIES_FILE) để lấy stream."
                )
            raise RuntimeError("TikTok ẩn playAddr đối với video này.")

        dl_headers = {
            "User-Agent": MOBILE_UA,
            "Referer": "https://www.tiktok.com/",
        }
        stream_resp = creq.get(
            play_addr,
            headers=dl_headers,
            cookies=dict(resp.cookies),
            impersonate="safari15_5",
            timeout=60,
        )
        stream_resp.raise_for_status()
        with open(output_video_path, "wb") as f:
            f.write(stream_resp.content)

        return str(item.get("desc") or "tiktok_video")

    def extract_audio(self, video_path: Path, audio_path: Path) -> Path:
        """
        Dùng ffmpeg trích xuất âm thanh từ `raw_video.mp4` ra file `audio.mp3`.
        Sử dụng `pan=mono|c0=0.5*c0+0.5*c1,volume=0.75` để tránh hiện tượng cộng hưởng +3dB
        gây cắt đỉnh (hard-clipping) sóng âm khiến Silero VAD của Whisper bỏ sót câu thoại.
        """
        logger.info(f"Đang trích xuất âm thanh chuẩn hóa không cắt đỉnh ra: {audio_path}")
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-af",
            "pan=mono|c0=0.5*c0+0.5*c1,volume=0.75",
            "-acodec",
            "libmp3lame",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-q:a",
            "2",
            str(audio_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0 or not audio_path.exists():
            raise RuntimeError(
                f"Trích xuất âm thanh thất bại bằng ffmpeg:\n{res.stderr}"
            )

        dur = get_media_duration(audio_path)
        logger.info(f"Trích xuất audio.mp3 hoàn tất (Thời lượng: {dur:.2f}s)")
        return audio_path
