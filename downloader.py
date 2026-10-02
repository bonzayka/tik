import asyncio
import os
import shutil
import subprocess
import uuid
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import yt_dlp

# Automatically make FFmpeg available in PATH without requiring sudo/root access
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

import config
from config import (
    DOWNLOADS_DIR,
    SUPPORTED_QUALITIES,
    MAX_FILE_SIZE_BYTES,
    BASE_DIR,
    COOKIES_FILE,
    COOKIES_FROM_BROWSER
)

logger = logging.getLogger(__name__)

def ensure_cookies():
    """Converts cookies.json to cookies.txt if cookies.json exists and cookies.txt doesn't or is older."""
    json_path = BASE_DIR / "cookies.json"
    txt_path = BASE_DIR / COOKIES_FILE
    if json_path.exists() and json_path.stat().st_size > 0:
        if not txt_path.exists() or txt_path.stat().st_mtime < json_path.stat().st_mtime:
            try:
                import json
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    lines = [
                        '# Netscape HTTP Cookie File',
                        '# https://curl.se/rfc/cookie_spec.html',
                        '# This is a generated file!  Do not edit.',
                        ''
                    ]
                    for c in data:
                        domain = c.get('domain', '')
                        flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                        path = c.get('path', '/')
                        secure = 'TRUE' if c.get('secure', False) else 'FALSE'
                        exp = int(c.get('expirationDate', 2147483647))
                        name = c.get('name', '')
                        val = c.get('value', '')
                        if name and val:
                            lines.append(f'{domain}\t{flag}\t{path}\t{secure}\t{exp}\t{name}\t{val}')
                    with open(txt_path, 'w', encoding='utf-8') as out:
                        out.write('\n'.join(lines) + '\n')
                    logger.info(f"Converted cookies.json to {COOKIES_FILE} ({len(lines)-4} cookies)")
            except Exception as e:
                logger.warning(f"Failed to convert cookies.json: {e}")

def is_youtube_url(url: str) -> bool:
    """Checks if the URL belongs to YouTube or YouTube Music."""
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u

def is_playlist_url(url: str) -> bool:
    """Checks if the URL is a YouTube playlist."""
    u = url.lower()
    return ("youtube.com" in u or "youtu.be" in u) and ("list=" in u) and ("list=wl" not in u)

def get_ydl_opts_for_url(url: str, custom_format: Optional[str] = None) -> Dict[str, Any]:
    """Generates optimal yt-dlp options tailored to the specific platform."""
    opts: Dict[str, Any] = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'js_runtimes': {'node': {}, 'deno': {}, 'quickjs': {}},
        'remote_components': ['ejs:github'],
    }

    # Only apply YouTube specific configurations
    if is_youtube_url(url):
        # 1. Check if cookies are provided (either cookies.txt or cookies.json)
        ensure_cookies()
        cookie_path = BASE_DIR / COOKIES_FILE
        if cookie_path.exists() and cookie_path.stat().st_size > 0:
            opts['cookiefile'] = str(cookie_path)
        elif COOKIES_FROM_BROWSER:
            opts['cookiesfrombrowser'] = (COOKIES_FROM_BROWSER,)
        else:
            # If no cookies, default to android client without webpage to bypass bot checks
            opts['extractor_args'] = {
                'youtube': {
                    'player_client': ['android'],
                    'player_skip': ['webpage', 'configs']
                }
            }

        # 2. Optional Proxy (e.g. from VLESS tunnel or .env)
        if config.PROXY:
            opts['proxy'] = config.PROXY

    if custom_format:
        opts['format'] = custom_format

    return opts

def _run_ydl_with_retry(ydl_opts: Dict[str, Any], url: str, download: bool = False) -> Dict[str, Any]:
    """Executes yt-dlp with automatic fallback across multiple clients and modes."""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=download)
    except Exception as e:
        err = str(e).lower()
        if is_youtube_url(url) and any(w in err for w in ['bot', 'sign in', 'cookies', 'confirm', 'requested format', 'unavailable', 'reload']):
            logger.warning(f"YouTube attempt failed ({e}), trying clean android client without cookies...")
            clean_opts = dict(ydl_opts)
            clean_opts.pop('cookiefile', None)
            clean_opts.pop('cookiesfrombrowser', None)
            clean_opts['extractor_args'] = {
                'youtube': {
                    'player_client': ['android'],
                    'player_skip': ['webpage', 'configs']
                }
            }
            try:
                with yt_dlp.YoutubeDL(clean_opts) as ydl:
                    return ydl.extract_info(url, download=download)
            except Exception as e2:
                logger.warning(f"Android retry also failed ({e2}), trying ios and web_embedded fallback...")
                ios_opts = dict(clean_opts)
                ios_opts['extractor_args'] = {
                    'youtube': {
                        'player_client': ['ios', 'web_embedded'],
                        'player_skip': ['webpage', 'configs']
                    }
                }
                with yt_dlp.YoutubeDL(ios_opts) as ydl:
                    return ydl.extract_info(url, download=download)
        raise

def _extract_info_sync(url: str) -> Dict[str, Any]:
    """Synchronous info extraction with yt-dlp and fallback logic."""
    ydl_opts = {
        **get_ydl_opts_for_url(url),
        'extract_flat': False,
    }
    return _run_ydl_with_retry(ydl_opts, url, download=False)

def estimate_format_sizes(info: Dict[str, Any], resolutions: List[int]) -> Dict[str, Any]:
    """
    Estimates file sizes for each resolution, as well as MP3 audio, voice note, and direct video.
    Takes into account separate video + audio streams (DASH) and progressive formats.
    """
    formats = info.get('formats') or []
    duration = info.get('duration') or 0

    # 1. Best audio stream size
    best_audio_size = 0
    best_audio_abr = 0
    for f in formats:
        vcodec = f.get('vcodec')
        acodec = f.get('acodec')
        # Audio-only stream
        if (not vcodec or vcodec == 'none') and acodec and acodec != 'none':
            abr = f.get('abr') or f.get('tbr') or 0
            size = f.get('filesize') or f.get('filesize_approx')
            if not size and abr and duration:
                size = int(abr * 1000 / 8 * duration)
            if size and abr >= best_audio_abr:
                best_audio_abr = abr
                best_audio_size = size

    # Fallback default audio estimate if audio stream is missing (128 kbps)
    if not best_audio_size and duration:
        best_audio_size = int(128 * 1000 / 8 * duration)

    # 2. Estimate size for each target resolution
    res_sizes: Dict[int, Optional[int]] = {}
    for res in resolutions:
        # Match formats where dimension <= res
        matching_formats = []
        for f in formats:
            h = f.get('height') or 0
            w = f.get('width') or 0
            vcodec = f.get('vcodec')
            dim = min(h, w) if (h and w) else (h or w)
            if dim and vcodec and vcodec != 'none' and dim <= res:
                matching_formats.append((dim, f))

        if not matching_formats:
            res_sizes[res] = None
            continue

        # Target the highest available dimension <= res
        max_dim = max(dim for dim, _ in matching_formats)
        target_formats = [f for dim, f in matching_formats if dim == max_dim]

        best_video_size = 0
        best_is_progressive = False
        best_vbr = 0

        for f in target_formats:
            acodec = f.get('acodec')
            is_prog = bool(acodec and acodec != 'none')
            size = f.get('filesize') or f.get('filesize_approx')
            bitrate = f.get('tbr') or f.get('vbr') or 0
            if not size and bitrate and duration:
                size = int(bitrate * 1000 / 8 * duration)

            if size and (bitrate >= best_vbr or size > best_video_size):
                best_vbr = bitrate
                best_video_size = size
                best_is_progressive = is_prog

        if best_video_size > 0:
            total_est = best_video_size if best_is_progressive else (best_video_size + best_audio_size)
            res_sizes[res] = total_est
        else:
            res_sizes[res] = None

    # 3. Audio MP3 size (approx 192 kbps)
    mp3_size = int(192 * 1000 / 8 * duration) if duration else best_audio_size

    # 4. Voice note OGG Opus size (approx 64 kbps mono)
    voice_size = int(64 * 1000 / 8 * duration) if duration else None

    # 5. Single video (for TikTok or non-YouTube)
    video_best = info.get('filesize') or info.get('filesize_approx')
    if not video_best and formats:
        for f in reversed(formats):
            s = f.get('filesize') or f.get('filesize_approx')
            if s:
                video_best = s
                break
    if not video_best and duration and info.get('tbr'):
        video_best = int(info['tbr'] * 1000 / 8 * duration)

    return {
        'resolutions': res_sizes,
        'audio_size': mp3_size,
        'voice_size': voice_size,
        'video_best': video_best
    }

async def get_video_info(url: str) -> Dict[str, Any]:
    """Asynchronously fetches metadata for a video URL and estimates format sizes."""
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
            h = f.get('height') or 0
            w = f.get('width') or 0
            vcodec = f.get('vcodec')
            dim = min(h, w) if (h and w) else (h or w)
            if dim and vcodec and vcodec != 'none':
                available_heights.add(int(dim))
                
        # Filter supported qualities
        resolutions: List[int] = []
        if 'youtube' in extractor:
            for q in SUPPORTED_QUALITIES:
                if any(h >= q for h in available_heights):
                    resolutions.append(q)
            if not resolutions and available_heights:
                best_h = min(max(available_heights), 2160)
                resolutions.append(best_h)
        else:
            resolutions = []

        # Estimate sizes
        estimated_sizes = estimate_format_sizes(info, resolutions)
            
        return {
            'success': True,
            'title': title,
            'duration': duration,
            'uploader': uploader,
            'thumbnail': thumbnail,
            'extractor': extractor,
            'is_live': is_live,
            'resolutions': resolutions,
            'estimated_sizes': estimated_sizes,
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
        format_selector = (
            f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={height}]+ba/"
            f"b[height<={height}]/"
            f"best"
        )
    else:
        format_selector = "bv*+ba/best"

    out_template = str(task_dir / "video_%(id)s.%(ext)s")
    ydl_opts = {
        **get_ydl_opts_for_url(url),
        'format': format_selector,
        'outtmpl': out_template,
        'merge_output_format': 'mp4',
    }

    info = _run_ydl_with_retry(ydl_opts, url, download=True)
    if 'entries' in info and info['entries']:
        info = info['entries'][0]
            
    # Find downloaded file
    mp4_files = list(task_dir.glob("*.mp4"))
    if not mp4_files:
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
    """Synchronous audio download as MP3 with embedded cover art and ID3 metadata."""
    out_template = str(task_dir / "audio_%(id)s.%(ext)s")
    ydl_opts = {
        **get_ydl_opts_for_url(url),
        'format': 'bestaudio/best',
        'outtmpl': out_template,
        'writethumbnail': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    info = _run_ydl_with_retry(ydl_opts, url, download=True)
    if 'entries' in info and info['entries']:
        info = info['entries'][0]

    mp3_files = list(task_dir.glob("*.mp3"))
    if not mp3_files:
        raise FileNotFoundError("Downloaded MP3 file not found")
    
    target_file = mp3_files[0]
    title = info.get('title', 'Audio')
    artist = info.get('uploader') or info.get('channel') or info.get('creator') or 'Audio'
    thumbnail_url = info.get('thumbnail')

    # Prepare 1:1 square cover art for Spotify/Apple Music look
    cover_file = task_dir / "cover.jpg"
    raw_images = [
        f for f in task_dir.glob("*") 
        if f.suffix.lower() in [".jpg", ".jpeg", ".webp", ".png"] and f != cover_file
    ]

    input_img = str(raw_images[0]) if raw_images else (thumbnail_url if thumbnail_url else None)
    if input_img:
        try:
            cmd_crop = [
                'ffmpeg', '-y',
                '-i', input_img,
                '-vf', 'crop=min(iw\\,ih):min(iw\\,ih),scale=500:500',
                str(cover_file)
            ]
            subprocess.run(cmd_crop, capture_output=True, timeout=15)
        except Exception as e:
            logger.warning(f"Could not crop album cover: {e}")

    # Embed cover and ID3v2 tags into MP3
    if cover_file.exists() and cover_file.stat().st_size > 0:
        tagged_file = task_dir / f"tagged_{target_file.name}"
        try:
            cmd_tag = [
                'ffmpeg', '-y',
                '-i', str(target_file),
                '-i', str(cover_file),
                '-map', '0:0',
                '-map', '1:0',
                '-c', 'copy',
                '-id3v2_version', '3',
                '-metadata', f'title={title}',
                '-metadata', f'artist={artist}',
                '-metadata', f'album={title}',
                '-metadata:s:v', 'title=Album cover',
                '-metadata:s:v', 'comment=Cover (front)',
                str(tagged_file)
            ]
            res = subprocess.run(cmd_tag, capture_output=True, timeout=20)
            if res.returncode == 0 and tagged_file.exists() and tagged_file.stat().st_size > 0:
                target_file.unlink(missing_ok=True)
                target_file = tagged_file
        except Exception as e:
            logger.warning(f"Failed to embed ID3 tags: {e}")

    filesize = target_file.stat().st_size
    duration = info.get('duration', 0)

    return {
        'filepath': str(target_file),
        'filesize': filesize,
        'duration': duration,
        'title': title,
        'uploader': artist,
        'thumb_path': str(cover_file) if cover_file.exists() else None
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
    raw_template = str(task_dir / "raw_audio_%(id)s.%(ext)s")
    ydl_opts = {
        **get_ydl_opts_for_url(url),
        'format': 'bestaudio/best',
        'outtmpl': raw_template,
    }

    info = _run_ydl_with_retry(ydl_opts, url, download=True)
    if 'entries' in info and info['entries']:
        info = info['entries'][0]

    raw_files = [f for f in task_dir.glob("raw_audio_*") if f.is_file() and not f.name.endswith('.part')]
    if not raw_files:
        raise FileNotFoundError("Raw audio file not found")
    
    raw_file = raw_files[0]
    voice_file = task_dir / "voice_message.ogg"

    # Convert to Telegram voice message spec with ffmpeg:
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
    
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        try:
            import static_ffmpeg
            static_ffmpeg.add_paths()
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except Exception:
            raise RuntimeError("FFmpeg не найден! Для работы без прав root выполните: pip install static-ffmpeg")

    if proc.returncode != 0:
        logger.error(f"FFmpeg error: {proc.stderr}")
        raise RuntimeError(f"FFmpeg error: {proc.stderr}")

    if not voice_file.exists():
        raise FileNotFoundError("Converted voice file not found")

    filesize = voice_file.stat().st_size
    duration = info.get('duration', 0)

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

def trim_media(
    input_path: str,
    output_path: str,
    start_sec: int,
    end_sec: int,
    is_video: bool = True
) -> bool:
    """Cuts a media file between start_sec and end_sec using ffmpeg."""
    duration = max(1, end_sec - start_sec)
    if is_video:
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start_sec),
            '-i', input_path,
            '-t', str(duration),
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '22',
            '-c:a', 'aac',
            output_path
        ]
    else:
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start_sec),
            '-i', input_path,
            '-t', str(duration),
            '-c', 'copy',
            output_path
        ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=120)
        return res.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 0
    except Exception as e:
        logger.error(f"Error trimming media: {e}")
        return False

def _get_playlist_info_sync(url: str) -> Dict[str, Any]:
    """Extracts playlist metadata without downloading media."""
    ydl_opts = {
        **get_ydl_opts_for_url(url),
        'extract_flat': 'in_playlist',
        'playlistend': 30,
    }
    info = _run_ydl_with_retry(ydl_opts, url, download=False)
    entries = info.get('entries') or []
    clean_entries = []
    for e in entries:
        if not e:
            continue
        v_id = e.get('id')
        v_url = e.get('url')
        if not v_url or not v_url.startswith('http'):
            v_url = f"https://www.youtube.com/watch?v={v_id}"
        clean_entries.append({
            'id': v_id,
            'title': e.get('title', 'Без названия'),
            'url': v_url,
            'duration': e.get('duration', 0),
            'uploader': e.get('uploader') or e.get('channel') or ''
        })
    return {
        'is_playlist': True,
        'title': info.get('title', 'Плейлист YouTube'),
        'uploader': info.get('uploader') or info.get('channel') or 'YouTube',
        'count': len(clean_entries),
        'entries': clean_entries
    }

async def get_playlist_info(url: str) -> Dict[str, Any]:
    """Asynchronously fetches playlist metadata."""
    return await asyncio.to_thread(_get_playlist_info_sync, url)

def _download_playlist_mp3_sync(
    entries: List[Dict[str, Any]],
    task_dir: Path,
    as_zip: bool = False,
    playlist_title: str = "Playlist"
) -> Dict[str, Any]:
    """Downloads tracks from a playlist as MP3, optionally packaging them into a ZIP archive."""
    import zipfile
    downloaded_files = []

    for i, entry in enumerate(entries, start=1):
        v_url = entry['url']
        track_dir = task_dir / f"track_{i}"
        track_dir.mkdir(parents=True, exist_ok=True)
        try:
            res = _download_audio_sync(v_url, track_dir)
            downloaded_files.append(res)
        except Exception as e:
            logger.warning(f"Failed to download playlist item {v_url}: {e}")

    if not downloaded_files:
        raise RuntimeError("Не удалось скачать ни один трек из плейлиста")

    if as_zip:
        safe_title = "".join(c for c in playlist_title if c.isalnum() or c in " -_").strip() or "Playlist"
        zip_path = task_dir / f"{safe_title}.zip"
        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
            for item in downloaded_files:
                fpath = Path(item['filepath'])
                if fpath.exists():
                    zipf.write(fpath, arcname=fpath.name)
        return {
            'is_zip': True,
            'filepath': str(zip_path),
            'filesize': zip_path.stat().st_size,
            'count': len(downloaded_files),
            'title': safe_title
        }
    else:
        return {
            'is_zip': False,
            'tracks': downloaded_files,
            'count': len(downloaded_files)
        }

async def download_playlist_mp3(
    entries: List[Dict[str, Any]],
    as_zip: bool = False,
    playlist_title: str = "Playlist"
) -> Tuple[Dict[str, Any], Path]:
    """Downloads playlist tracks as MP3 or ZIP."""
    task_id = str(uuid.uuid4())
    task_dir = DOWNLOADS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    try:
        res = await asyncio.to_thread(_download_playlist_mp3_sync, entries, task_dir, as_zip, playlist_title)
        return res, task_dir
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
