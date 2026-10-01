import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN", "7735472569:AAGK8ODEqysN2viV7M92WKXwjw-KtIG-3H8")

# Base directories
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / os.getenv("DOWNLOAD_DIR", "downloads")
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Limits
# Telegram Bot API standard upload limit is 50MB (52,428,800 bytes)
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_MB", "50")) * 1024 * 1024

# Quality options to display for YouTube
SUPPORTED_QUALITIES = [1080, 720, 480, 360]

# Task cache expiration (seconds)
TASK_TTL_SECONDS = 3600

# Cookies configuration for YouTube anti-bot bypass
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.txt")
COOKIES_FROM_BROWSER = os.getenv("COOKIES_FROM_BROWSER", "")
