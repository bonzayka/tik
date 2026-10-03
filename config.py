import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN", "7735472569:AAFUibgHA0g737Z_g24n5DqlVailiLo5y4Q")

# Base directories
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / os.getenv("DOWNLOAD_DIR", "downloads")
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Telegram MTProto Credentials for large file uploads (up to 2GB)
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
HAS_MTPROTO = bool(API_ID and API_HASH)

# Limits
# Standard Bot API limit is 50MB. With MTProto credentials (API_ID/API_HASH) it is 2000MB (2GB).
DEFAULT_MAX_MB = "2000" if HAS_MTPROTO else "50"
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", DEFAULT_MAX_MB))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Quality options to display for YouTube (2160p = 4K, 1440p = 2K, 1080p = FHD, etc.)
SUPPORTED_QUALITIES = [2160, 1440, 1080, 720, 480, 360]

# Task cache expiration (seconds)
TASK_TTL_SECONDS = 3600

# Cookies configuration for YouTube anti-bot bypass
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.txt")
COOKIES_FROM_BROWSER = os.getenv("COOKIES_FROM_BROWSER", "")

# Optional proxy for bypassing datacenter IP bans (http://user:pass@host:port or socks5://host:port)
PROXY = os.getenv("PROXY", os.getenv("YOUTUBE_PROXY", ""))

# VLESS Reality URL for automatic tunnel proxy
VLESS_URL = os.getenv("VLESS_URL", "")
