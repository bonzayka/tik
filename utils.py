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

def clean_social_url(url: str) -> str:
    """Normalizes mobile share URLs (e.g. Instagram /share/) and removes tracking parameters."""
    if not url:
        return url
    # Clean instagram share link: /share/p/ -> /p/, /share/reel/ -> /reel/
    url = re.sub(r'instagram\.com/share/(p|reel|tv)/', r'instagram.com/\1/', url, flags=re.IGNORECASE)
    # Strip tracking parameters for social links
    if any(d in url.lower() for d in ['instagram.com', 'tiktok.com', 'pinterest.com', 'pin.it', 'twitter.com', 'x.com', 'reddit.com']):
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        if parsed.query:
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            for k in list(qs.keys()):
                if any(t in k.lower() for t in ['utm_', 'igsh', 'share_id', 'invite_code', 'tt_from', 'feature']):
                    qs.pop(k, None)
            new_q = urllib.parse.urlencode(qs, doseq=True)
            url = urllib.parse.urlunparse(parsed._replace(query=new_q))
    return url

def resolve_short_url_sync(url: str) -> str:
    """Follows HTTP redirects for short URLs like pin.it."""
    if 'pin.it' in url.lower():
        import urllib.request
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
            )
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
            with opener.open(req, timeout=10) as resp:
                final = resp.geturl()
                if final and 'pinterest.com' in final:
                    return final
        except Exception:
            pass
    return url

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
    elif "youtube.com/shorts" in url_lower or ("youtu.be" in url_lower and "shorts" in url_lower):
        return "⚡ YouTube Shorts"
    elif "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "🎬 YouTube"
    elif "instagram.com" in url_lower or "instagr.am" in url_lower:
        if "/reel" in url_lower:
            return "📸 Instagram Reels"
        elif "/stories" in url_lower:
            return "📸 Instagram Stories"
        return "📸 Instagram"
    elif "vk.com/clip" in url_lower or "/clip" in url_lower:
        return "🔵 VK Клипы"
    elif "vk.com" in url_lower or "vkvideo.ru" in url_lower:
        return "🔵 VK Видео"
    elif "pinterest.com" in url_lower or "pin.it" in url_lower:
        return "📌 Pinterest"
    elif "twitter.com" in url_lower or "x.com" in url_lower:
        return "🐦 Twitter (X)"
    elif "reddit.com" in url_lower or "redd.it" in url_lower:
        return "🔴 Reddit"

    if extractor:
        return f"🌐 {extractor.capitalize()}"
    return "🌐 Видео"

def parse_timecode_str(s: str) -> Optional[int]:
    """Converts MM:SS, HH:MM:SS, or raw SS into total seconds."""
    if not s:
        return None
    parts = s.strip().split(':')
    try:
        if len(parts) == 1:
            return int(parts[0])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return None
    return None

def parse_time_range(text: str) -> Optional[tuple[int, int]]:
    """
    Finds and parses a time range like '01:15-02:40', '1:20 - 2:30', '10-45' from text.
    Returns (start_seconds, end_seconds) or None.
    """
    if not text:
        return None
    pattern = r"(?:^|\s)(\d{1,2}(?::\d{2}){1,2}|\d+)\s*(?:-|–|—|to|\s)\s*(\d{1,2}(?::\d{2}){1,2}|\d+)(?:\s|$)"
    match = re.search(pattern, text)
    if not match:
        return None
    start_sec = parse_timecode_str(match.group(1))
    end_sec = parse_timecode_str(match.group(2))
    if start_sec is not None and end_sec is not None and end_sec > start_sec:
        return (start_sec, end_sec)
    return None
