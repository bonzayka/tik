import asyncio
import os
import shutil
import subprocess
import uuid
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import yt_dlp

from config import (
    DOWNLOADS_DIR,
    SUPPORTED_QUALITIES,
    MAX_FILE_SIZE_BYTES,
    BASE_DIR,
    COOKIES_FILE,
    COOKIES_FROM_BROWSER
)

logger = logging.getLogger(__name__)

_cached_browser: Optional[str] = None

def detect_browser_cookies() -> str:
    """Attempts to find an available browser with readable YouTube cookies."""
    global _cached_browser
    if _cached_browser is not None:
        return _cached_browser

    if COOKIES_FROM_BROWSER:
        _cached_browser = COOKIES_FROM_BROWSER
        return COOKIES_FROM_BROWSER

    # Try supported browsers on Windows/Linux
    for b in ['firefox', 'chrome', 'edge', 'brave', 'opera']:
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'cookiesfrombrowser': (b,),
                'extract_flat': True
            }
            with yt_dlp.YoutubeDL(ydl_opts):
                pass
            _cached_browser = b
            logger.info(f"Auto-detected working cookies from browser: {b}")
            return b
        except Exception:
            continue

    _cached_browser = ""
    return ""

def get_base_ydl_opts() -> Dict[str, Any]:
    """Generates standard yt-dlp options with anti-bot bypass and cookie integration."""
    opts: Dict[str, Any] = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'js_runtimes': {'node': {}},
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web']
            }
        },
    }

    # 1. Check for cookies file
    cookie_path = BASE_DIR / COOKIES_FILE
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        opts['cookiefile'] = str(cookie_path)
    else:
        # 2. Try browser cookies
        browser = detect_browser_cookies()
        if browser:
            opts['cookiesfrombrowser'] = (browser,)

    return opts

def _extract_info_sync(url: str) -> Dict[str, Any]:
    """Synchronous info extraction with yt-dlp and fallback logic."""
    ydl_opts = {
        **get_base_ydl_opts(),
        'extract_flat': False,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        err_str = str(e)
        # If blocked by YouTube bot detection, retry with fallback android client
        if "Sign in to confirm you’re not a bot" in err_str or "cookies" in err_str.lower():
            logger.warning("Sign-in required detected, attempting fallback with android player client...")
            fallback_opts = {
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'js_runtimes': {'node': {}},
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android']
                    }
                },
                'extract_flat': False
            }
            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                return ydl.extract_info(url, download=False)
        raise

async def get_video_info(url: str) -> Dict[str, Any]:
    """Asynchronously fetches metadata for a video URL."""
    try:
        info = await asyncio.to_thread(_extract_info_sync, url)
        
        # If playlist, get first entry
        if 'entries' in info and info['entries']:
            info = info['entries'][0]
            
        title = info.get('title', 'Без названия')
        duration = info.get('duration', 0)
        uploader = info.get('uploader') or info.get('channel') or info.get('creator') or 'Неизвестный автор'
        thumbnail = info.get('thumbnail')
        extractor = (info.get('extractor') or info.get('extractor_key') or '').lower()
        is_live = info.get('is_live', False)
        
        # Determine available resolutions
        formats = info.get('formats', [])
        available_heights = set()
        for f in formats:
            h = f.get('height')
            vcodec = f.get('vcodec')
            if h and vcodec and vcodec != 'none':
                available_heights.add(int(h))
                
        # Filter supported qualities
        resolutions: List[int] = []
        if 'youtube' in extractor:
            for q in SUPPORTED_QUALITIES:
                # If quality is available or lower than the max available height
                if any(h >= q for h in available_heights):
                    resolutions.append(q)
            # If none matched, take highest available <= 1080
            if not resolutions and available_heights:
                best_h = min(max(available_heights), 1080)
                resolutions.append(best_h)
        else:
            # For TikTok and other single-format platforms
            resolutions = []
            
        return {
            'success': True,
            'title': title,
            'duration': duration,
            'uploader': uploader,
            'thumbnail': thumbnail,
            'extractor': extractor,
            'is_live': is_live,
            'resolutions': resolutions,
            'raw_info': info
        }
    except Exception as e:
        logger.error(f"Error extracting info for {url}: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }

def _download_video_sync(url: str, task_dir: Path, height: Optional[int] = None) -> Dict[str, Any]:
    """Synchronous video download."""
    if height:
        # Prefer mp4 video and m4a audio, merge with ffmpeg
        format_selector = (
            f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={height}]+ba/"
            f"b[height<={height}]/"
            f"best"
        )
    else:
        # Best available (e.g. TikTok)
        format_selector = "bv*+ba/best"

    out_template = str(task_dir / "video_%(id)s.%(ext)s")
    ydl_opts = {
        **get_base_ydl_opts(),
        'format': format_selector,
        'outtmpl': out_template,
        'merge_output_format': 'mp4',
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if 'entries' in info and info['entries']:
            info = info['entries'][0]
            
    # Find downloaded file
    mp4_files = list(task_dir.glob("*.mp4"))
    if not mp4_files:
        # Fallback to any video file
        video_files = [f for f in task_dir.glob("*") if f.is_file() and not f.name.endswith('.part')]
        if not video_files:
            raise FileNotFoundError("Downloaded video file not found")
        target_file = video_files[0]
    else:
        target_file = mp4_files[0]

    filesize = target_file.stat().st_size
    duration = info.get('duration', 0)
    width = info.get('width', 0)
    video_height = info.get('height', 0)

    return {
        'filepath': str(target_file),
        'filesize': filesize,
        'duration': duration,
        'width': width,
        'height': video_height,
        'title': info.get('title', 'Video')
    }

async def download_video(url: str, height: Optional[int] = None) -> Tuple[Dict[str, Any], Path]:
    """Downloads video in a dedicated temporary directory."""
    task_id = str(uuid.uuid4())
    task_dir = DOWNLOADS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        result = await asyncio.to_thread(_download_video_sync, url, task_dir, height)
        return result, task_dir
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise

def _download_audio_sync(url: str, task_dir: Path) -> Dict[str, Any]:
    """Synchronous audio download as MP3."""
    out_template = str(task_dir / "audio_%(id)s.%(ext)s")
    ydl_opts = {
        **get_base_ydl_opts(),
        'format': 'bestaudio/best',
        'outtmpl': out_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if 'entries' in info and info['entries']:
            info = info['entries'][0]

    mp3_files = list(task_dir.glob("*.mp3"))
    if not mp3_files:
        raise FileNotFoundError("Downloaded MP3 file not found")
    
    target_file = mp3_files[0]
    filesize = target_file.stat().st_size
    duration = info.get('duration', 0)

    return {
        'filepath': str(target_file),
        'filesize': filesize,
        'duration': duration,
        'title': info.get('title', 'Audio'),
        'uploader': info.get('uploader') or info.get('channel') or 'Audio'
    }

async def download_audio(url: str) -> Tuple[Dict[str, Any], Path]:
    """Downloads audio as MP3 in a dedicated temporary directory."""
    task_id = str(uuid.uuid4())
    task_dir = DOWNLOADS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = await asyncio.to_thread(_download_audio_sync, url, task_dir)
        return result, task_dir
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise

def _download_voice_sync(url: str, task_dir: Path) -> Dict[str, Any]:
    """Synchronous voice message download (OGG Opus format for Telegram)."""
    # 1. First download best audio
    raw_template = str(task_dir / "raw_audio_%(id)s.%(ext)s")
    ydl_opts = {
        **get_base_ydl_opts(),
        'format': 'bestaudio/best',
        'outtmpl': raw_template,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if 'entries' in info and info['entries']:
            info = info['entries'][0]

    raw_files = [f for f in task_dir.glob("raw_audio_*") if f.is_file() and not f.name.endswith('.part')]
    if not raw_files:
        raise FileNotFoundError("Raw audio file not found")
    
    raw_file = raw_files[0]
    voice_file = task_dir / "voice_message.ogg"

    # 2. Convert to Telegram voice message spec with ffmpeg:
    # Codec: libopus, Sample rate: 48000, Channels: 1 (mono), Bitrate: 64k
    cmd = [
        'ffmpeg',
        '-y',
        '-i', str(raw_file),
        '-c:a', 'libopus',
        '-b:a', '64k',
        '-ar', '48000',
        '-ac', '1',
        '-vn',
        str(voice_file)
    ]
    
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error(f"FFmpeg error: {proc.stderr}")
        raise RuntimeError(f"FFmpeg failed to convert to voice note: {proc.stderr}")

    if not voice_file.exists():
        raise FileNotFoundError("Converted voice file not found")

    filesize = voice_file.stat().st_size
    duration = info.get('duration', 0)

    # Clean up raw file
    try:
        raw_file.unlink()
    except Exception:
        pass

    return {
        'filepath': str(voice_file),
        'filesize': filesize,
        'duration': duration,
        'title': info.get('title', 'Voice Message')
    }

async def download_voice(url: str) -> Tuple[Dict[str, Any], Path]:
    """Downloads and converts audio into Telegram voice note (.ogg opus)."""
    task_id = str(uuid.uuid4())
    task_dir = DOWNLOADS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = await asyncio.to_thread(_download_voice_sync, url, task_dir)
        return result, task_dir
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise

def cleanup_task_dir(task_dir: Path):
    """Safely removes temporary directory and files."""
    try:
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
    except Exception as e:
        logger.warning(f"Failed to cleanup task dir {task_dir}: {e}")
