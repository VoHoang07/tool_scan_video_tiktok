import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("tiktok_pipeline")


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure structured console logging for the pipeline."""
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def ensure_ffmpeg() -> Tuple[str, str]:
    """
    Ensure `ffmpeg` and `ffprobe` are available in PATH or virtual environment.
    If not present on the system PATH, automatically activates `static_ffmpeg`
    and links binaries into `venv/bin` so both subprocess and `yt-dlp` can use them.
    """
    project_root = Path(__file__).resolve().parent.parent
    venv_bin = project_root / "venv" / "bin"
    extra_paths = [
        str(venv_bin),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(Path.home() / ".local" / "bin"),
    ]
    current_path = os.environ.get("PATH", "")
    for p in reversed(extra_paths):
        if p not in current_path.split(os.pathsep) and os.path.isdir(p):
            current_path = f"{p}{os.pathsep}{current_path}"
    os.environ["PATH"] = current_path

    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")

    if not ffmpeg_bin or not ffprobe_bin:
        try:
            import static_ffmpeg

            static_ffmpeg.add_paths()
            ffmpeg_bin = shutil.which("ffmpeg")
            ffprobe_bin = shutil.which("ffprobe")

            # Also create symlinks in venv/bin so direct CLI invocations find ffmpeg
            if venv_bin.is_dir():
                for name, resolved in [("ffmpeg", ffmpeg_bin), ("ffprobe", ffprobe_bin)]:
                    if resolved:
                        target_link = venv_bin / name
                        if not target_link.exists():
                            try:
                                target_link.symlink_to(resolved)
                            except OSError:
                                pass
        except Exception as exc:
            logger.debug(f"Could not initialize static_ffmpeg fallback: {exc}")

    if not ffmpeg_bin:
        raise RuntimeError(
            "Không tìm thấy công cụ hệ thống `ffmpeg`. "
            "Vui lòng cài đặt ffmpeg hoặc chạy `pip install static-ffmpeg` trong venv."
        )
    if not ffprobe_bin:
        ffprobe_bin = ffmpeg_bin

    return ffmpeg_bin, ffprobe_bin


def get_media_duration(file_path: Path) -> float:
    """Return media duration in seconds using ffprobe (or ffmpeg fallback)."""
    ffmpeg_bin, ffprobe_bin = ensure_ffmpeg()
    file_str = str(file_path)

    if ffprobe_bin and "ffprobe" in Path(ffprobe_bin).name:
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            file_str,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            try:
                return max(0.0, float(res.stdout.strip()))
            except ValueError:
                pass

    # Fallback: parse Duration from ffmpeg -i stderr
    cmd = [ffmpeg_bin, "-i", file_str]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    for line in res.stderr.splitlines():
        if "Duration:" in line:
            try:
                dur_part = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = dur_part.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except Exception:
                pass
    return 0.0


def get_video_resolution(file_path: Path) -> Tuple[int, int]:
    """Return (width, height) of a video file."""
    _, ffprobe_bin = ensure_ffmpeg()
    if ffprobe_bin and "ffprobe" in Path(ffprobe_bin).name:
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(file_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and "x" in res.stdout:
            try:
                parts = res.stdout.strip().splitlines()[0].split("x")
                return int(parts[0]), int(parts[1])
            except Exception:
                pass
    return 1080, 1920


def format_srt_timestamp(seconds: float) -> str:
    """Convert seconds (float) to SRT timestamp format HH:MM:SS,mmm."""
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    s = total_sec % 60
    total_min = total_sec // 60
    m = total_min % 60
    h = total_min // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_ass_timestamp(seconds: float) -> str:
    """Convert seconds (float) to ASS timestamp format H:MM:SS.cc."""
    seconds = max(0.0, seconds)
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_sec = total_cs // 100
    s = total_sec % 60
    total_min = total_sec // 60
    m = total_min % 60
    h = total_min // 60
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"
