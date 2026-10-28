import csv
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import requests
from curl_cffi import requests as creq

from .downloader import DESKTOP_UA, MOBILE_UA, VideoDownloader, fetch_douyin_guest_cookies
from .transcriber import AudioTranscriber
from .translator import SubtitleTranslator, has_vietnamese_diacritics
from .utils import ensure_ffmpeg, get_media_duration, logger

# Danh sách secUid đã xác thực của các kênh mẫu TikTok Việt Nam để lấy toàn bộ video trong kênh nhanh & ổn định
KNOWN_TIKTOK_SEC_UIDS: Dict[str, str] = {
    "tieutin2000": "MS4wLjABAAAA-Y_-TBoSi0NUFHqpwFuyTPQt39asURmANBKFqyTCadKT-3M-jodB5MjzzbdTYGzX",
    "bongcucaudio04": "MS4wLjABAAAAqSs8aGN8ETXmcCXpTo7AZiIonC-dik61DFmCCCx66xgx2NX_ZOvxxvMo4mvPgvN9",
    "gamukbangaudio": "MS4wLjABAAAAPt-iDeD6kthXgmvw0VkX1-MgxNpCiufkg76FrDaV7X32bCJbknhURTwN7jnu85vd",
    "soo.bin.0": "MS4wLjABAAAAQh7XRDDEot4ykqX8F1t_xyk9TtP5YpW-D9rn62AZQsrj-A1SV2pFYxd_bRYWXrgN",
    "kimvuongaudio": "MS4wLjABAAAA4s5GyBKAnFNMKQTJrhnxi02DP_u2kd7ZenxE0SNTkU9w55Y2451cVC8PWqAdTaeB",
    "funny.gau": "MS4wLjABAAAAnW9ZCoNMK0ic5TlzeWu_kIBGZNb8_z3gJMQ0PtjqIbc5KzvVKr5QGX8pwbCWAoQg",
}

KNOWN_VIETNAMESE_HANDLES = {
    "tieutin2000",
    "bongcucaudio04",
    "gamukbangaudio",
    "soo.bin.0",
    "kimvuongaudio",
    "funny.gau",
}

# Ngưỡng lượt xem (Views) và lượt thích (Likes) tối thiểu để chỉ lấy video viral view cao (Trung Quốc & Việt Nam)
MIN_HIGH_VIEW_PLAY_COUNT_TIKTOK = 100_000
MIN_HIGH_VIEW_LIKES_TIKTOK = 5_000
MIN_HIGH_VIEW_LIKES_DOUYIN = 10_000

# ==============================================================================
# CẤU HÌNH 4 PROFILE KÊNH TIKTOK CHUYÊN BIỆT (CHỈ LẤY VIDEO TRUNG QUỐC & VIỆT NAM VIEW CAO)
# ==============================================================================
CHANNEL_PROFILES: Dict[str, Dict[str, Any]] = {
    "channel_1_ai_animation": {
        "channel_id": "1",
        "profile_key": "channel_1_ai_animation",
        "folder_name": "video_AI_hay",
        "description": "AI Hoạt họa, Thú cưng thành tinh, Phim ngắn AI chữa lành (Trung - Việt view cao)",
        "seed_video_urls": [
            "https://v.douyin.com/Mr4JFtYJ-lg/",
            "https://v.douyin.com/KhnOwGH99QQ/",
            "https://www.douyin.com/video/7686077455564988992",  # 努力的喵喵酱 - 快乐小青蛙之山丁子大作战
            "https://www.douyin.com/video/7691766718599295080",  # 努力的喵喵酱 - 快乐小青蛙之小青蛇惹大祸
            "https://www.douyin.com/video/7665616691048598799",  # 一瞬好长 - Ai剧丨《地海》第一集
            "https://www.douyin.com/video/7684239577346739456",  # 一瞬好长 - Ai剧丨《地海》第十九集
            "https://www.douyin.com/video/7697440750308429098",  # 一瞬好长 - Ai剧丨《地海》第二十集
            "https://www.douyin.com/video/7698223650570276134",  # 一瞬好长 - Ai剧丨《地海》第二十一集
            "https://v.douyin.com/5PXz62pKn0s/",
            "https://v.douyin.com/RrMNQy7NBa0/",
        ],
        "seed_channels": [],
        "douyin_keywords": [
            "AI动画短片",
            "萌宠成精",
            "动物大逃亡",
            "克苏鲁AI",
            "AI短剧",
            "治愈系动漫",
        ],
        "tiktok_keywords": [
            "#hoathinhai",
            "#phimnganai",
            "#ai动画",
            "#ai短剧",
            "#ai萌宠",
            "#ai动漫",
        ],
        "tiktok_challenge_ids": [
            "7215824929186004998",  # #hoathinhai (110M views VN)
            "7329916616232140801",  # #phimnganai (23M views VN)
            "7171274051875242010",  # #ai动画 (159M views CN)
            "7348490384151937025",  # #ai短剧 (30.7M views CN)
            "7463480141252034582",  # #ai萌宠 (94.3M views CN)
            "7167589236522024986",  # #ai动漫 (21M views CN)
        ],
        "douyin_challenge_ids": [
            "1625956003624974",  # #地海 (Trọn bộ tập phim ngắn AI 《地海》 của kênh 一瞬好长)
            "1674607865397325",  # #萌宠出道计划 (Kênh @李梦 - 臭可可的洗澡日)
            "1684216673933320",  # #ai动漫 (Kênh 努力的喵喵酱 - 快乐小青蛙)
            "1682091371105293",  # #AI短剧 (Phim ngắn AI)
            "1606571520286788",  # #克苏鲁 (Kịch bản AI kỳ ảo)
            "1551203555277826",  # #治愈系 (Kênh 黄灰红 - 小咪重阳回村看奶奶)
            "1562727459956738",  # #猫咪 (Mèo AI chữa lành)
            "7481095278095763519",  # #AI动画 / #AI动画短片
            "1767586789604366",  # #动漫解说 / #治愈系动漫
            "1592197408664647",  # #有声漫画 / #AI短剧
            "1578523164433422",  # #治愈 / #治愈系动漫
        ],
    },
    "channel_2_life_comedy": {
        "channel_id": "2",
        "profile_key": "channel_2_life_comedy",
        "folder_name": "baby_AI",
        "description": "Em bé AI hài hước (AI萌娃 / 大头菠萝), Mầm non lém lỉnh & Gia đình vui nhộn (Trung - Việt view cao)",
        "seed_video_urls": [
            "https://www.douyin.com/video/7638714267462561417",  # #大头菠萝 #一人带娃日常 - 小孩怎么这么喜欢吃泡面呀
            "https://www.douyin.com/video/7634246231678587978",  # #大头菠萝 #一人带娃日常 - 巨婴宝宝上学又迟到了
            "https://www.douyin.com/video/7653148698738335945",  # #大头菠萝 - 巨婴宝宝的烤肠差一点被小老鼠偷走
            "https://www.douyin.com/video/7640576010862205938",  # #大头菠萝 - 迷你妈妈为了摘野果差一点被螳螂抓住吃掉
            "https://www.douyin.com/video/7649090399869777051",  # #大头菠萝 - 迷你妈妈差一点被癞蛤蟆吃掉
            "https://www.douyin.com/video/7659099729062903625",  # #大头菠萝 - 巨婴宝宝和迷你妈妈抢电视遥控器
            "https://www.tiktok.com/@andy76243/video/7668708297821670663",  # #AI萌娃 - 男人为什么要做家务？听听小胖怎么说 (287k views)
            "https://www.tiktok.com/@andy76243/video/7664467357850488071",  # #AI萌娃 - 这结尾万万没想到啊 (240k views)
            "https://www.tiktok.com/@andy76243/video/7668157404000931079",  # #语出惊人的熊孩子 #萌娃搞笑日常 (407k views)
            "https://www.douyin.com/video/7254524593694608651",  # #人类幼崽 #萌娃 - 忘了喝孟婆汤了？
            "https://www.douyin.com/video/7212859447947889959",  # #懂事的孩子 #萌娃 - 就剩最后两块肉了，宝宝的做法太让妈妈感动了
        ],
        "seed_channels": [],
        "douyin_keywords": [
            "大头菠萝",
            "AI萌娃",
            "巨婴宝宝",
            "萌娃搞笑日常",
            "人类幼崽",
            "熊孩子搞笑日常",
            "一人带娃日常",
        ],
        "tiktok_keywords": [
            "#ai萌娃",
            "#萌娃搞笑日常",
            "#人类幼崽",
            "#语出惊人的熊孩子",
            "#人类幼崽有多可爱",
            "#embehaihuoc",
            "#embecute",
            "#embedangyeu",
        ],
        "tiktok_challenge_ids": [
            "7236712553568976902",  # #ai萌娃 (16.4M views CN AI Baby)
            "1678587005908993",  # #萌娃搞笑日常 (518M views CN Baby Comedy)
            "1682665615870977",  # #人类幼崽 (1.6B views CN Cute Kids)
            "7242345830019072005",  # #语出惊人的熊孩子 (375M views CN Funny Kid Dialogue)
            "7035311318714449925",  # #人类幼崽有多可爱 (17.9M views CN)
            "1577251025398814",  # #骗你生女儿 (789M views CN)
            "1634934865901569",  # #embehaihuoc (4.86B views VN)
            "1589552538562561",  # #embecute (1.13B views VN)
            "1601303421157377",  # #embedangyeu (49.8B views VN)
        ],
        "douyin_challenge_ids": [
            "1631527180822535",  # #大头菠萝 (Series AI Baby bé Dứa / 迷你妈妈 巨婴宝宝)
            "1619206220311555",  # #巨婴宝宝 (Series em bé AI khổng lồ hài hước)
            "1560480383202305",  # #萌娃 (Chủ đề em bé đáng yêu triệu tim trên Douyin)
            "1588769457994759",  # #熊孩子搞笑日常 (Các pha đối đáp hài hước của trẻ nhỏ)
            "1618466015647748",  # #一人带娃日常 (Nhật ký chăm con hài hước triệu view)
            "1639642543618055",  # #亲子互动日常 (Tương tác hài hước giữa bố mẹ và bé)
            "1576122662289438",  # #亲子互动 (Tương tác cha mẹ con cái)
            "1607861792729096",  # #你家孩子也这样吗 (Tình huống lém lỉnh của bé)
            "1632017292199939",  # #儿童搞笑视频 (Video hoạt hình & hài hước trẻ em)
        ],
    },
    "channel_3_novel_drama": {
        "channel_id": "3",
        "profile_key": "channel_3_novel_drama",
        "folder_name": "audio_dem_khuyen",
        "description": "Truyện Audio Ngôn tình, Trùng sinh, Tổng tài & Đoản kịch (Trung - Việt view cao)",
        "seed_video_urls": [
            "https://www.tiktok.com/@tieutin2000/video/7682055125342424341",
            "https://www.tiktok.com/@bongcucaudio04/video/7698263475457592596",
            "https://www.tiktok.com/@gamukbangaudio/video/7670439173538549012",
            "https://www.tiktok.com/@soo.bin.0/video/7693321057758973185",
        ],
        "seed_channels": [
            "tieutin2000",
            "bongcucaudio04",
            "gamukbangaudio",
            "soo.bin.0",
        ],
        "douyin_keywords": [
            "小说推文",
            "重生文",
            "霸总短",
            "反转爽文",
            "有声书高能片段",
        ],
        "tiktok_keywords": [
            "#truyenaudio",
            "#kechuyendemkhuya",
            "#ngontinh",
            "#trongsinh",
            "#tongtai",
            "#小说推文",
        ],
        "tiktok_challenge_ids": [
            "1663304971287554",  # #truyenaudio (26.2B views VN)
            "1660070617826305",  # #kechuyendemkhuya (1.11B views VN)
            "1600998155267078",  # #ngontinh (30.1B views VN)
            "1692021200044033",  # #小说推文 (530M views CN)
        ],
        "douyin_challenge_ids": [
            "1597005944409102",  # #小说推文
            "1765126573358095",  # #好看又过瘾的小说推荐
            "1636581556231175",  # #宝藏小说 / #反转爽文
            "1597091696349188",  # #虐文 / #霸总短
            "1588303515058184",  # #有声小说 / #有声书高能片段
            "1593525576614915",  # #小说推荐 / #重生文
        ],
    },
    "channel_4_ai_cultivation": {
        "channel_id": "4",
        "profile_key": "channel_4_ai_cultivation",
        "folder_name": "tu_chan_gioi",
        "description": "AI Tu tiên, Tiên nghịch, Huyền huyễn, Hoạt hình 3D Tu chân (Trung - Việt view cao)",
        "seed_video_urls": [
            "https://www.tiktok.com/@kimvuongaudio/video/7650380932804906261",
            "https://www.tiktok.com/@funny.gau/video/7683492499272273173",
        ],
        "seed_channels": [
            "kimvuongaudio",
            "funny.gau",
        ],
        "douyin_keywords": [
            "AI修仙",
            "修仙小说推文",
            "玄幻修真短剧",
            "宗门大比",
            "无敌修仙",
            "修真洪荒",
        ],
        "tiktok_keywords": [
            "#tiennghich",
            "#tienhiep",
            "#huyenhuyen",
            "#hoathinh3d",
            "#修仙",
            "#修仙动画",
        ],
        "tiktok_challenge_ids": [
            "1689924968983554",  # #tiennghich (3.85B views VN)
            "1637461312204801",  # #tienhiep (803M views VN)
            "57569679",  # #huyenhuyen (466M views VN)
            "1659158692304898",  # #hoathinh3d (9.6B views VN)
            "1571938920820737",  # #修仙 (120M views CN)
            "7074136725626290177",  # #修仙动画 (13.7M views CN)
        ],
        "douyin_challenge_ids": [
            "1571938920820737",  # #修仙 (Tu tiên chính thống / Tiên hiệp)
            "1618717699338407",  # #师尊 (Sư tôn / Tông môn đại tỷ)
            "1610102238337037",  # #古风意境 (Cổ phong tiên hiệp / Hồng hoang)
            "1707580528615437",  # #仙尊语录 / #AI修仙 / #无敌修仙
            "1588303515058184",  # #有声小说 / #修仙小说推文
        ],
    },
}

CHANNEL_ALIASES: Dict[str, str] = {
    "1": "channel_1_ai_animation",
    "channel_1": "channel_1_ai_animation",
    "video_ai_hay": "channel_1_ai_animation",
    "video_AI_hay": "channel_1_ai_animation",
    "channel_1_ai_animation": "channel_1_ai_animation",
    "2": "channel_2_life_comedy",
    "channel_2": "channel_2_life_comedy",
    "baby_ai": "channel_2_life_comedy",
    "baby_AI": "channel_2_life_comedy",
    "channel_2_life_comedy": "channel_2_life_comedy",
    "3": "channel_3_novel_drama",
    "channel_3": "channel_3_novel_drama",
    "audio_dem_khuyen": "channel_3_novel_drama",
    "channel_3_novel_drama": "channel_3_novel_drama",
    "4": "channel_4_ai_cultivation",
    "channel_4": "channel_4_ai_cultivation",
    "tu_chan_gioi": "channel_4_ai_cultivation",
    "channel_4_ai_cultivation": "channel_4_ai_cultivation",
}

LEGACY_CHANNEL_FOLDER_MAP: Dict[str, str] = {
    "channel_1": "video_AI_hay",
    "channel_2": "baby_AI",
    "channel_3": "audio_dem_khuyen",
    "channel_4": "tu_chan_gioi",
    "video_ai_hay": "video_AI_hay",
    "video_AI_hay": "video_AI_hay",
    "baby_ai": "baby_AI",
    "baby_AI": "baby_AI",
    "audio_dem_khuyen": "audio_dem_khuyen",
    "tu_chan_gioi": "tu_chan_gioi",
}

# Danh sách chủ đề kể chuyện chuẩn trên Douyin & Challenge IDs tương ứng
DOUYIN_STORY_GENRES: Dict[str, List[str]] = {
    "情感故事": [
        "1571371300932626",  # #这是一个悲伤的故事
        "1572618705886286",  # #情感
        "1638200962944004",  # #情感文案
        "1567586472199170",  # #情感视频
        "1590199429695495",  # #泪点低的人别看
        "1608558450231304",  # #意难平
    ],
    "睡前故事": [
        "1578523164433422",  # #治愈
        "1566159894075394",  # #配音
        "1571371300932626",  # #这是一个悲伤的故事
        "1767587183750168",  # #情感语录
    ],
    "讲故事": [
        "1567291448659969",  # #讲故事
        "1574288256850958",  # #意想不到的结局
        "1571371300932626",  # #这是一个悲伤的故事
        "1583473234973709",  # #剧情
        "1566159894075394",  # #配音
    ],
    "有声书": [
        "1588303515058184",  # #有声小说 / #有声书
        "1566159894075394",  # #配音
        "1739149071728643",  # #配音木成
        "1707580528615437",  # #仙尊语录
        "1767587183750168",  # #情感语录
    ],
    "文案": [
        "1658420629774343",  # #文案
        "1638200962944004",  # #情感文案
        "1767587183750168",  # #情感语录
        "1626065775068173",  # #总有一句话戳心
    ],
    # Từ khóa thuộc Kênh 1 (AI Animation)
    "AI动画短片": ["7481095278095763519", "1767586789604366", "1549679016708097"],
    "萌宠成精": ["7481095278095763519", "1578523164433422", "1674607865397325"],
    "动物大逃亡": ["7481095278095763519", "1583473234973709", "1684216673933320"],
    "克苏鲁AI": ["1625956003624974", "1606571520286788", "7481095278095763519"],
    "AI短剧": ["1682091371105293", "7481095278095763519", "1592197408664647"],
    "治愈系动漫": ["1578523164433422", "1767586789604366", "1551203555277826"],
    # Từ khóa thuộc Kênh 2 (Baby AI / Em bé hài hước Trung - Việt)
    "大头菠萝": ["1631527180822535", "1619206220311555", "1618466015647748"],
    "AI萌娃": ["1631527180822535", "1619206220311555", "1560480383202305"],
    "巨婴宝宝": ["1619206220311555", "1631527180822535", "1632017292199939"],
    "萌娃搞笑日常": ["1560480383202305", "1588769457994759", "1618466015647748"],
    "人类幼崽": ["1560480383202305", "1607861792729096", "1639642543618055"],
    "熊孩子搞笑日常": ["1588769457994759", "1607861792729096", "1560480383202305"],
    "一人带娃日常": ["1618466015647748", "1639642543618055", "1576122662289438"],
    # Từ khóa thuộc Kênh 3 (Novel Drama)
    "小说推文": ["1597005944409102", "1765126573358095", "1636581556231175"],
    "重生文": ["1593525576614915", "1597005944409102", "1765126573358095"],
    "霸总短": ["1597091696349188", "1597005944409102", "1765126573358095"],
    "反转爽文": ["1636581556231175", "1765126573358095", "1597005944409102"],
    "有声书高能片段": ["1588303515058184", "1597005944409102", "1636581556231175"],
    # Từ khóa thuộc Kênh 4 (AI Cultivation)
    "AI修仙": ["1571938920820737", "1707580528615437", "1618717699338407"],
    "修仙小说推文": ["1571938920820737", "1588303515058184", "1597005944409102"],
    "玄幻修真短剧": ["1571938920820737", "1618717699338407", "1707580528615437"],
    "宗门大比": ["1571938920820737", "1618717699338407", "1610102238337037"],
    "无敌修仙": ["1571938920820737", "1707580528615437", "1610102238337037"],
    "修真洪荒": ["1571938920820737", "1610102238337037", "1707580528615437"],
}

# Danh sách chủ đề chuẩn trên TikTok (Chỉ dùng từ khóa Trung Quốc & Việt Nam)
TIKTOK_STORY_GENRES: Dict[str, List[str]] = {
    "hoathinhai": ["hoathinhai", "phimnganai", "ai动画", "ai短剧", "ai萌宠"],
    "phimnganai": ["phimnganai", "hoathinhai", "ai动画", "ai短剧"],
    "embehaihuoc": ["ai萌娃", "萌娃搞笑日常", "人类幼崽", "embehaihuoc", "embecute", "embedangyeu"],
    "baby_ai": ["ai萌娃", "萌娃搞笑日常", "人类幼崽", "语出惊人的熊孩子", "embehaihuoc", "embecute"],
    "truyenaudio": ["truyenaudio", "kechuyendemkhuya", "ngontinh", "trongsinh", "小说推文"],
    "ngontinh": ["ngontinh", "truyenaudio", "kechuyendemkhuya", "trongsinh"],
    "tutien": ["tiennghich", "tienhiep", "huyenhuyen", "hoathinh3d", "修仙"],
    "tienhiep": ["tienhiep", "tiennghich", "huyenhuyen", "hoathinh3d", "修仙"],
}

# Từ khóa nhận diện nhạc thương mại / EDM / Remix / Nhảy nhót không thoại cần loại bỏ
MUSIC_BLACKLIST_KEYWORDS = (
    "remix",
    "dj ",
    "dj-",
    "dj版",
    "(dj",
    "edm",
    "dance",
    "sped up",
    "slowed",
    "reverb",
    "instrumental",
    "karaoke",
    "official video",
    "official music",
    "choreography",
    "beat ",
    "bgm",
    "纯音乐",
    "伴奏",
    "慢摇",
    "蹦迪",
    "手势舞",
    "跳舞",
)

# Blacklist loại trừ video bán hàng, review sản phẩm, livestream, quảng cáo, ảnh tĩnh, mẹo vặt linh tinh
CONTENT_BLACKLIST_DOUYIN = (
    "带货",
    "直播",
    "广告",
    "好物分享",
    "测评",
    "种草好物",
    "购物车",
    "小黄车",
    "秒杀",
    "清仓",
    "福利",
    "小妙招",
    "生活小妙招",
    "短视频运营",
    "自媒体创业",
    "引流",
    "变现",
    "仙侠手游",
    "手游推荐",
    "图文",
    "静态壁纸",
    "照片合集",
    "穿搭",
    "写真",
)

CONTENT_BLACKLIST_TIKTOK = (
    "#ad",
    "#haul",
    "#grwm",
    "#tiktokshop",
    "#sponsored",
    "#commission",
    "#unboxing",
    "#lifehack",
    "link in bio",
    "shop now",
    "paid partnership",
    "livestream",
    "#slideshow",
    "#photomode",
)

# Từ khóa nhận diện âm thanh gốc (Chỉ giữ Tiếng Trung & Tiếng Việt)
ORIGINAL_SOUND_MARKERS = (
    "原声",
    "配音",
    "original sound",
    "original audio",
    "âm thanh gốc",
    "nhạc nền -",
)


def resolve_channel_profile(channel_arg: str) -> Dict[str, Any]:
    """Phân giải tham số `--channel` (1, 2, 3, 4 hoặc tên profile) sang cấu hình kênh."""
    key = str(channel_arg).strip().lower()
    profile_name = CHANNEL_ALIASES.get(key)
    if not profile_name or profile_name not in CHANNEL_PROFILES:
        valid = ", ".join(sorted(CHANNEL_PROFILES.keys()))
        raise ValueError(
            f"Kênh '{channel_arg}' không hợp lệ. Chọn 1, 2, 3, 4 hoặc một trong: {valid}"
        )
    return CHANNEL_PROFILES[profile_name]


class StoryAudioCrawler:
    """
    Cào tự động các Audio / Video kể chuyện đang viral (Storytelling / Spoken Voice)
    trên TikTok và Douyin, hỗ trợ phân luồng 4 kênh TikTok chuyên biệt (`channel_1` -> `channel_4`):
    1. Thời lượng: 30s - 120s & Chống trùng lặp qua `story_audio_history.json`
    2. Filter Engine: Tối thiểu 20,000 lượt thích (Likes), loại bỏ Livestream (`直播`), Quảng cáo bán hàng (`带货`),
       và loại bỏ video chỉ ghép ảnh tĩnh không có chuyển động video.
    3. Voice Density Filter (Whisper): Kiểm tra thực tế số câu thoại >= 3 câu VÀ
       tổng thời lượng giọng nói chiếm >= 50% độ dài video.
    4. Quản lý hàng đợi Ready / Posted (`output/channel_X/ready/` & `output/channel_X/posted/`)
       đồng bộ với Master CSV (`output/danh_sach_video.csv`).
    """

    def __init__(
        self,
        project_root: Path,
        min_duration: float = 15.0,
        max_duration: float = 2400.0,
        min_segments: int = 2,
        min_speech_ratio: float = 0.25,
        min_likes: int = 10,
        min_shares_saves: int = 0,
        whisper_model: Optional[str] = None,
    ):
        self.project_root = Path(project_root)
        self.history_path = self.project_root / "story_audio_history.json"
        self.output_root = self.project_root / "output"
        self.csv_path = self.output_root / "danh_sach_video.csv"
        self.temp_root = self.project_root / "workspace_temp" / "crawler_candidates"
        self.temp_root.mkdir(parents=True, exist_ok=True)

        # Khởi tạo sẵn cấu trúc thư mục ready/ và posted/ cho 4 kênh và đồng bộ dữ liệu cũ
        self.ensure_channel_directories()

        self.min_duration = min_duration
        self.max_duration = max_duration
        self.min_segments = min_segments
        self.min_speech_ratio = min_speech_ratio
        self.min_likes = min_likes
        self.min_shares_saves = min_shares_saves
        self._transcriber: Optional[AudioTranscriber] = None
        self._whisper_model = whisper_model
        self.history = self._load_history()

    @property
    def transcriber(self) -> AudioTranscriber:
        if self._transcriber is None:
            self._transcriber = AudioTranscriber(model_size=self._whisper_model)
        return self._transcriber

    def ensure_channel_directories(self) -> None:
        """
        Đảm bảo 4 thư mục kênh `output/channel_1` .. `output/channel_4` cùng 2 thư mục con
        `ready/` và `posted/` luôn tồn tại, đồng thời tự động dọn dẹp & di chuyển các file `.mp4`
        nằm trực tiếp trong `output/channel_X/` vào `output/channel_X/ready/`.
        """
        self.output_root.mkdir(parents=True, exist_ok=True)
        for profile in CHANNEL_PROFILES.values():
            ch_dir = self.output_root / profile["folder_name"]
            ready_dir = ch_dir / "ready"
            posted_dir = ch_dir / "posted"
            ready_dir.mkdir(parents=True, exist_ok=True)
            posted_dir.mkdir(parents=True, exist_ok=True)

            # Tự động chuyển các file .mp4 nằm trực tiếp ở output/channel_X/ vào output/channel_X/ready/
            for mp4_file in ch_dir.glob("*.mp4"):
                if mp4_file.is_file():
                    dest = ready_dir / mp4_file.name
                    if dest.exists():
                        dest.unlink()
                    shutil.move(str(mp4_file), str(dest))
                    logger.info(f"Đã chuyển video vào hàng đợi chờ đăng: {dest}")

        # Dọn dẹp các file video test/rác cũ nằm trực tiếp ngoài thư mục output/
        for stray_mp4 in self.output_root.glob("*.mp4"):
            if stray_mp4.is_file():
                stray_mp4.unlink(missing_ok=True)
                logger.info(f"Đã dọn dẹp file video test cũ ngoài thư mục gốc output/: {stray_mp4.name}")

        # Đảm bảo file CSV được nâng cấp lên schema chuẩn (có cột status=READY/POSTED và posted_at)
        self._upgrade_master_csv_schema()

    def _collect_all_known_posted_story_ids_and_files(self) -> Tuple[Set[str], Set[str]]:
        """
        Thu thập toàn bộ `story_id` và `filename` đã từng được đăng trên cả 4 kênh từ:
        1. Thư mục `output/<channel>/posted/*.mp4`
        2. File `output/danh_sach_video.csv` (cột `status == POSTED`)
        3. File `uploaded_videos_registry.json` (sổ cái chống đăng trùng vĩnh viễn)
        4. File `remote_telemetry/intel_status_report.json` (lịch sử telemetry upload)
        5. Tập hạt giống `KNOWN_POSTED_IDS` đã xác nhận từ lịch sử Git.
        """
        posted_ids: Set[str] = {
            "7690915418453708052",
            "7691716032381127966",
            "7694494576270216478",
            "7653148698738335945",
            "7640576010862205938",
            "7696217946204441869",
            "7699677240048569621",
            "7698936354142506260",
            "7698023893977599272",
            "7697561971461197082",
            "7682055125342424341",
            "7670439173538549012",
            "7699354713556323590",
            "7650380932804906261",
            "7683492499272273173",
            "7698299779394080050",
            "7638714267462561417",
        }
        posted_filenames: Set[str] = {
            "20261023_090135_tiktok_story_7690915418453708052.mp4",
            "20261025_140905_tiktok_story_7691716032381127966.mp4",
            "20261025_145613_tiktok_story_7691716032381127966.mp4",
            "20261025_142722_tiktok_story_7694494576270216478.mp4",
            "20261025_163628_douyin_story_7653148698738335945.mp4",
            "20261025_165507_douyin_story_7640576010862205938.mp4",
            "20261025_172156_tiktok_story_7696217946204441869.mp4",
            "20261025_170051_tiktok_story_7699677240048569621.mp4",
            "20261025_181833_tiktok_story_7698936354142506260.mp4",
        }

        def _extract_sid(text: str) -> str:
            m = re.search(r"(?:tiktok|douyin)_story_(\d+)", str(text or ""), re.IGNORECASE)
            if m:
                return m.group(1)
            m2 = re.search(r"/video/(\d+)", str(text or ""))
            return m2.group(1) if m2 else ""

        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
            posted_dir = self.output_root / ch_folder / "posted"
            if posted_dir.exists():
                for mp4 in posted_dir.glob("*.mp4"):
                    if mp4.is_file():
                        posted_filenames.add(mp4.name)
                        sid = _extract_sid(mp4.name)
                        if sid:
                            posted_ids.add(sid)

        if self.csv_path.exists():
            try:
                with open(self.csv_path, mode="r", newline="", encoding="utf-8") as rf:
                    for row in csv.DictReader(rf):
                        st = (row.get("status") or "").strip().upper()
                        fn = (row.get("filename") or "").strip()
                        s_url = (row.get("source_url") or "").strip()
                        if st == "POSTED" or "/posted/" in fn:
                            if fn:
                                posted_filenames.add(Path(fn).name)
                            sid = _extract_sid(fn) or _extract_sid(s_url)
                            if sid:
                                posted_ids.add(sid)
            except Exception:
                pass

        reg_path = self.project_root / "uploaded_videos_registry.json"
        if reg_path.exists():
            try:
                reg_data = json.loads(reg_path.read_text(encoding="utf-8"))
                for sid in reg_data.get("posted_story_ids") or []:
                    if sid:
                        posted_ids.add(str(sid))
                for fn in reg_data.get("posted_filenames") or []:
                    if fn:
                        posted_filenames.add(Path(str(fn)).name)
            except Exception:
                pass

        telem_path = self.project_root / "remote_telemetry" / "intel_status_report.json"
        if telem_path.exists():
            try:
                tdata = json.loads(telem_path.read_text(encoding="utf-8"))
                events = list(tdata.get("recent_events") or [])
                if tdata.get("latest_event"):
                    events.append(tdata["latest_event"])
                for ev in events:
                    if isinstance(ev, dict) and ev.get("event_type") == "upload":
                        vfile = str((ev.get("details") or {}).get("video_file") or "").strip()
                        if vfile:
                            posted_filenames.add(Path(vfile).name)
                            sid = _extract_sid(vfile)
                            if sid:
                                posted_ids.add(sid)
            except Exception:
                pass

        return posted_ids, posted_filenames

    def _load_history(self) -> Dict[str, Any]:
        """Đọc lịch sử các video_id và sound_id đã xử lý từ `story_audio_history.json` + đồng bộ ổ cứng."""
        data: Dict[str, Any] = {"video_ids": [], "sound_ids": [], "items": []}
        if self.history_path.exists():
            try:
                loaded = json.loads(self.history_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    loaded.setdefault("video_ids", [])
                    loaded.setdefault("sound_ids", [])
                    loaded.setdefault("items", [])
                    data = loaded
            except Exception as exc:
                logger.warning(f"Không thể đọc {self.history_path}: {exc}")

        try:
            posted_ids, _ = self._collect_all_known_posted_story_ids_and_files()
            existing_vids = set(str(v) for v in data.get("video_ids", []))
            for sid in posted_ids:
                if sid not in existing_vids:
                    data["video_ids"].append(sid)
                    existing_vids.add(sid)
            for profile in CHANNEL_PROFILES.values():
                ch_folder = profile["folder_name"]
                for sub in ("ready", "posted"):
                    sub_dir = self.output_root / ch_folder / sub
                    if sub_dir.exists():
                        for mp4 in sub_dir.glob("*.mp4"):
                            m = re.search(r"(?:tiktok|douyin)_story_(\d+)", mp4.stem, re.IGNORECASE)
                            if m and m.group(1) not in existing_vids:
                                data["video_ids"].append(m.group(1))
                                existing_vids.add(m.group(1))
        except Exception:
            pass
        return data

    def save_to_history(
        self,
        video_id: str,
        sound_id: str,
        platform: str,
        genre: str,
        source_url: str,
        output_filename: str,
        channel: str = "",
    ) -> None:
        """Lưu `video_id` và `sound_id` vào `story_audio_history.json` để tránh cào trùng."""
        self.history = self._load_history()
        if video_id and str(video_id) not in self.history["video_ids"]:
            self.history["video_ids"].append(str(video_id))
        if sound_id and str(sound_id) not in self.history["sound_ids"]:
            self.history["sound_ids"].append(str(sound_id))

        self.history["items"].append(
            {
                "channel": channel,
                "video_id": str(video_id),
                "sound_id": str(sound_id),
                "platform": platform,
                "genre": genre,
                "source_url": source_url,
                "output_filename": output_filename,
                "crawled_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self.history_path.write_text(
            json.dumps(self.history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_processed(self, video_id: str, sound_id: Optional[str] = None) -> bool:
        """Kiểm tra xem video_id hoặc sound_id đã từng được xử lý hoặc đã nằm trong ready/posted chưa."""
        vid_str = str(video_id).strip()
        if vid_str in self.history.get("video_ids", []):
            return True
        if sound_id and str(sound_id) in self.history.get("sound_ids", []):
            return True
        if vid_str:
            posted_ids, _ = self._collect_all_known_posted_story_ids_and_files()
            if vid_str in posted_ids:
                return True
            for profile in CHANNEL_PROFILES.values():
                ch_folder = profile["folder_name"]
                for sub in ("ready", "posted"):
                    sub_dir = self.output_root / ch_folder / sub
                    if sub_dir.exists() and any(vid_str in f.name for f in sub_dir.glob("*.mp4")):
                        return True
        return False

    @staticmethod
    def is_strictly_chinese_or_vietnamese_video(
        title: str,
        music_title: str = "",
        region: str = "",
        author_id: str = "",
        platform: str = "tiktok",
    ) -> bool:
        """
        Bộ lọc ngôn ngữ & quốc gia nghiêm ngặt (Strict China & Vietnam Only Gate):
        - Chỉ cho phép video của Trung Quốc (chữ Hán / Douyin) và Việt Nam (dấu tiếng Việt / kênh Việt Nam).
        - Chặn tuyệt đối mọi video nước ngoài khác (Anh, Tây Ban Nha, Nga, Hàn, Nhật, Thái, Ấn Độ, Ả Rập, Indonesia...).
        """
        combined = f"{title or ''} {music_title or ''}".strip()
        auth_clean = (author_id or "").strip().lower().lstrip("@")

        if auth_clean == "guangzhoujianglan_mx" or auth_clean.endswith("_mx"):
            return False

        # Chặn tuyệt đối các hệ chữ viết ngoài Trung Quốc & Việt Nam:
        # Kana Nhật (\u3040-\u30ff), Hangul Hàn (\uac00-\ud7af), Cyrillic Nga (\u0400-\u04ff),
        # Ả Rập (\u0600-\u06ff), Thái (\u0e00-\u0e7f), Hindi Devanagari (\u0900-\u097f)
        if re.search(
            r"[\u3040-\u30ff\uac00-\ud7af\u1100-\u11ff\u0400-\u04ff\u0600-\u06ff\u0e00-\u0e7f\u0900-\u097f]",
            combined,
        ):
            return False

        if platform == "douyin":
            return True

        if auth_clean in KNOWN_VIETNAMESE_HANDLES:
            return True

        has_vi = has_vietnamese_diacritics(combined)
        has_zh = len(re.findall(r"[\u4e00-\u9fff]", combined)) >= 2

        if has_vi or has_zh:
            return True

        return False

    @staticmethod
    def is_valid_baby_ai_topic(title: str, music_title: str = "") -> bool:
        """
        Bộ lọc chủ đề chuyên biệt cho kênh `baby_AI` (`channel_2_life_comedy`):
        - Bắt buộc nội dung phải thuộc chủ đề Em bé AI / Mầm non / Đại Đầu Bô Lô / Trẻ nhỏ hài hước đáng yêu.
        - Loại bỏ các video hài người lớn, vợ chồng, tắm gội (#淋浴), tình yêu đôi lứa không liên quan đến em bé.
        """
        combined = f"{title or ''} {music_title or ''}".lower()
        adult_offtopic_markers = (
            "淋浴",
            "爱夫",
            "夫妻幽默",
            "情侣",
            "相亲",
            "前任",
            "分手",
            "夜店",
            "蹦迪",
            "美妆",
        )
        for bad in adult_offtopic_markers:
            if bad in combined:
                return False

        baby_markers = (
            "娃",
            "宝",
            "孩",
            "幼崽",
            "菠萝",
            "亲子",
            "小胖",
            "小奶",
            "闺女",
            "儿子",
            "女儿",
            "儿童",
            "小朋友",
            "弟弟",
            "妹妹",
            "姐姐",
            "哥哥",
            "幼儿园",
            "小学生",
            "辅导作业",
            "em bé",
            "embe",
            "bé ",
            "con ",
            "con gái",
            "con trai",
            "mẹ và bé",
            "mevabe",
            "bố trông con",
            "chăm con",
            "chamcon",
            "baby",
        )
        return any(m in combined for m in baby_markers)

    @staticmethod
    def is_blacklisted_content(text: str, platform: str = "douyin") -> bool:
        """
        Kiểm tra xem tiêu đề / mô tả video có chứa từ khóa bán hàng, review sản phẩm,
        livestream, quảng cáo hoặc mẹo vặt linh tinh không.
        """
        lowered = (text or "").lower()
        for kw in CONTENT_BLACKLIST_DOUYIN:
            if kw.lower() in lowered:
                return True
        for kw in CONTENT_BLACKLIST_TIKTOK:
            if kw.lower() in lowered:
                return True
        return False

    @staticmethod
    def is_original_story_sound(
        music_title: str,
        is_original_flag: Optional[bool] = None,
        video_desc: str = "",
    ) -> Tuple[bool, bool]:
        """
        Kiểm tra âm thanh gốc (Original Voice Filter) & Blacklist nội dung rác:
        Trả về `(allowed, is_explicit_original)`:
        - `allowed = False` nếu phát hiện bài hát thương mại / EDM / Remix / Bán hàng / Livestream / Quảng cáo.
        - `is_explicit_original = True` nếu có nhãn `原声` hoặc `original sound`.
        """
        combined = f"{music_title} {video_desc}".lower()
        for bad_kw in MUSIC_BLACKLIST_KEYWORDS:
            if bad_kw in combined:
                return False, False

        if StoryAudioCrawler.is_blacklisted_content(combined):
            return False, False

        title_lower = (music_title or "").lower()
        is_explicit_orig = bool(is_original_flag) or any(
            marker in title_lower for marker in ORIGINAL_SOUND_MARKERS
        )

        if "原声中的歌曲" in music_title and not any(
            story_kw in video_desc
            for story_kw in (
                "故事",
                "文案",
                "语录",
                "配音",
                "有声",
                "小说",
                "感情",
                "经历",
                "解说",
                "动画",
                "修仙",
                "玄幻",
                "搞笑",
                "日常",
                "喜剧",
                "短剧",
                "萌娃",
                "幼崽",
                "宝宝",
            )
        ):
            return False, False

        return True, is_explicit_orig

    def _build_seed_url_candidates(
        self, seed_urls: List[str], genre_label: str
    ) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
        """
        Giải nén và tạo danh sách ứng viên ưu tiên số 1 trực tiếp từ các link video mẫu (`seed_video_urls`),
        đồng thời bóc tách thêm `hashtag_id` và `music_id` từ các video mẫu Douyin để tìm video liên quan.
        """
        candidates: List[Dict[str, Any]] = []
        extra_ch_ids: List[str] = []
        extra_music_ids: List[str] = []

        if not seed_urls:
            return candidates, extra_ch_ids, extra_music_ids

        downloader = VideoDownloader(work_dir=self.temp_root)
        dy_session = requests.Session()
        guest_cookies = fetch_douyin_guest_cookies()
        for domain in (".iesdouyin.com", ".douyin.com"):
            for ck_name, ck_val in guest_cookies.items():
                dy_session.cookies.set(ck_name, ck_val, domain=domain)

        for raw_url in seed_urls:
            try:
                clean_url = downloader.extract_url_from_text(raw_url)
                if "guangzhoujianglan_mx" in clean_url.lower():
                    continue
                platform = downloader.detect_platform(clean_url)
                resolved_url, vid = downloader.resolve_short_url(clean_url, platform)
                if not vid:
                    continue

                if platform == "douyin":
                    detail = self._fetch_douyin_item_detail(dy_session, vid) or {}
                    for te in detail.get("text_extra") or []:
                        hid = str(te.get("hashtag_id") or "")
                        hname = str(te.get("hashtag_name") or "")
                        if hid and hid not in extra_ch_ids and "淋浴" not in hname:
                            extra_ch_ids.append(hid)
                    music_obj = detail.get("music") or {}
                    mid = str(music_obj.get("mid") or "")
                    if mid and mid not in extra_music_ids:
                        extra_music_ids.append(mid)

                    if self.is_processed(vid):
                        continue

                    duration = float((detail.get("video") or {}).get("duration") or 60000.0) / 1000.0
                    desc = str(detail.get("desc") or f"douyin_{vid}")
                    stats = detail.get("statistics") or {}
                    digg = int(stats.get("digg_count") or 100000)
                    share = int(stats.get("share_count") or 1000)
                    collect = int(stats.get("collect_count") or 1000)

                    candidates.append(
                        {
                            "platform": "douyin",
                            "genre": genre_label,
                            "video_id": vid,
                            "sound_id": mid or f"dy_seed_{vid}",
                            "source_url": f"https://www.douyin.com/video/{vid}",
                            "duration": duration,
                            "title": desc,
                            "music_title": str(music_obj.get("title") or "原声"),
                            "is_original": True,
                            "is_recent_30d": True,
                            "is_seed": True,
                            "is_vietnamese": False,
                            "play_count": max(digg * 15, 200000),
                            "digg_count": digg,
                            "share_count": share,
                            "collect_count": collect,
                            "shares_and_saves": share + collect,
                            "engagement_score": 10_000_000 + digg,
                        }
                    )
                else:
                    if self.is_processed(vid):
                        continue
                    handle_match = re.search(r"tiktok\.com/@([^/?#]+)", resolved_url)
                    author_handle = (handle_match.group(1) if handle_match else "").lower()
                    if author_handle == "guangzhoujianglan_mx" or author_handle.endswith("_mx"):
                        continue
                    is_vi = author_handle in KNOWN_VIETNAMESE_HANDLES

                    candidates.append(
                        {
                            "platform": "tiktok",
                            "genre": genre_label,
                            "video_id": vid,
                            "sound_id": f"tt_seed_{vid}",
                            "source_url": resolved_url,
                            "duration": 120.0,
                            "title": "",
                            "music_title": "original sound",
                            "is_original": True,
                            "is_recent_30d": True,
                            "is_seed": True,
                            "is_vietnamese": is_vi,
                            "author_handle": author_handle,
                            "play_count": 300000,
                            "digg_count": 100000,
                            "share_count": 1000,
                            "collect_count": 1000,
                            "shares_and_saves": 2000,
                            "engagement_score": 10_000_000,
                        }
                    )
            except Exception as exc:
                logger.warning(f"Không thể phân giải link mẫu {raw_url}: {exc}")

        return candidates, extra_ch_ids, extra_music_ids

    def discover_tiktok_channel_videos(
        self,
        handle_or_url: str,
        genre_label: str = "storytime",
        max_videos: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Lấy toàn bộ danh sách video từ một kênh TikTok (`@handle` hoặc link kênh `https://www.tiktok.com/@handle`)
        bằng API `/api/creator/item_list/` với chữ ký TLS `safari15_5`.
        Chỉ lấy kênh Trung Quốc hoặc Việt Nam và ưu tiên sắp xếp theo lượt xem (playCount) cao nhất.
        """
        raw = handle_or_url.strip()
        handle_match = re.search(r"tiktok\.com/@([^/?#\s]+)", raw)
        if handle_match:
            handle = handle_match.group(1).strip().lstrip("@")
        else:
            handle = raw.lstrip("@").strip().split("/")[0]

        handle_lower = handle.lower()
        if handle_lower == "guangzhoujianglan_mx" or handle_lower.endswith("_mx"):
            logger.info(f"Bỏ qua kênh nước ngoài ngoài Trung/Việt: @{handle}")
            return []

        sec_uid = KNOWN_TIKTOK_SEC_UIDS.get(handle_lower)
        if not sec_uid:
            try:
                prof_resp = creq.get(
                    f"https://www.tiktok.com/@{handle}",
                    impersonate="safari15_5",
                    timeout=15,
                )
                sec_match = re.search(r'"secUid":"([^"]+)"', prof_resp.text)
                if sec_match:
                    sec_uid = sec_match.group(1)
            except Exception as exc:
                logger.warning(f"Không thể lấy secUid cho kênh @{handle}: {exc}")

        if not sec_uid:
            logger.warning(f"Không tìm thấy secUid cho kênh TikTok @{handle}.")
            return []

        candidates: List[Dict[str, Any]] = []
        seen_ids = set()
        cursor = str(int(time.time() * 1000))
        has_cookies = bool(os.getenv("COOKIES_FILE") and Path(os.getenv("COOKIES_FILE", "")).is_file())

        for _page in range(max(1, (max_videos + 14) // 15)):
            if len(candidates) >= max_videos:
                break
            try:
                resp = creq.get(
                    "https://www.tiktok.com/api/creator/item_list/",
                    params={
                        "aid": "1988",
                        "count": "15",
                        "cursor": cursor,
                        "secUid": sec_uid,
                        "type": "1",
                        "language": "vi",
                    },
                    headers={"Referer": f"https://www.tiktok.com/@{handle}"},
                    impersonate="safari15_5",
                    timeout=15,
                )
                if resp.status_code != 200 or not resp.text:
                    break
                data = resp.json()
                item_list = data.get("itemList") or []
                if not item_list:
                    break

                for item in item_list:
                    vid = str(item.get("id") or "")
                    if not vid or vid in seen_ids:
                        continue
                    seen_ids.add(vid)

                    if self.is_processed(vid):
                        continue

                    # Bỏ qua bài ảnh tĩnh hoặc bài ShortDrama khóa playAddr khi chưa có COOKIES_FILE
                    if item.get("imagePost"):
                        continue
                    video_obj = item.get("video") or {}
                    play_addr = video_obj.get("playAddr") or video_obj.get("downloadAddr") or ""
                    if not play_addr and item.get("dramaInfo") and not has_cookies:
                        continue

                    duration = float(video_obj.get("duration") or 60.0)
                    if not (self.min_duration <= duration <= self.max_duration):
                        continue

                    desc = str(item.get("desc") or "")
                    music_obj = item.get("music") or {}
                    music_title = str(music_obj.get("title") or "original sound")

                    if not self.is_strictly_chinese_or_vietnamese_video(
                        title=desc,
                        music_title=music_title,
                        author_id=handle_lower,
                        platform="tiktok",
                    ):
                        continue

                    stats = item.get("stats") or {}
                    play_cnt = int(stats.get("playCount") or 0)
                    digg = int(stats.get("diggCount") or 0)
                    share = int(stats.get("shareCount") or 0)
                    collect = int(stats.get("collectCount") or 0)
                    sound_id = str(music_obj.get("id") or f"tt_ch_{vid}")
                    is_vi = (
                        handle_lower in KNOWN_VIETNAMESE_HANDLES
                        or has_vietnamese_diacritics(desc)
                    )

                    candidates.append(
                        {
                            "platform": "tiktok",
                            "genre": genre_label,
                            "video_id": vid,
                            "sound_id": sound_id,
                            "source_url": f"https://www.tiktok.com/@{handle}/video/{vid}",
                            "duration": duration,
                            "title": desc,
                            "music_title": music_title,
                            "is_original": True,
                            "is_recent_30d": True,
                            "is_channel_video": True,
                            "is_vietnamese": is_vi,
                            "author_handle": handle_lower,
                            "play_count": play_cnt,
                            "digg_count": digg,
                            "share_count": share,
                            "collect_count": collect,
                            "shares_and_saves": share + collect,
                            "engagement_score": 1_000_000 + play_cnt + digg * 5 + (share + collect) * 10,
                        }
                    )

                last_ctime = item_list[-1].get("createTime")
                if not data.get("hasMore") or not last_ctime:
                    break
                cursor = str(int(last_ctime) * 1000)
            except Exception as exc:
                logger.debug(f"Lỗi lấy danh sách video kênh @{handle}: {exc}")
                break

        # Sắp xếp ưu tiên các video có lượt xem (play_count) và tương tác cao nhất trong kênh mẫu
        candidates.sort(
            key=lambda x: (int(x.get("play_count") or 0), int(x.get("digg_count") or 0)),
            reverse=True,
        )
        logger.info(
            f"Đã quét kênh TikTok @{handle}: tìm thấy {len(candidates)} video Trung/Việt chưa xử lý (đã xếp theo view cao nhất)."
        )
        return candidates

    def _discover_douyin_by_music_ids(
        self, music_ids: List[str], genre_label: str, max_candidates: int = 15
    ) -> List[Dict[str, Any]]:
        """Tìm các video Douyin liên quan sử dụng cùng âm thanh gốc (`music_id`) với video mẫu."""
        if not music_ids:
            return []

        session = requests.Session()
        guest_cookies = fetch_douyin_guest_cookies()
        for domain in (".iesdouyin.com", ".douyin.com"):
            for ck_name, ck_val in guest_cookies.items():
                session.cookies.set(ck_name, ck_val, domain=domain)

        candidates: List[Dict[str, Any]] = []
        seen_vids = set()
        is_baby_channel = "channel_2" in genre_label or "baby" in genre_label.lower()

        for mid in music_ids:
            if len(candidates) >= max_candidates:
                break
            try:
                api_url = (
                    f"https://www.iesdouyin.com/web/api/v2/music/list/aweme/"
                    f"?music_id={mid}&count=20&cursor=0&aid=1128"
                )
                r = session.get(api_url, headers={"User-Agent": MOBILE_UA}, timeout=15)
                if r.status_code != 200:
                    continue
                aweme_list = r.json().get("aweme_list") or []
            except Exception as exc:
                logger.debug(f"Lỗi lấy music aweme Douyin {mid}: {exc}")
                continue

            for item in aweme_list:
                vid = str(item.get("aweme_id") or "")
                if not vid or vid in seen_vids or self.is_processed(vid):
                    continue
                seen_vids.add(vid)

                if (
                    item.get("images")
                    or item.get("aweme_type") in (2, 68, 150)
                    or item.get("is_ads")
                ):
                    continue

                video_obj = item.get("video") or {}
                duration = float(video_obj.get("duration") or 0.0) / 1000.0
                if not (self.min_duration <= duration <= self.max_duration):
                    continue

                desc = str(item.get("desc") or "")
                if not self.is_strictly_chinese_or_vietnamese_video(
                    title=desc, music_title="原声", platform="douyin"
                ):
                    continue
                if is_baby_channel and not self.is_valid_baby_ai_topic(desc):
                    continue

                stats = item.get("statistics") or {}
                digg = int(stats.get("digg_count") or 0)
                share = int(stats.get("share_count") or 0)
                collect = int(stats.get("collect_count") or 0)

                if digg < max(self.min_likes, 1000):
                    continue

                candidates.append(
                    {
                        "platform": "douyin",
                        "genre": genre_label,
                        "video_id": vid,
                        "sound_id": f"dy_music_{vid}",
                        "source_url": f"https://www.douyin.com/video/{vid}",
                        "duration": duration,
                        "title": desc,
                        "music_title": "原声",
                        "is_original": True,
                        "is_recent_30d": True,
                        "play_count": digg * 20,
                        "digg_count": digg,
                        "share_count": share,
                        "collect_count": collect,
                        "shares_and_saves": share + collect,
                        "engagement_score": digg + (share + collect) * 3,
                    }
                )

        candidates.sort(key=lambda x: int(x.get("engagement_score") or 0), reverse=True)
        return candidates

    def discover_for_channel(
        self,
        channel_arg: str,
        platform_override: Optional[str] = None,
        max_candidates: int = 50,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Tự động thu thập ứng viên cho từng kênh (`channel_1` .. `channel_4`) theo đúng thứ tự ưu tiên:
        1. Trực tiếp các video trong danh sách link mẫu (`seed_video_urls`).
        2. Tất cả các video trong các kênh mẫu (`seed_channels`).
        3. Các video liên quan cùng chủ đề / hashtag / âm thanh (`douyin_challenge_ids`, `music_ids`, `tiktok_keywords`),
           chỉ lấy video của Trung Quốc & Việt Nam và có lượt view/tương tác cao.
        """
        profile = resolve_channel_profile(channel_arg)
        profile_key = profile["profile_key"]
        seed_urls: List[str] = profile.get("seed_video_urls") or []
        seed_channels: List[str] = profile.get("seed_channels") or []
        douyin_kws: List[str] = profile.get("douyin_keywords") or []
        tiktok_kws: List[str] = profile.get("tiktok_keywords") or []
        tiktok_cids: List[str] = list(profile.get("tiktok_challenge_ids") or [])
        douyin_cids: List[str] = list(profile.get("douyin_challenge_ids") or [])

        logger.info(
            f"Đã nạp cấu hình kênh [{profile_key}] -> Thư mục: output/{profile['folder_name']}/ready/ | "
            f"Link mẫu: {len(seed_urls)} | Kênh mẫu: {seed_channels} (Chế độ: Chỉ lấy Trung Quốc & Việt Nam View Cao)"
        )

        # 1. Lấy trực tiếp các video từ link mẫu của người dùng
        seed_candidates, extra_ch_ids, extra_music_ids = self._build_seed_url_candidates(
            seed_urls=seed_urls,
            genre_label=profile_key,
        )
        if platform_override:
            seed_candidates = [
                c for c in seed_candidates if c["platform"] == platform_override
            ]

        for cid in reversed(extra_ch_ids):
            if cid not in douyin_cids:
                douyin_cids.insert(0, cid)

        # 2. Lấy tất cả các video từ các kênh mẫu (interleave đều giữa các kênh mẫu, ưu tiên view cao)
        channel_candidates: List[Dict[str, Any]] = []
        if platform_override in (None, "tiktok") and seed_channels:
            per_channel_lists: List[List[Dict[str, Any]]] = []
            for ch_handle in seed_channels:
                ch_vids = self.discover_tiktok_channel_videos(
                    handle_or_url=ch_handle,
                    genre_label=profile_key,
                    max_videos=25,
                )
                if ch_vids:
                    per_channel_lists.append(ch_vids)
            max_ch_len = max((len(lst) for lst in per_channel_lists), default=0)
            for idx in range(max_ch_len):
                for lst in per_channel_lists:
                    if idx < len(lst):
                        channel_candidates.append(lst[idx])

        # 3. Lấy các video liên quan (cùng âm thanh gốc / cùng challenge / hashtag) có view cao
        douyin_candidates: List[Dict[str, Any]] = []
        tiktok_candidates: List[Dict[str, Any]] = []

        use_douyin = platform_override in (None, "douyin") and bool(douyin_kws or douyin_cids)
        use_tiktok = platform_override in (None, "tiktok") and bool(tiktok_kws or tiktok_cids)

        if use_douyin:
            if extra_music_ids:
                douyin_candidates.extend(
                    self._discover_douyin_by_music_ids(
                        music_ids=extra_music_ids,
                        genre_label=profile_key,
                        max_candidates=10,
                    )
                )
            douyin_candidates.extend(
                self._discover_douyin_by_challenge_ids(
                    ch_ids=douyin_cids,
                    genre_label=profile_key,
                    max_candidates=max_candidates,
                )
            )
            douyin_candidates.sort(
                key=lambda x: int(x.get("engagement_score") or 0),
                reverse=True,
            )

        if use_tiktok:
            clean_tt_kws = [kw.lstrip("#").strip() for kw in tiktok_kws if kw.strip()]
            tiktok_candidates = self._discover_tiktok_by_keywords(
                keywords=clean_tt_kws,
                max_candidates=max_candidates,
                preset_challenge_ids=tiktok_cids,
                channel_profile_key=profile_key,
            )
            tiktok_candidates.sort(
                key=lambda x: (
                    int(x.get("play_count") or 0),
                    int(x.get("engagement_score") or 0),
                ),
                reverse=True,
            )

        # Gộp danh sách theo thứ tự ưu tiên: Link mẫu -> Kênh mẫu -> Video liên quan view cao (khử trùng lặp video_id)
        merged: List[Dict[str, Any]] = []
        seen_vids = set()

        for cand in seed_candidates + channel_candidates:
            vid = str(cand.get("video_id") or "")
            if vid and vid not in seen_vids:
                seen_vids.add(vid)
                merged.append(cand)

        related_pool: List[Dict[str, Any]] = []
        max_rel = max(len(douyin_candidates), len(tiktok_candidates))
        for i in range(max_rel):
            if i < len(douyin_candidates):
                related_pool.append(douyin_candidates[i])
            if i < len(tiktok_candidates):
                related_pool.append(tiktok_candidates[i])

        for cand in related_pool:
            vid = str(cand.get("video_id") or "")
            if vid and vid not in seen_vids:
                seen_vids.add(vid)
                merged.append(cand)

        logger.info(
            f"Kênh [{profile_key}]: Tìm thấy tổng cộng {len(merged)} ứng viên Trung/Việt view cao "
            f"(Link mẫu: {len(seed_candidates)}, Kênh mẫu: {len(channel_candidates)}, "
            f"Liên quan Douyin: {len(douyin_candidates)}, Liên quan TikTok: {len(tiktok_candidates)})."
        )
        return profile, merged[:max_candidates]

    def discover_candidates(
        self, platform: str, genre: str, max_candidates: int = 40
    ) -> List[Dict[str, Any]]:
        """
        Tìm kiếm danh sách ứng viên video kể chuyện theo nền tảng (`douyin` / `tiktok`)
        và chủ đề (`genre`), áp dụng bộ lọc Trung Quốc & Việt Nam view cao,
        khử trùng lặp và sắp xếp theo mức độ tương tác (view + tim + share) giảm dần.
        """
        platform = platform.lower().strip()
        clean_genre = genre.lstrip("#").strip()

        if platform == "tiktok":
            search_terms = TIKTOK_STORY_GENRES.get(clean_genre.lower(), [clean_genre])
            if clean_genre.lower() not in [t.lower() for t in search_terms]:
                search_terms = [clean_genre] + search_terms
            candidates = self._discover_tiktok_by_keywords(search_terms, max_candidates)
        elif platform == "douyin":
            ch_ids = list(DOUYIN_STORY_GENRES.get(clean_genre, []))
            for g_name, g_cids in DOUYIN_STORY_GENRES.items():
                if clean_genre in g_name or g_name in clean_genre:
                    for c in g_cids:
                        if c not in ch_ids:
                            ch_ids.append(c)
            if not ch_ids:
                for g_cids in DOUYIN_STORY_GENRES.values():
                    for c in g_cids:
                        if c not in ch_ids:
                            ch_ids.append(c)
            candidates = self._discover_douyin_by_challenge_ids(
                ch_ids, clean_genre, max_candidates
            )
        else:
            raise ValueError(
                f"Platform không hợp lệ: {platform} (chỉ hỗ trợ 'tiktok' hoặc 'douyin')"
            )

        candidates.sort(
            key=lambda x: (
                int(x.get("play_count", 0)),
                int(x.get("engagement_score", 0)),
                1 if x.get("is_original") else 0,
            ),
            reverse=True,
        )

        logger.info(
            f"Đã tìm thấy {len(candidates)} ứng viên Trung/Việt view cao [{platform.upper()} - '{genre}'] "
            f"thỏa điều kiện thời lượng ({self.min_duration:.0f}s-{self.max_duration:.0f}s) & chưa trùng lặp."
        )
        return candidates

    def _discover_tiktok_by_keywords(
        self,
        keywords: List[str],
        max_candidates: int = 40,
        preset_challenge_ids: Optional[List[str]] = None,
        channel_profile_key: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Tìm ứng viên video trên TikTok từ danh sách Challenge IDs & từ khóa Hashtag:
        - Chỉ lấy video của Trung Quốc (chữ Hán) và Việt Nam (tiếng Việt).
        - Chỉ lấy video có lượng view cao (`play_count >= 100,000` hoặc `digg_count >= 5,000`).
        - Tuyệt đối không fallback sang hashtag tiếng Anh (`storytime`, `redditstories`).
        """
        challenge_ids: List[Tuple[str, str]] = []
        default_label = keywords[0] if keywords else (channel_profile_key or "tiktok_vn_cn")

        if preset_challenge_ids:
            for pcid in preset_challenge_ids:
                cid_clean = str(pcid).strip()
                if cid_clean and cid_clean not in [x[0] for x in challenge_ids]:
                    challenge_ids.append((cid_clean, default_label))
        else:
            for term in keywords:
                clean_term = term.lstrip("#").strip()
                if not clean_term:
                    continue
                try:
                    resp = creq.get(
                        "https://www.tikwm.com/api/challenge/search",
                        params={"keywords": clean_term},
                        impersonate="chrome",
                        timeout=12,
                    )
                    if resp.status_code == 200 and resp.text.strip().startswith("{"):
                        ch_list = (resp.json().get("data") or {}).get("challenge_list") or []
                        for ch in ch_list[:3]:
                            cid = str(ch.get("id") or "")
                            cha_name = str(ch.get("cha_name") or "").strip().lower()
                            view_cnt = int(ch.get("view_count") or 0)
                            if not cid or cid in [x[0] for x in challenge_ids]:
                                continue
                            if cha_name == clean_term.lower() or (
                                clean_term.lower() in cha_name and view_cnt >= 1_000_000
                            ):
                                challenge_ids.append((cid, clean_term))
                except Exception as exc:
                    logger.debug(f"TikTok challenge search error ({clean_term}): {exc}")

        if not challenge_ids:
            challenge_ids = [
                ("1634934865901569", "embehaihuoc"),
                ("1678587005908993", "萌娃搞笑日常"),
                ("7236712553568976902", "ai萌娃"),
            ]

        now_ts = int(time.time())
        thirty_days_sec = 30 * 86400
        seen_vids = set()
        candidates: List[Dict[str, Any]] = []
        is_baby_channel = (
            "channel_2" in channel_profile_key
            or "baby" in channel_profile_key.lower()
            or any(
                k in " ".join(keywords).lower()
                for k in ("萌娃", "幼崽", "embehaihuoc", "embecute", "embedangyeu", "熊孩子")
            )
        )

        target_cap = min(max_candidates, 24)
        for cid, kw_label in challenge_ids:
            if len(candidates) >= target_cap:
                break
            for cursor in (0, 20):
                if len(candidates) >= target_cap:
                    break
                try:
                    r = creq.get(
                        "https://www.tikwm.com/api/challenge/posts",
                        params={"challenge_id": cid, "count": 25, "cursor": cursor},
                        impersonate="chrome",
                        timeout=12,
                    )
                    if r.status_code != 200 or not r.text.strip().startswith("{"):
                        continue
                    vids = (r.json().get("data") or {}).get("videos") or []
                except Exception as exc:
                    logger.debug(f"Lỗi lấy danh sách video TikTok challenge {cid}: {exc}")
                    continue

                for v in vids:
                    vid = str(v.get("video_id") or "")
                    if not vid or vid in seen_vids:
                        continue
                    seen_vids.add(vid)

                    # Bỏ qua các bài ảnh tĩnh (slideshow/images), quảng cáo hoặc livestream
                    if v.get("images") or v.get("is_ad") or v.get("is_top") or v.get("live_info"):
                        continue

                    duration = float(v.get("duration") or 0.0)
                    if not (self.min_duration <= duration <= self.max_duration):
                        continue

                    music_info = v.get("music_info") or {}
                    sound_id = str(music_info.get("id") or f"tt_sound_{vid}")
                    if self.is_processed(vid, sound_id):
                        continue

                    music_title = str(music_info.get("title") or "")
                    is_orig_flag = music_info.get("original")
                    desc = str(v.get("title") or "")
                    region = str(v.get("region") or "").upper()
                    author_id = (v.get("author") or {}).get("unique_id") or "tiktoker"

                    # 1. Bộ lọc Quốc gia & Ngôn ngữ: Chỉ lấy Trung Quốc & Việt Nam
                    if not self.is_strictly_chinese_or_vietnamese_video(
                        title=desc,
                        music_title=music_title,
                        region=region,
                        author_id=author_id,
                        platform="tiktok",
                    ):
                        continue

                    # 2. Bộ lọc Chủ đề Baby AI / Em bé hài hước (nếu là kênh baby_AI)
                    if is_baby_channel and not self.is_valid_baby_ai_topic(desc, music_title):
                        continue

                    allowed, is_explicit_orig = self.is_original_story_sound(
                        music_title, is_orig_flag, desc
                    )
                    if not allowed:
                        continue

                    create_ts = int(v.get("create_time") or 0)
                    is_recent = (now_ts - create_ts) <= thirty_days_sec if create_ts else False
                    play_cnt = int(v.get("play_count") or 0)
                    digg = int(v.get("digg_count") or 0)
                    share = int(v.get("share_count") or 0)
                    collect = int(
                        v.get("collect_count") or v.get("download_count") or 0
                    )
                    shares_and_saves = share + collect

                    # 3. Bộ lọc Lượng View Cao & Tương Tác Cao (High-View Filter):
                    # Bắt buộc đạt >= 100,000 lượt xem (play_count) hoặc >= 5,000 lượt tim (digg_count)
                    min_req_likes = max(self.min_likes, MIN_HIGH_VIEW_LIKES_TIKTOK)
                    if play_cnt < MIN_HIGH_VIEW_PLAY_COUNT_TIKTOK and digg < min_req_likes:
                        continue
                    if digg < 1000 or shares_and_saves < self.min_shares_saves:
                        continue

                    is_vi = (
                        author_id.lower() in KNOWN_VIETNAMESE_HANDLES
                        or has_vietnamese_diacritics(desc)
                    )

                    candidates.append(
                        {
                            "platform": "tiktok",
                            "genre": kw_label,
                            "video_id": vid,
                            "sound_id": sound_id,
                            "source_url": f"https://www.tiktok.com/@{author_id}/video/{vid}",
                            "duration": duration,
                            "title": desc,
                            "music_title": music_title,
                            "is_original": is_explicit_orig,
                            "is_recent_30d": is_recent,
                            "is_vietnamese": is_vi,
                            "region": region,
                            "play_count": play_cnt,
                            "digg_count": digg,
                            "share_count": share,
                            "collect_count": collect,
                            "shares_and_saves": shares_and_saves,
                            "engagement_score": play_cnt + digg * 5 + shares_and_saves * 15,
                        }
                    )

        candidates.sort(
            key=lambda x: (int(x.get("play_count") or 0), int(x.get("engagement_score") or 0)),
            reverse=True,
        )
        return candidates[:max_candidates]

    def _discover_douyin_by_challenge_ids(
        self, ch_ids: List[str], genre_label: str, max_candidates: int = 40
    ) -> List[Dict[str, Any]]:
        """
        Tìm ứng viên video trên Douyin (Trung Quốc) từ danh sách Challenge IDs:
        - Áp dụng bộ lọc chủ đề nghiêm ngặt cho `baby_AI`.
        - Chỉ lấy video có lượng tương tác / lượt xem cao (`digg_count >= 10,000`, riêng series `#大头菠萝` / `#巨婴宝宝` `>= 1,000`).
        """
        session = requests.Session()
        guest_cookies = fetch_douyin_guest_cookies()
        for domain in (".iesdouyin.com", ".douyin.com"):
            for ck_name, ck_val in guest_cookies.items():
                session.cookies.set(ck_name, ck_val, domain=domain)

        now_ts = int(time.time())
        thirty_days_sec = 30 * 86400
        seen_vids = set()
        candidates: List[Dict[str, Any]] = []
        is_baby_channel = (
            "channel_2" in genre_label
            or "baby" in genre_label.lower()
            or any(k in genre_label for k in ("菠萝", "萌娃", "幼崽", "巨婴", "带娃", "熊孩子"))
        )
        niche_baby_ai_cids = {"1631527180822535", "1619206220311555"}

        target_cap = min(max_candidates, 18)
        for cid in ch_ids:
            if len(candidates) >= target_cap:
                break
            for cursor in (0,):
                if len(candidates) >= target_cap:
                    break
                try:
                    api_url = (
                        f"https://www.iesdouyin.com/web/api/v2/challenge/aweme/"
                        f"?ch_id={cid}&count=20&cursor={cursor}&aid=1128"
                    )
                    r = session.get(
                        api_url,
                        headers={"User-Agent": MOBILE_UA},
                        timeout=12,
                    )
                    if r.status_code != 200:
                        continue
                    aweme_list = r.json().get("aweme_list") or []
                except Exception as exc:
                    logger.debug(f"Lỗi lấy challenge Douyin {cid}: {exc}")
                    continue

                for item in aweme_list:
                    vid = str(item.get("aweme_id") or "")
                    if not vid or vid in seen_vids:
                        continue
                    seen_vids.add(vid)

                    # Bỏ qua bài ghép ảnh tĩnh (images / aweme_type 2, 68, 150), quảng cáo hoặc livestream
                    if (
                        item.get("images")
                        or item.get("aweme_type") in (2, 68, 150)
                        or item.get("is_ads")
                        or item.get("raw_ad_data")
                        or item.get("cell_room")
                    ):
                        continue

                    video_obj = item.get("video") or {}
                    duration = float(video_obj.get("duration") or 0.0) / 1000.0
                    if not (self.min_duration <= duration <= self.max_duration):
                        continue

                    raw_desc = str(item.get("desc") or "")
                    if raw_desc:
                        if not self.is_strictly_chinese_or_vietnamese_video(
                            title=raw_desc, platform="douyin"
                        ):
                            continue
                        if is_baby_channel and not self.is_valid_baby_ai_topic(raw_desc):
                            continue
                        allowed_pre, _ = self.is_original_story_sound("", None, raw_desc)
                        if not allowed_pre:
                            continue

                    if self.is_processed(vid):
                        continue

                    detail = self._fetch_douyin_item_detail(session, vid)
                    if not detail:
                        continue

                    if (
                        detail.get("images")
                        or detail.get("aweme_type") in (2, 68, 150)
                        or detail.get("is_ads")
                    ):
                        continue

                    music_obj = detail.get("music") or {}
                    sound_id = str(music_obj.get("mid") or f"dy_sound_{vid}")
                    if self.is_processed(vid, sound_id):
                        continue

                    music_title = str(music_obj.get("title") or "")
                    desc = str(detail.get("desc") or item.get("desc") or "")

                    if not self.is_strictly_chinese_or_vietnamese_video(
                        title=desc,
                        music_title=music_title,
                        platform="douyin",
                    ):
                        continue

                    if is_baby_channel and not self.is_valid_baby_ai_topic(desc, music_title):
                        continue

                    allowed, is_explicit_orig = self.is_original_story_sound(
                        music_title, None, desc
                    )
                    if not allowed:
                        continue

                    stats = detail.get("statistics") or {}
                    digg = int(stats.get("digg_count") or 0)
                    share = int(stats.get("share_count") or 0)
                    collect = int(stats.get("collect_count") or 0)
                    shares_and_saves = share + collect

                    # Bộ lọc Lượng View & Tương tác cao trên Douyin:
                    # Tối thiểu 10,000 lượt tim (~200k-500k+ views) cho các challenge chung,
                    # hoặc >= 1,000 lượt tim cho series AI Baby chuyên biệt (#大头菠萝 / #巨婴宝宝).
                    min_dy_likes = (
                        1000
                        if str(cid) in niche_baby_ai_cids
                        else max(self.min_likes, MIN_HIGH_VIEW_LIKES_DOUYIN)
                    )
                    if digg < min_dy_likes or shares_and_saves < self.min_shares_saves:
                        continue

                    create_ts = int(detail.get("create_time") or 0)
                    is_recent = (now_ts - create_ts) <= thirty_days_sec if create_ts else False
                    est_plays = digg * 20 + shares_and_saves * 50

                    candidates.append(
                        {
                            "platform": "douyin",
                            "genre": genre_label,
                            "video_id": vid,
                            "sound_id": sound_id,
                            "source_url": f"https://www.douyin.com/video/{vid}",
                            "duration": duration,
                            "title": desc,
                            "music_title": music_title,
                            "is_original": is_explicit_orig,
                            "is_recent_30d": is_recent,
                            "play_count": est_plays,
                            "digg_count": digg,
                            "share_count": share,
                            "collect_count": collect,
                            "shares_and_saves": shares_and_saves,
                            "engagement_score": est_plays + digg * 5 + shares_and_saves * 15,
                        }
                    )

        candidates.sort(
            key=lambda x: int(x.get("engagement_score") or 0),
            reverse=True,
        )
        return candidates[:max_candidates]

    @staticmethod
    def _fetch_douyin_item_detail(
        session: requests.Session, video_id: str
    ) -> Optional[Dict[str, Any]]:
        """Lấy chi tiết `music`, `statistics`, `create_time` của một video Douyin."""
        try:
            url = f"https://www.iesdouyin.com/share/video/{video_id}/"
            resp = session.get(url, headers={"User-Agent": MOBILE_UA}, timeout=12)
            if resp.status_code != 200:
                return None
            match = re.search(
                r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>",
                resp.text,
                re.DOTALL,
            )
            if not match:
                return None
            router_data = json.loads(match.group(1))
            for val in (router_data.get("loaderData") or {}).values():
                if isinstance(val, dict) and "videoInfoRes" in val:
                    item_list = val["videoInfoRes"].get("item_list") or []
                    if item_list:
                        return item_list[0]
        except Exception:
            return None
        return None

    @staticmethod
    def has_video_motion(video_path: Path, min_diff_score: float = 1.8) -> bool:
        """
        Kiểm tra video có chuyển động hình ảnh thực sự hay chỉ là ảnh tĩnh ghép nhạc/thoại.
        Trích xuất nhanh các khung hình vùng trung tâm bằng ffmpeg (1 fps, 160x160 grayscale)
        và đo độ biến thiên pixel trung bình giữa các khung hình liên tiếp.
        """
        try:
            ffmpeg_bin, _ = ensure_ffmpeg()
            cmd = [
                ffmpeg_bin,
                "-v",
                "error",
                "-i",
                str(video_path),
                "-t",
                "15",
                "-vf",
                "fps=1,crop=iw*0.80:ih*0.55:iw*0.10:ih*0.15,scale=160:160",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-",
            ]
            res = subprocess.run(cmd, capture_output=True, check=False, timeout=15)
            raw_bytes = res.stdout
            frame_size = 160 * 160
            num_frames = len(raw_bytes) // frame_size
            if num_frames < 3:
                return True
            frames = np.frombuffer(
                raw_bytes[: num_frames * frame_size], dtype=np.uint8
            ).reshape((num_frames, 160, 160)).astype(np.float32)
            diffs = np.mean(np.abs(frames[1:] - frames[:-1]), axis=(1, 2))
            mean_motion = float(np.mean(diffs))
            return mean_motion >= min_diff_score
        except Exception:
            return True

    def verify_voice_density(
        self, candidate: Dict[str, Any]
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Bộ lọc kiểm tra chuyển động hình ảnh, ngôn ngữ Trung/Việt & mật độ giọng đọc:
        - Tải video và trích xuất audio mẫu.
        - Kiểm tra tiêu đề/metadata chỉ thuộc Trung Quốc hoặc Việt Nam.
        - Kiểm tra chuyển động hình ảnh (`has_video_motion`): Loại bỏ các video chỉ ghép ảnh tĩnh.
        - Chạy `faster-whisper` kiểm tra nhanh:
          + Bắt buộc ngôn ngữ giọng nói (`detected_lang`) phải là Tiếng Trung (`zh`, `yue`, `cmn`) hoặc Tiếng Việt (`vi`).
            Loại bỏ ngay lập tức mọi ngôn ngữ nước ngoài khác (`en`, `es`, `ru`, `id`, `ko`, `ja`, `th`, `hi`, `ar`...).
          + Số lượng câu thoại (`len(segments)`) >= `self.min_segments`.
          + Tổng thời gian có lời thoại chiếm >= `self.min_speech_ratio` độ dài video.
        """
        vid = candidate["video_id"]
        platform = candidate["platform"]
        source_url = candidate["source_url"]

        cand_dir = self.temp_root / f"{platform}_{vid}"
        if cand_dir.exists():
            shutil.rmtree(cand_dir, ignore_errors=True)
        cand_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"[Voice Filter] Đang kiểm tra ứng viên {platform.upper()} ID={vid} "
            f"(thời lượng={candidate['duration']:.1f}s, sound='{candidate['music_title']}', "
            f"view={candidate.get('play_count', 0)}, tim={candidate['digg_count']}, share={candidate['share_count']})..."
        )

        try:
            downloader = VideoDownloader(work_dir=cand_dir)
            dl_info = downloader.download(source_url)
            raw_video_path: Path = dl_info["video_path"]
            audio_path: Path = dl_info["audio_path"]
            dl_title = str(dl_info.get("title") or candidate.get("title") or "")
            if dl_title and not candidate.get("title"):
                candidate["title"] = dl_title

            if dl_title and not self.is_strictly_chinese_or_vietnamese_video(
                title=dl_title,
                music_title=str(candidate.get("music_title") or ""),
                author_id=str(candidate.get("author_handle") or ""),
                platform=platform,
            ):
                logger.info(
                    f"[Language Gate] Loại bỏ {vid}: Tiêu đề '{dl_title[:60]}' không phải Tiếng Trung hoặc Tiếng Việt."
                )
                shutil.rmtree(cand_dir, ignore_errors=True)
                return False, None

            actual_duration = get_media_duration(raw_video_path) or float(
                candidate["duration"]
            )
            is_priority = bool(
                candidate.get("is_seed") or candidate.get("is_channel_video")
            )

            if not (self.min_duration <= actual_duration <= max(self.max_duration, 360.0)):
                logger.info(
                    f"[Voice Filter] Loại bỏ {vid}: thời lượng thực tế {actual_duration:.1f}s "
                    f"nằm ngoài khoảng [{self.min_duration:.0f}s, {max(self.max_duration, 360.0):.0f}s]."
                )
                shutil.rmtree(cand_dir, ignore_errors=True)
                return False, None

            # Nếu video thuộc kênh tiếng Việt hoặc tiêu đề đã là tiếng Việt -> Giữ nguyên 100% không cần chạy Whisper/TTS
            if candidate.get("is_vietnamese") or has_vietnamese_diacritics(dl_title):
                logger.info(
                    f"[Voice Filter] Video {vid} là Tiếng Việt ('vi') có sẵn -> "
                    "Giữ nguyên video & âm thanh gốc 100%."
                )
                transcript_data = {
                    "language": "vi",
                    "segments": [],
                    "original_title": dl_title,
                }
                return True, {
                    "candidate": candidate,
                    "work_dir": cand_dir,
                    "raw_video_path": raw_video_path,
                    "audio_path": audio_path,
                    "actual_duration": actual_duration,
                    "transcript_data": transcript_data,
                    "speech_ratio": 1.0,
                    "spoken_seconds": round(actual_duration, 2),
                    "original_title": dl_title,
                }

            # Kiểm tra chuyển động video cho các video liên quan (không áp dụng cho link mẫu / kênh mẫu)
            if not is_priority and not self.has_video_motion(raw_video_path):
                logger.info(
                    f"[Motion Filter] Loại bỏ {vid}: Video chỉ ghép ảnh tĩnh không có chuyển động khung hình."
                )
                shutil.rmtree(cand_dir, ignore_errors=True)
                return False, None

            transcript_json = cand_dir / "transcript_raw.json"
            transcript_data = self.transcriber.transcribe(
                audio_path=audio_path,
                save_json_path=transcript_json,
            )
            transcript_data["original_title"] = dl_title
            detected_lang = (transcript_data.get("language") or "zh").lower().strip()

            if detected_lang == "vi":
                logger.info(
                    f"[Voice Filter] Whisper xác nhận video {vid} là Tiếng Việt ('vi') -> "
                    "Giữ nguyên video & âm thanh gốc 100%."
                )
                return True, {
                    "candidate": candidate,
                    "work_dir": cand_dir,
                    "raw_video_path": raw_video_path,
                    "audio_path": audio_path,
                    "actual_duration": actual_duration,
                    "transcript_data": transcript_data,
                    "speech_ratio": 1.0,
                    "spoken_seconds": round(actual_duration, 2),
                    "original_title": dl_title,
                }

            # Chặn tuyệt đối mọi ngôn ngữ nước ngoài ngoài Tiếng Trung (zh/yue/cmn) và Tiếng Việt (vi)
            if detected_lang not in ("zh", "yue", "cmn", "vi"):
                logger.info(
                    f"[Language Gate] -> LOẠI BỎ {vid}: Ngôn ngữ âm thanh phát hiện là '{detected_lang}' "
                    "(chỉ cho phép video Trung Quốc 'zh' và Việt Nam 'vi')."
                )
                shutil.rmtree(cand_dir, ignore_errors=True)
                return False, None

            segments = transcript_data.get("segments") or []
            seg_count = len(segments)

            intervals = sorted(
                (max(0.0, float(s["start"])), min(actual_duration, float(s["end"])))
                for s in segments
                if float(s["end"]) > float(s["start"])
            )
            merged_intervals: List[Tuple[float, float]] = []
            for st, en in intervals:
                if not merged_intervals or st > merged_intervals[-1][1]:
                    merged_intervals.append((st, en))
                else:
                    merged_intervals[-1] = (
                        merged_intervals[-1][0],
                        max(merged_intervals[-1][1], en),
                    )

            spoken_seconds = sum(en - st for st, en in merged_intervals)
            speech_ratio = (
                spoken_seconds / actual_duration if actual_duration > 0 else 0.0
            )

            mean_logprob = float(transcript_data.get("mean_avg_logprob", -0.3) or -0.3)
            ocr_coverage = float(transcript_data.get("ocr_coverage", 0.0) or 0.0)
            total_chars = sum(len((s.get("text") or "").strip()) for s in segments)
            avg_chars = total_chars / seg_count if seg_count > 0 else 0.0

            logger.info(
                f"[Voice Filter] Kết quả đo {vid}: lang={detected_lang}, số câu={seg_count}, "
                f"độ dài TB={avg_chars:.1f} ký tự/câu, "
                f"thời gian thoại={spoken_seconds:.1f}s/{actual_duration:.1f}s ({speech_ratio:.1%}), "
                f"logprob={mean_logprob:.2f}, ocr={ocr_coverage:.0%}"
            )

            if is_priority:
                if seg_count < 1:
                    logger.info(f"[Voice Filter] Loại bỏ {vid}: không tìm thấy câu thoại nào để dịch.")
                    shutil.rmtree(cand_dir, ignore_errors=True)
                    return False, None
            else:
                if seg_count < self.min_segments or speech_ratio < self.min_speech_ratio:
                    logger.info(
                        f"[Voice Filter] -> LOẠI BỎ {vid} (không đạt chuẩn mật độ câu thoại)."
                    )
                    shutil.rmtree(cand_dir, ignore_errors=True)
                    return False, None

            logger.info(
                f"[Voice Filter] -> ĐẠT CHUẨN TRUNG/VIỆT VIEW CAO! Chọn video {vid} để dịch trực tiếp sang tiếng Việt & lồng tiếng."
            )
            return True, {
                "candidate": candidate,
                "work_dir": cand_dir,
                "raw_video_path": raw_video_path,
                "audio_path": audio_path,
                "actual_duration": actual_duration,
                "transcript_data": transcript_data,
                "speech_ratio": round(speech_ratio, 4),
                "spoken_seconds": round(spoken_seconds, 2),
                "original_title": dl_title,
            }
        except Exception as exc:
            logger.warning(f"[Voice Filter] Lỗi khi kiểm tra ứng viên {vid}: {exc}")
            shutil.rmtree(cand_dir, ignore_errors=True)
            return False, None

    @staticmethod
    def clear_macl_xattr(file_path: Path) -> None:
        """Xóa thuộc tính mở rộng `com.apple.macl` khi mới xuất video vào `ready/` để tránh nhận diện nhầm."""
        try:
            subprocess.run(
                ["xattr", "-d", "com.apple.macl", str(file_path)],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except Exception:
            pass

    @staticmethod
    def has_browser_upload_macl(file_path: Path) -> bool:
        """
        Phát hiện tự động trên macOS khi người dùng đã chọn file video từ `output/channel_X/ready/`
        trong hộp thoại Upload File của trình duyệt (Chrome/Safari/Brave/Arc `NSOpenPanel`)
        để đăng lên TikTok (`com.apple.macl` có cờ `08 00`).
        """
        try:
            res = subprocess.run(
                ["xattr", "-px", "com.apple.macl", str(file_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return False
            raw_bytes = bytes.fromhex("".join(res.stdout.split()))
            for idx in range(0, len(raw_bytes), 18):
                chunk = raw_bytes[idx : idx + 18]
                if len(chunk) == 18 and chunk[:2] == b"\x08\x00" and any(chunk[2:18]):
                    return True
        except Exception:
            return False
        return False

    def purge_foreign_and_offtopic_ready_videos(self) -> List[str]:
        """
        Tự động dọn dẹp khỏi `output/*/ready/` mọi video nước ngoài (tiếng Anh, Tây Ban Nha...)
        hoặc video lệch chủ đề `baby_AI` đã bị cào từ bộ từ khóa tiếng Anh cũ trước ngày 2026-10-28:
        1. Kiểm tra `transcript_raw.json` trong `workspace_temp`: nếu `language` không thuộc `('vi', 'zh', 'yue', 'cmn')` -> Xóa.
        2. Kiểm tra `output/danh_sach_video.csv`: nếu `source_url` chứa `guangzhoujianglan_mx` hoặc `caption` chứa câu tiếng Anh chưa dịch -> Xóa.
        3. Đối với `baby_AI` và `video_AI_hay`: xóa toàn bộ các file `2026102[0-7]_*_tiktok_story_*.mp4` trong `ready/`
           (vì trước 2026-10-28 hai kênh này chỉ cấu hình hashtag tiếng Anh `#familycomedy`, `#relatablehumor`, `#skitcomedy`, `#aianimation`),
           và xóa các file `2026102[0-7]_*_douyin_story_*.mp4` trong `baby_AI/ready/` không thuộc bộ seed `#大头菠萝`.
        """
        purged: List[str] = []
        valid_baby_ai_douyin_seeds = {
            "7638714267462561417",
            "7634246231678587978",
            "7653148698738335945",
            "7640576010862205938",
            "7649090399869777051",
            "7659099729062903625",
            "7254524593694608651",
            "7212859447947889959",
        }
        english_sentence_re = re.compile(
            r"\b(?:did you know|that's how|these are|when i|keep up|black belt|karate|dentist|knock your|whole kitchen|storytime|reddit)\b",
            re.IGNORECASE,
        )

        csv_meta_by_name: Dict[str, Tuple[str, str]] = {}
        if self.csv_path.exists():
            try:
                with open(self.csv_path, mode="r", newline="", encoding="utf-8") as rf:
                    for row in csv.DictReader(rf):
                        fn = Path((row.get("filename") or "").strip()).name
                        s_url = (row.get("source_url") or "").strip()
                        cap = (row.get("caption") or "").strip()
                        if fn:
                            csv_meta_by_name[fn] = (s_url, cap)
            except Exception:
                pass

        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
            ready_dir = self.output_root / ch_folder / "ready"
            if not ready_dir.exists():
                continue

            for mp4 in sorted(ready_dir.glob("*.mp4")):
                if not mp4.is_file():
                    continue
                fname = mp4.name
                m_sid = re.search(r"(tiktok|douyin)_story_(\d+)", mp4.stem, re.IGNORECASE)
                plat = m_sid.group(1).lower() if m_sid else ""
                sid = m_sid.group(2) if m_sid else ""
                s_url, cap = csv_meta_by_name.get(fname, ("", ""))

                reason = ""
                if "guangzhoujianglan_mx" in s_url.lower():
                    reason = "kênh tiếng Tây Ban Nha (@guangzhoujianglan_mx)"
                elif cap and english_sentence_re.search(cap):
                    reason = "caption chứa lời thoại tiếng Anh nước ngoài"
                elif ch_folder in ("baby_AI", "video_AI_hay") and plat == "tiktok" and re.match(r"^2026102[0-7]_", fname):
                    reason = f"video TikTok tiếng Anh cũ trước 28/10 trong {ch_folder}"
                elif ch_folder == "baby_AI" and plat == "douyin" and re.match(r"^2026102[0-7]_", fname) and sid not in valid_baby_ai_douyin_seeds:
                    reason = "video Douyin hài người lớn cũ không thuộc chủ đề Baby AI"
                elif (plat == "douyin" or ch_folder in ("baby_AI", "video_AI_hay")) and fname[:15] < "20261028_155500":
                    reason = "video lồng tiếng bản cũ trước khi nâng cấp Word-Timestamp Sync & Anti-Repeat OCR (15:55 28/10)"

                if not reason and sid:
                    for tr_dir in (
                        self.temp_root / f"{plat}_{sid}",
                        self.project_root / "workspace_temp" / f"{plat}_{sid}",
                    ):
                        tr_file = tr_dir / "transcript_raw.json"
                        if tr_file.exists():
                            try:
                                tr_json = json.loads(tr_file.read_text(encoding="utf-8"))
                                lang = str(tr_json.get("language") or "zh").lower().strip()
                                if lang not in ("vi", "zh", "yue", "cmn"):
                                    reason = f"ngôn ngữ âm thanh ngoại ngữ '{lang}' (không phải Trung/Việt)"
                                    break
                            except Exception:
                                pass

                if reason:
                    mp4.unlink(missing_ok=True)
                    purged.append(f"{ch_folder}/ready/{fname}")
                    # Xóa luôn ID khỏi story_audio_history.json để hệ thống có thể lồng tiếng lại bằng thuật toán mới chuẩn khớp 100%
                    if sid and self.history_path.exists():
                        try:
                            h_data = json.loads(self.history_path.read_text(encoding="utf-8"))
                            v_ids = h_data.get("video_ids") or []
                            if sid in v_ids:
                                h_data["video_ids"] = [x for x in v_ids if x != sid]
                                self.history_path.write_text(
                                    json.dumps(h_data, ensure_ascii=False, indent=2),
                                    encoding="utf-8",
                                )
                        except Exception:
                            pass
                    logger.info(
                        f"[Language & Topic Purge] Đã xóa video không đạt chuẩn khỏi {ch_folder}/ready/{fname} ({reason})."
                    )

        return purged

    def auto_sync_posted_videos(self, min_age_seconds: float = 35.0) -> List[Dict[str, Any]]:
        """
        Tự động đồng bộ trạng thái `READY` <-> `POSTED` trên toàn bộ 4 kênh (`channel_1` .. `channel_4`):
        0. Dọn dẹp ngay mọi video nước ngoài (ngoài Trung Quốc & Việt Nam) hoặc lệch chủ đề `baby_AI` trong `ready/`.
        1. Loại bỏ/di chuyển ngay mọi video trong `ready/` có `story_id` hoặc `filename` đã từng được đăng
           (`_collect_all_known_posted_story_ids_and_files`), hoặc bị trùng lặp `story_id` ngay trong cùng `ready/`.
        2. Phát hiện các video trong `output/channel_X/ready/` đã được chọn upload lên TikTok qua trình duyệt
           (`has_browser_upload_macl` == True và đã qua `min_age_seconds` để trình duyệt đọc xong file)
           -> Tự động chuyển sang `output/channel_X/posted/` và cập nhật `output/danh_sach_video.csv` (`POSTED`).
        3. Đồng bộ 2 chiều giữa thư mục ổ cứng (`ready/`, `posted/`) và file `output/danh_sach_video.csv`.
        """
        self.ensure_channel_directories()
        self.purge_foreign_and_offtopic_ready_videos()
        auto_moved: List[Dict[str, Any]] = []
        now_ts = time.time()
        posted_ids, posted_filenames = self._collect_all_known_posted_story_ids_and_files()

        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
            ready_dir = self.output_root / ch_folder / "ready"
            posted_dir = self.output_root / ch_folder / "posted"
            seen_ready_story_ids: Set[str] = set()

            for mp4 in sorted(ready_dir.glob("*.mp4")):
                if not mp4.is_file():
                    continue
                m_sid = re.search(r"(?:tiktok|douyin)_story_(\d+)", mp4.stem, re.IGNORECASE)
                sid = m_sid.group(1) if m_sid else ""

                # 1. Chặn đứng đăng trùng: nếu video_id hoặc filename đã nằm trong danh sách ĐÃ ĐĂNG
                if mp4.name in posted_filenames or (sid and sid in posted_ids):
                    dest_file = posted_dir / mp4.name
                    has_same_sid_in_posted = bool(
                        sid and any(sid in p.name for p in posted_dir.glob("*.mp4"))
                    )
                    if has_same_sid_in_posted and not dest_file.exists():
                        mp4.unlink(missing_ok=True)
                        logger.info(
                            f"[Anti-Duplicate Guard] Đã xóa bản sao video trùng story_id={sid} trong {ch_folder}/ready/{mp4.name} (vì đã có bản đã đăng trong posted/)."
                        )
                    else:
                        if dest_file.exists():
                            dest_file.unlink()
                        shutil.move(str(mp4), str(dest_file))
                        logger.info(
                            f"[Anti-Duplicate Guard] Phát hiện video trong {ch_folder}/ready/{mp4.name} (story_id={sid}) ĐÃ ĐƯỢC ĐĂNG TRƯỚC ĐÓ -> Tự động chuyển sang posted/!"
                        )
                    continue

                # 2. Chặn trùng lặp ngay trong cùng thư mục ready/ (2 file khác timestamp nhưng cùng story_id)
                if sid:
                    if sid in seen_ready_story_ids:
                        mp4.unlink(missing_ok=True)
                        logger.info(
                            f"[Anti-Duplicate Guard] Đã xóa file trùng lặp story_id={sid} ngay trong {ch_folder}/ready/{mp4.name}."
                        )
                        continue
                    seen_ready_story_ids.add(sid)

                if self.has_browser_upload_macl(mp4):
                    st = mp4.stat()
                    if (now_ts - st.st_ctime) >= min_age_seconds:
                        posted_time_str = datetime.fromtimestamp(st.st_ctime).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        dest_file = posted_dir / mp4.name
                        if dest_file.exists():
                            dest_file.unlink()
                        shutil.move(str(mp4), str(dest_file))
                        if sid:
                            posted_ids.add(sid)
                        posted_filenames.add(mp4.name)
                        logger.info(
                            f"[Auto-Sync Posted] Phát hiện video đã được chọn upload lên TikTok: "
                            f"{ch_folder}/ready/{mp4.name} -> Đã chuyển sang {ch_folder}/posted/{mp4.name} "
                            f"(posted_at={posted_time_str})"
                        )
                        auto_moved.append(
                            {
                                "channel": ch_folder,
                                "filename": f"{ch_folder}/posted/{mp4.name}",
                                "moved_to": str(dest_file),
                                "status": "POSTED",
                                "posted_at": posted_time_str,
                            }
                        )

        self._upgrade_master_csv_schema()
        return auto_moved

    @staticmethod
    def is_raw_filename_caption(text: str, stem: str = "") -> bool:
        """
        Kiểm tra xem chuỗi `text` có đang bị chứa tên file thô / dãy số ID
        (ví dụ: `20261025_152239_tiktok_story_7696574490729303310`) hoặc chữ Trung/Anh chưa dịch
        thay vì kịch bản mô tả tiếng Việt hoàn chỉnh hay không.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return True
        if stem and cleaned == stem.strip():
            return True
        if (
            re.search(r"\b20\d{6}_\d{6}\b", cleaned)
            or "tiktok_story" in cleaned.lower()
            or "douyin_story" in cleaned.lower()
            or re.match(r"^[\d_\-\s\.mp4]+$", cleaned.lower())
            or bool(re.search(r"[\u4e00-\u9fff]", cleaned))
        ):
            return True
        return False

    def recover_contextual_caption_and_hashtags(
        self, ch_folder: str, filename_or_stem: str, source_url: str = ""
    ) -> Tuple[str, str, str]:
        """
        Tự động khôi phục / sinh mới Kịch bản ngắn tiếng Việt theo đúng ngữ cảnh video + bộ Hashtag Hot Search
        cho các file video trong `ready/` hoặc `posted/` (kể cả khi `danh_sach_video.csv` bị thiếu dòng hoặc mất cột caption).
        Tuyệt đối không bao giờ trả về tên file hay dãy số ID.
        """
        stem = Path(filename_or_stem).stem
        m = re.search(r"(tiktok|douyin)_story_(\d+)", stem, re.IGNORECASE)
        platform = (m.group(1).lower() if m else "tiktok")
        video_id = (m.group(2) if m else "")
        if not video_id and source_url:
            m_url = re.search(r"/video/(\d+)", source_url)
            if m_url:
                video_id = m_url.group(1)

        resolved_url = (source_url or "").strip()
        if not resolved_url and video_id:
            if platform == "douyin":
                resolved_url = f"https://www.douyin.com/video/{video_id}"
            else:
                resolved_url = f"https://www.tiktok.com/@tiktok/video/{video_id}"

        original_title = ""
        segments: List[Dict[str, Any]] = []

        # 1. Kiểm tra bản lưu tạm trong temp/ nếu có
        if video_id:
            for p_try in (platform, "tiktok", "douyin"):
                tr_path = self.temp_root / f"{p_try}_{video_id}" / "transcript_raw.json"
                if tr_path.exists():
                    try:
                        tr_data = json.loads(tr_path.read_text(encoding="utf-8"))
                        original_title = str(tr_data.get("original_title") or "").strip()
                        segments = tr_data.get("segments") or []
                        if original_title or segments:
                            break
                    except Exception:
                        pass

        # 2. Nếu chưa có tiêu đề gốc, truy vấn nhanh API tikwm / douyin theo video_id (timeout ngắn 5s)
        if not original_title and video_id:
            if platform == "tiktok":
                try:
                    resp = requests.get(
                        "https://www.tikwm.com/api/",
                        params={"url": f"https://www.tiktok.com/@tiktok/video/{video_id}"},
                        headers={"User-Agent": DESKTOP_UA},
                        timeout=5,
                    )
                    if resp.status_code == 200:
                        data = (resp.json() or {}).get("data") or {}
                        original_title = str(data.get("title") or "").strip()
                        author_uid = str((data.get("author") or {}).get("unique_id") or "").strip()
                        if author_uid and "@tiktok/" in resolved_url:
                            resolved_url = f"https://www.tiktok.com/@{author_uid}/video/{video_id}"
                except Exception:
                    pass
            elif platform == "douyin":
                try:
                    item = self._fetch_douyin_item_detail(requests.Session(), video_id)
                    if item:
                        original_title = str(item.get("desc") or "").strip()
                except Exception:
                    pass

        # Loại bỏ mọi dấu vết tên file nếu original_title vô tình chứa dãy số, hoặc nếu tiêu đề là tiếng Anh rác không thuộc văn cảnh Việt/Trung của kênh
        if self.is_raw_filename_caption(original_title, stem):
            original_title = ""
        elif original_title and not (
            has_vietnamese_diacritics(original_title)
            or bool(re.search(r"[\u4e00-\u9fff]", original_title))
        ):
            original_title = ""

        # 3. Nếu offline hoặc video gốc không có tiêu đề chữ, dùng chủ đề chuẩn theo ngách kênh + biến thiên theo video_id
        if not original_title and not segments:
            fallback_topics = {
                "tu_chan_gioi": [
                    "Phàm nhân nghịch thiên cải mệnh thức tỉnh huyết mạch thượng cổ tại Tu Chân Giới",
                    "Tưởng là phế vật dưới đáy tông môn ai ngờ ẩn giấu thực lực chấn động thiên địa",
                    "Hành trình tu tiên vượt mọi đại kiếp bước lên đỉnh cao vạn giới",
                    "Màn thức tỉnh linh căn và phản kích khiến toàn bộ cường giả phải khiếp sợ",
                ],
                "audio_dem_khuyen": [
                    "Biến cố bất ngờ lúc nửa đêm và sự thật chấn động phía sau cuộc hôn nhân",
                    "Góc khuất tình cảm và màn đáp trả cực gắt khiến trà xanh không kịp trở tay",
                    "Lòng người khó đoán chỉ đến khi biến cố ập tới mới biết ai thật lòng",
                    "Cú quay xe nghẹt thở ở phút cuối của câu chuyện đêm khuya",
                ],
                "video_AI_hay": [
                    "Thú cưng thành tinh và màn phiêu lưu kỳ ảo không thể rời mắt",
                    "Phim ngắn hoạt hình AI siêu cuốn với tình tiết bất ngờ ở giây cuối cùng",
                    "Câu chuyện hoạt hình AI chữa lành khiến hàng triệu người xem đi xem lại",
                    "Thế giới AI kỳ ảo với màn biến hình đỉnh cao",
                ],
                "baby_AI": [
                    "Nhật ký gia đình siêu lầy và màn đối đáp cười xỉu của em bé lém lỉnh",
                    "Tình huống dở khóc dở cười của hai mẹ con khiến cả nhà bật cười",
                    "Pha xử lý đi vào lòng đất của em bé tinh nghịch trong gia đình",
                    "Khoảnh khắc đáng yêu và hài hước nhất trong ngày của bé",
                ],
            }
            topic_list = fallback_topics.get(ch_folder, fallback_topics["video_AI_hay"])
            idx = int(video_id[-4:]) % len(topic_list) if (video_id and video_id[-4:].isdigit()) else 0
            original_title = topic_list[idx]

        translator = SubtitleTranslator()
        src_lang = "vi" if has_vietnamese_diacritics(original_title) else "zh"
        meta = translator.generate_story_metadata(
            segments=segments,
            platform=platform,
            genre=ch_folder,
            original_title=original_title,
            src_lang=src_lang,
        )
        caption = (meta.get("caption") or "").strip()
        hashtags = (meta.get("hashtags") or "").strip()
        if not hashtags:
            hashtags = "#xuhuong #xuhuongtiktok #fyp #viral #tiktokvn"
        return caption, hashtags, resolved_url

    def _upgrade_master_csv_schema(self) -> None:
        """
        Đảm bảo `output/danh_sach_video.csv` luôn có đúng 7 cột chuẩn:
        `channel,filename,source_url,caption,hashtags,status,posted_at`
        và tự động đồng bộ 2 chiều giữa ổ cứng (`ready/` <-> `posted/`) và CSV cho cả 4 kênh.
        """
        expected_header = [
            "channel",
            "filename",
            "source_url",
            "caption",
            "hashtags",
            "status",
            "posted_at",
        ]
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            return

        with open(self.csv_path, mode="r", newline="", encoding="utf-8") as rf:
            rows = list(csv.reader(rf))
        if not rows:
            return

        needs_rewrite = rows[0] != expected_header
        upgraded_rows = [expected_header]
        seen_basenames = set()

        for r in rows[1:]:
            if not r:
                continue
            if len(r) == 5:
                ch, fn, url, cap, tags = "general", r[0], r[1], r[2], r[3]
                st, pat = r[4], ""
            elif len(r) == 6:
                ch, fn, url, cap, tags, st = r[0], r[1], r[2], r[3], r[4], r[5]
                pat = ""
            else:
                ch, fn, url, cap, tags, st, pat = r[0], r[1], r[2], r[3], r[4], r[5], r[6]

            st_clean = st.strip()
            # Chuẩn hóa trạng thái READY / POSTED
            if st_clean.lower() in ("da_dang", "posted", "done", "1", "x", "đã đăng"):
                if st != "POSTED":
                    needs_rewrite = True
                st = "POSTED"
            elif st_clean.lower() in ("chua_dang", "ready", "", "0", "chờ đăng"):
                if st != "READY":
                    needs_rewrite = True
                st = "READY"

            # Chuẩn hóa tên kênh (chuyển đổi tự động từ channel_1..4 sang tên mới)
            ch_clean = ch.strip()
            mapped_ch = LEGACY_CHANNEL_FOLDER_MAP.get(
                ch_clean, LEGACY_CHANNEL_FOLDER_MAP.get(ch_clean.lower(), ch_clean)
            )
            if mapped_ch != ch:
                ch = mapped_ch
                needs_rewrite = True

            # Chuẩn hóa đường dẫn filename theo thư mục ready/ hoặc posted/ và đồng bộ 2 chiều với ổ cứng
            parts = Path(fn).parts
            basename = Path(fn).name
            seen_basenames.add(basename)
            valid_folders = {p["folder_name"] for p in CHANNEL_PROFILES.values()}

            if ch in valid_folders:
                ready_p = self.output_root / ch / "ready" / basename
                posted_p = self.output_root / ch / "posted" / basename

                # Nếu người dùng đánh dấu POSTED trong CSV mà file vẫn nằm ở ready/ -> tự động di chuyển sang posted/
                if st == "POSTED" and ready_p.exists() and not posted_p.exists():
                    posted_p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(ready_p), str(posted_p))
                    needs_rewrite = True

                if posted_p.exists():
                    new_fn = f"{ch}/posted/{basename}"
                    if fn != new_fn or st != "POSTED" or not pat:
                        fn = new_fn
                        st = "POSTED"
                        if not pat:
                            pat = datetime.fromtimestamp(posted_p.stat().st_ctime).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            )
                        needs_rewrite = True
                elif ready_p.exists():
                    new_fn = f"{ch}/ready/{basename}"
                    if fn != new_fn or st != "READY":
                        fn = new_fn
                        st = "READY"
                        pat = ""
                        needs_rewrite = True
                else:
                    # File không còn tồn tại trong cả ready/ lẫn posted/ -> loại bỏ dòng rác khỏi CSV
                    needs_rewrite = True
                    continue
            elif len(parts) == 2 and parts[0] in LEGACY_CHANNEL_FOLDER_MAP:
                sub = "posted" if st == "POSTED" else "ready"
                mapped_p0 = LEGACY_CHANNEL_FOLDER_MAP[parts[0]]
                new_fn = f"{mapped_p0}/{sub}/{parts[1]}"
                if new_fn != fn:
                    fn = new_fn
                    needs_rewrite = True

            # Tự động sửa lại nếu cột caption đang bị ghi tên file/dãy số hoặc thiếu hashtags
            if self.is_raw_filename_caption(cap, Path(basename).stem) or not (tags or "").strip():
                rec_cap, rec_tags, rec_url = self.recover_contextual_caption_and_hashtags(
                    ch, basename, url
                )
                if self.is_raw_filename_caption(cap, Path(basename).stem):
                    cap = rec_cap
                if not (tags or "").strip():
                    tags = rec_tags
                if not (url or "").strip() and rec_url:
                    url = rec_url
                needs_rewrite = True

            upgraded_rows.append([ch, fn, url, cap, tags, st, pat])

        # Quét bổ sung các file .mp4 có trên ổ cứng (ready/ hoặc posted/) nhưng chưa có trong CSV
        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
            for sub_name, st_label in (("ready", "READY"), ("posted", "POSTED")):
                sub_dir = self.output_root / ch_folder / sub_name
                if not sub_dir.exists():
                    continue
                for mp4 in sorted(sub_dir.glob("*.mp4")):
                    if not mp4.is_file() or mp4.name in seen_basenames:
                        continue
                    seen_basenames.add(mp4.name)
                    rel_fn = f"{ch_folder}/{sub_name}/{mp4.name}"
                    pat_str = (
                        datetime.fromtimestamp(mp4.stat().st_ctime).strftime("%Y-%m-%d %H:%M:%S")
                        if st_label == "POSTED"
                        else ""
                    )
                    rec_cap, rec_tags, rec_url = self.recover_contextual_caption_and_hashtags(
                        ch_folder, mp4.name
                    )
                    upgraded_rows.append(
                        [ch_folder, rel_fn, rec_url, rec_cap, rec_tags, st_label, pat_str]
                    )
                    needs_rewrite = True

        if needs_rewrite:
            with open(self.csv_path, mode="w", newline="", encoding="utf-8") as wf:
                csv.writer(wf).writerows(upgraded_rows)
            logger.info(f"Đã đồng bộ Master CSV ({self.csv_path}) với thư mục ready/ và posted/ của 4 kênh.")

    def append_to_master_csv(
        self,
        filename: str,
        source_url: str,
        caption: str,
        hashtags: str,
        status: str = "READY",
        channel: str = "general",
        posted_at: str = "",
    ) -> Path:
        """
        Ghi / append thông tin video đã hoàn thiện vào Master List `output/danh_sach_video.csv`
        với các cột: `channel,filename,source_url,caption,hashtags,status,posted_at`.
        """
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        expected_header = [
            "channel",
            "filename",
            "source_url",
            "caption",
            "hashtags",
            "status",
            "posted_at",
        ]

        self._upgrade_master_csv_schema()
        file_exists = self.csv_path.exists() and self.csv_path.stat().st_size > 0

        norm_status = "POSTED" if status.upper() == "POSTED" else "READY"
        with open(self.csv_path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(expected_header)
            writer.writerow(
                [channel, filename, source_url, caption, hashtags, norm_status, posted_at]
            )

        logger.info(
            f"Đã cập nhật Master List CSV: {self.csv_path} "
            f"(channel={channel}, file={filename}, status={norm_status})"
        )
        return self.csv_path

    def mark_video_posted(self, identifier: str) -> Dict[str, Any]:
        """
        Đánh dấu một (hoặc nhiều) video đã được đăng lên TikTok (`--mark-posted <đường_dẫn_hoặc_tên_file_hoặc_id>`):
        1. Hỗ trợ cả đường dẫn tuyệt đối (`/Users/.../output/channel_3/ready/xxx.mp4`), đường dẫn tương đối,
           tên file (`xxx.mp4`), hoặc ID video (`7682055125342424341`).
        2. Tự động di chuyển file từ `output/channel_X/ready/` sang `output/channel_X/posted/`.
        3. Cập nhật dòng tương ứng trong `output/danh_sach_video.csv` thành `status = POSTED`,
           cập nhật đường dẫn `filename`, và ghi thời gian hiện tại vào cột `posted_at`.
        4. Đồng thời kích hoạt `auto_sync_posted_videos()` để đồng bộ toàn bộ 4 kênh.
        """
        raw_ident = str(identifier).strip()
        if not raw_ident:
            raise ValueError("Vui lòng truyền đường dẫn file, tên file hoặc video ID hợp lệ cho --mark-posted.")

        # Hỗ trợ truyền nhiều file/ID cách nhau bằng dấu phẩy
        if "," in raw_ident:
            parts = [p.strip() for p in raw_ident.split(",") if p.strip()]
            if len(parts) > 1:
                results = [self.mark_video_posted(p) for p in parts]
                return {"batch_marked": len(results), "items": results}

        self.ensure_channel_directories()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ident_name = Path(raw_ident).name
        ident_stem = Path(raw_ident).stem

        matched_ready_file: Optional[Path] = None
        matched_channel_folder: Optional[str] = None

        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
            ready_dir = self.output_root / ch_folder / "ready"
            for mp4 in sorted(ready_dir.glob("*.mp4")):
                if (
                    raw_ident == str(mp4)
                    or raw_ident == str(mp4.resolve())
                    or ident_name == mp4.name
                    or ident_stem == mp4.stem
                    or raw_ident in mp4.name
                    or ident_name in mp4.name
                    or f"{ch_folder}/ready/{mp4.name}" == raw_ident
                ):
                    matched_ready_file = mp4
                    matched_channel_folder = ch_folder
                    break
            if matched_ready_file:
                break

        # Nếu file đã nằm trong posted/ từ trước, kiểm tra để đồng bộ CSV
        already_posted_file: Optional[Path] = None
        if not matched_ready_file:
            for profile in CHANNEL_PROFILES.values():
                ch_folder = profile["folder_name"]
                posted_dir = self.output_root / ch_folder / "posted"
                for mp4 in sorted(posted_dir.glob("*.mp4")):
                    if (
                        raw_ident == str(mp4)
                        or raw_ident == str(mp4.resolve())
                        or ident_name == mp4.name
                        or ident_stem == mp4.stem
                        or raw_ident in mp4.name
                        or ident_name in mp4.name
                    ):
                        already_posted_file = mp4
                        matched_channel_folder = ch_folder
                        break
                if already_posted_file:
                    break

        if not matched_ready_file and not already_posted_file:
            raise FileNotFoundError(
                f"Không tìm thấy video nào khớp với '{raw_ident}' trong các thư mục output/channel_X/ready/."
            )

        if matched_ready_file and matched_channel_folder:
            posted_dir = self.output_root / matched_channel_folder / "posted"
            posted_dir.mkdir(parents=True, exist_ok=True)
            dest_file = posted_dir / matched_ready_file.name
            if dest_file.exists():
                dest_file.unlink()
            shutil.move(str(matched_ready_file), str(dest_file))
            target_basename = dest_file.name
        else:
            dest_file = already_posted_file  # type: ignore[assignment]
            target_basename = dest_file.name

        new_rel_filename = f"{matched_channel_folder}/posted/{target_basename}"
        updated_csv_rows = 0

        if self.csv_path.exists() and self.csv_path.stat().st_size > 0:
            with open(self.csv_path, mode="r", newline="", encoding="utf-8") as rf:
                rows = list(csv.reader(rf))
            header = rows[0] if rows else [
                "channel",
                "filename",
                "source_url",
                "caption",
                "hashtags",
                "status",
                "posted_at",
            ]
            new_rows = [header]
            for r in rows[1:]:
                if len(r) < 7:
                    r = r + [""] * (7 - len(r))
                row_fn = r[1]
                row_url = r[2]
                if (
                    target_basename in row_fn
                    or ident_name in row_fn
                    or (raw_ident.isdigit() and raw_ident in row_url)
                ):
                    r[1] = new_rel_filename
                    r[5] = "POSTED"
                    r[6] = now_str
                    updated_csv_rows += 1
                new_rows.append(r[:7])

            with open(self.csv_path, mode="w", newline="", encoding="utf-8") as wf:
                csv.writer(wf).writerows(new_rows)

        # Đồng bộ luôn các kênh còn lại (nếu có video đã chọn upload qua trình duyệt hoặc chỉnh CSV)
        auto_synced_others = self.auto_sync_posted_videos()

        logger.info(
            f"Đã đánh dấu POSTED thành công: {dest_file} | posted_at={now_str} "
            f"(đã cập nhật {updated_csv_rows} dòng trong CSV, tự động đồng bộ thêm {len(auto_synced_others)} video)"
        )
        return {
            "channel": matched_channel_folder,
            "filename": new_rel_filename,
            "moved_to": str(dest_file),
            "status": "POSTED",
            "posted_at": now_str,
            "csv_rows_updated": updated_csv_rows,
            "auto_synced_other_channels": auto_synced_others,
        }

    def get_inventory_status(self) -> Dict[str, Any]:
        """
        Tự động đồng bộ trạng thái `READY` <-> `POSTED` trên toàn bộ 4 kênh
        rồi thống kê số lượng video đang chờ đăng (`ready/`) và đã đăng (`posted/`)
        của từng kênh (`channel_1` .. `channel_4`).
        """
        self.ensure_channel_directories()
        auto_synced = self.auto_sync_posted_videos()
        channels_stats: List[Dict[str, Any]] = []
        total_ready = 0
        total_posted = 0

        for profile in CHANNEL_PROFILES.values():
            ch_folder = profile["folder_name"]
            ready_dir = self.output_root / ch_folder / "ready"
            posted_dir = self.output_root / ch_folder / "posted"
            ready_files = sorted(f.name for f in ready_dir.glob("*.mp4") if f.is_file())
            posted_files = sorted(f.name for f in posted_dir.glob("*.mp4") if f.is_file())
            r_cnt = len(ready_files)
            p_cnt = len(posted_files)
            total_ready += r_cnt
            total_posted += p_cnt
            channels_stats.append(
                {
                    "channel_id": profile["channel_id"],
                    "channel": ch_folder,
                    "profile_key": profile["profile_key"],
                    "description": profile["description"],
                    "ready_count": r_cnt,
                    "posted_count": p_cnt,
                    "total_count": r_cnt + p_cnt,
                    "ready_files": ready_files,
                    "posted_files": posted_files,
                }
            )

        return {
            "channels": channels_stats,
            "total_ready": total_ready,
            "total_posted": total_posted,
            "total_videos": total_ready + total_posted,
            "auto_synced": auto_synced,
            "csv_path": str(self.csv_path),
        }

    def format_inventory_status_table(self) -> str:
        """Tạo bảng thống kê текстовый (ASCII Table) đẹp mắt cho lệnh `--status`."""
        inv = self.get_inventory_status()
        lines = [
            "=" * 100,
            "THỐNG KÊ TỒN KHO VIDEO TIKTOK THEO KÊNH (READY / POSTED QUEUE)",
            "=" * 100,
            f"{'KÊNH':<18} | {'CHỦ ĐỀ NGÁCH (PROFILE)':<26} | {'READY (CHỜ ĐĂNG)':>17} | {'POSTED (ĐÃ ĐĂNG)':>17} | {'TỔNG CỘNG':>10}",
            "-" * 100,
        ]
        for ch in inv["channels"]:
            lines.append(
                f"{ch['channel']:<18} | {ch['profile_key']:<26} | {ch['ready_count']:>17} | {ch['posted_count']:>17} | {ch['total_count']:>10}"
            )
        lines.extend(
            [
                "-" * 100,
                f"{'TỔNG KẾT':<18} | {'Toàn bộ 4 kênh TikTok':<26} | {inv['total_ready']:>17} | {inv['total_posted']:>17} | {inv['total_videos']:>10}",
                "=" * 100,
                f"Master CSV: {inv['csv_path']}",
            ]
        )
        return "\n".join(lines)


def _hot_patch_live_crawler() -> None:
    """
    Khi `importlib.reload(_cr_mod)` chạy trên tiến trình 24/7 (máy Intel Mac hoặc Apple Silicon),
    tự động reload luôn `transcriber`, `translator`, `tts_engine`, `composer` và cập nhật
    `StoryAudioCrawler`, `AudioTranscriber`, `SubtitleTranslator`, `TTSEngine`, `VideoComposer`
    vào `sys.modules['__main__']` / `tiktok_pipeline.main` và vá trực tiếp các class cũ đang chạy trong RAM.
    """
    import importlib
    import sys

    reloaded_classes: Dict[str, Any] = {"StoryAudioCrawler": StoryAudioCrawler}
    sub_specs = [
        ("tiktok_pipeline.transcriber", "AudioTranscriber"),
        ("tiktok_pipeline.translator", "SubtitleTranslator"),
        ("tiktok_pipeline.tts_engine", "TTSEngine"),
        ("tiktok_pipeline.composer", "VideoComposer"),
    ]
    for mod_path, cls_name in sub_specs:
        try:
            sub_mod = sys.modules.get(mod_path)
            if sub_mod is not None:
                importlib.reload(sub_mod)
            else:
                sub_mod = importlib.import_module(mod_path)
            new_c = getattr(sub_mod, cls_name, None)
            if new_c is not None:
                reloaded_classes[cls_name] = new_c
                globals()[cls_name] = new_c
        except Exception:
            pass

    for mod_name in ("__main__", "tiktok_pipeline.main", "tiktok_pipeline.crawler"):
        mod = sys.modules.get(mod_name)
        if not mod:
            continue
        for cls_name, new_cls in reloaded_classes.items():
            old_cls = getattr(mod, cls_name, None)
            if old_cls is not None and old_cls is not new_cls:
                for attr_name, attr_val in new_cls.__dict__.items():
                    if attr_name.startswith("__") and attr_name not in ("__init__",):
                        continue
                    try:
                        setattr(old_cls, attr_name, attr_val)
                    except Exception:
                        pass
            try:
                setattr(mod, cls_name, new_cls)
            except Exception:
                pass
        try:
            setattr(mod, "resolve_channel_profile", resolve_channel_profile)
            setattr(mod, "KNOWN_VIETNAMESE_HANDLES", KNOWN_VIETNAMESE_HANDLES)
        except Exception:
            pass


_hot_patch_live_crawler()
