#!/usr/bin/env python3
"""
CLI Entrypoint cho TikTok & Douyin Automated Dubbing Pipeline.
Cách dùng:
    python main.py --url <TIKTOK_OR_DOUYIN_URL>
"""

import warnings
warnings.filterwarnings("ignore")

from tiktok_pipeline.main import main

if __name__ == "__main__":
    main()
