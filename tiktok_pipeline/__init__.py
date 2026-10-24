"""
TikTok & Douyin Automated Video Dubbing & Subtitle Pipeline.
Modules:
- downloader: Tải video không logo từ TikTok & Douyin, tách audio.mp3 và raw_video.mp4
- transcriber: Nhận diện giọng nói & ngôn ngữ gốc (zh/en/...) bằng faster-whisper
- translator: Dịch thuật phụ đề sang tiếng Việt tự nhiên qua LLM API (OpenAI/Gemini/DeepSeek)
- tts_engine: Tổng hợp giọng đọc tiếng Việt bằng edge-tts và khớp tốc độ theo timestamp
- composer: Ghép giọng lồng tiếng, giảm âm gốc (ducking) và burn phụ đề vào video bằng ffmpeg
"""

import warnings
warnings.filterwarnings("ignore", message=".*NotOpenSSLWarning.*")
try:
    from urllib3.exceptions import NotOpenSSLWarning
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except Exception:
    pass

__version__ = "1.0.0"
