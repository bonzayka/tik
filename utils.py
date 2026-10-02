import re
from typing import Optional

# Regex for detecting URLs
URL_PATTERN = re.compile(
    r"https?://(?:www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b[-a-zA-Z0-9()@:%_+.~#?&/=]*",
    re.IGNORECASE
)

def find_first_url(text: str) -> Optional[str]:
    """Finds the first valid HTTP/HTTPS URL in text."""
    if not text:
        return None
    match = URL_PATTERN.search(text.strip())
    if match:
        return match.group(0)
    return None

def format_duration(seconds: Optional[int | float]) -> str:
    """Formats duration in seconds to MM:SS or HH:MM:SS."""
    if not seconds or seconds < 0:
        return "Неизвестно"
    
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"

def format_size(size_bytes: Optional[int | float]) -> str:
    """Formats file size in bytes to human-readable string (КБ, МБ, ГБ)."""
    if not size_bytes or size_bytes <= 0:
        return "0 КБ"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} КБ"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} МБ"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} ГБ"

def detect_platform(url: str, extractor: Optional[str] = None) -> str:
    """Returns a friendly platform label."""
    url_lower = url.lower()
    if "tiktok.com" in url_lower:
        return "📱 TikTok"
    elif "youtube.com/shorts" in url_lower or "youtu.be" in url_lower and "shorts" in url_lower:
        return "⚡ YouTube Shorts"
    elif "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "🎬 YouTube"
    elif "instagram.com" in url_lower:
        return "📸 Instagram"
    elif "vk.com" in url_lower or "vkvideo.ru" in url_lower:
        return "🔵 VK Видео"
    
    if extractor:
        return f"🌐 {extractor.capitalize()}"
    return "🌐 Видео"
