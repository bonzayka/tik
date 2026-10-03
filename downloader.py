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

# Monkey-patch yt-dlp extractors to support photo posts and carousels
try:
    from yt_dlp.extractor.pinterest import PinterestIE
    _orig_pin_extract = PinterestIE._extract_video

    def _patched_pinterest_extract_video(self, data, extract_formats=True):
        res = _orig_pin_extract(self, data, extract_formats=extract_formats)
        if not res.get('formats'):
            images = data.get('images', {})
            img_url, w, h = None, None, None
            if isinstance(images, dict):
                orig = images.get('orig') or images.get('736x') or images.get('564x')
                if isinstance(orig, dict):
                    img_url = orig.get('url')
                    w = orig.get('width')
                    h = orig.get('height')
            if not img_url and res.get('thumbnails'):
                t = res['thumbnails'][-1]
                img_url = t.get('url')
                w = t.get('width')
                h = t.get('height')
            if img_url:
                res['formats'] = [{
                    'url': img_url,
                    'format_id': 'image_orig',
                    'ext': 'jpg',
                    'width': w,
                    'height': h,
                    'vcodec': 'none',
                    'acodec': 'none',
                }]
        return res

    PinterestIE._extract_video = _patched_pinterest_extract_video
except Exception as _e:
    pass

try:
    from yt_dlp.extractor.instagram import InstagramIE
    _orig_ig_extract_media = InstagramIE._extract_product_media

    def _patched_ig_extract_product_media(self, product_media):
        res = _orig_ig_extract_media(self, product_media)
        if not res.get('formats') and res.get('thumbnails'):
            best_thumb = res['thumbnails'][-1]
            res['formats'] = [{
                'url': best_thumb['url'],
                'format_id': 'image_orig',
                'ext': 'jpg',
                'width': best_thumb.get('width'),
                'height': best_thumb.get('height'),
                'vcodec': 'none',
                'acodec': 'none',
            }]
        return res

    InstagramIE._extract_product_media = _patched_ig_extract_product_media
except Exception as _e:
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
from utils import clean_social_url, resolve_short_url_sync

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

def _fetch_pinterest_direct_info(url: str) -> Optional[Dict[str, Any]]:
    """Directly extracts media info from Pinterest API or HTML if yt-dlp fails."""
    import urllib.request
    import urllib.parse
    import json
    import re
    
    clean_u = resolve_short_url_sync(url)
    m = re.search(r'/pin/(?:[\w-]+--)?(\d+)', clean_u)
    pin_id = m.group(1) if m else None
    
    title = "Pinterest Pin"
    uploader = "Pinterest"
    media_url = None
    media_type = "photo"
    thumb_url = None
    
    # 1. Unauth PinResource API
    if pin_id:
        try:
            q = urllib.parse.urlencode({'data': json.dumps({'options': {'field_set_key': 'unauth_react_main_pin', 'id': pin_id}})})
            api_req = urllib.request.Request(
                f'https://www.pinterest.com/resource/PinResource/get/?{q}',
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    'X-Pinterest-PWS-Handler': 'www/[username].js',
                    'Accept': 'application/json, text/javascript, */*, q=0.01'
                }
            )
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
            with opener.open(api_req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8')).get('resource_response', {}).get('data', {})
                if data:
                    title = data.get('title') or data.get('grid_title') or title
                    uploader = (data.get('closeup_attribution') or {}).get('full_name') or uploader
                    videos = data.get('videos', {})
                    video_list = videos.get('video_list') if isinstance(videos, dict) else None
                    if video_list and isinstance(video_list, dict):
                        for vk, vd in video_list.items():
                            if isinstance(vd, dict) and vd.get('url') and not vd['url'].endswith('.m3u8'):
                                media_url = vd['url']
                                media_type = 'video'
                                break
                    images = data.get('images', {})
                    orig = images.get('orig') or images.get('736x') or images.get('564x')
                    if orig and orig.get('url'):
                        thumb_url = orig.get('url')
                        if not media_url:
                            media_url = orig['url']
                            media_type = 'photo'
        except Exception as e:
            logger.debug(f"Pinterest PinResource API failed: {e}")

    # 2. HTML parsing fallback
    if not media_url:
        try:
            req = urllib.request.Request(
                clean_u,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
            )
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
            with opener.open(req, timeout=10) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
                
            og_v = re.search(r'<meta\s+property=["\']og:video(?::secure_url)?["\']\s+content=["\']([^"\']+)["\']', html)
            if og_v:
                media_url = og_v.group(1)
                media_type = 'video'
            else:
                og_i = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html)
                if og_i:
                    img = og_i.group(1)
                    media_url = re.sub(r'/(?:736x|474x|236x|564x)/', '/originals/', img)
                    media_type = 'photo'
                    thumb_url = media_url
        except Exception as e:
            logger.debug(f"Pinterest HTML fallback failed: {e}")

    if not media_url:
        return None

    return {
        'id': pin_id or 'pinterest_pin',
        'title': title,
        'uploader': uploader,
        'thumbnail': thumb_url or media_url,
        'extractor': 'pinterest',
        'duration': 0,
        'formats': [{
            'url': media_url,
            'format_id': 'direct',
            'ext': 'mp4' if media_type == 'video' else 'jpg',
            'vcodec': 'none' if media_type == 'photo' else 'h264',
            'acodec': 'none' if media_type == 'photo' else 'aac',
        }],
        '_direct_url': media_url,
        '_media_type': media_type
    }

def _download_pinterest_direct(url: str, task_dir: Path) -> Dict[str, Any]:
    """Directly downloads Pinterest media (photo or video) into task_dir."""
    import urllib.request
    
    info = _fetch_pinterest_direct_info(url)
    if not info or not info.get('_direct_url'):
        raise ValueError("Не удалось получить прямую ссылку на медиа Pinterest")
        
    direct_url = info['_direct_url']
    media_type = info['_media_type']
    pin_id = info.get('id', 'pin')
    ext = '.mp4' if media_type == 'video' else '.jpg'
    target_file = task_dir / f"pinterest_{pin_id}{ext}"
    
    req = urllib.request.Request(
        direct_url,
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
    )
    with urllib.request.urlopen(req, timeout=30) as resp, open(target_file, 'wb') as f:
        shutil.copyfileobj(resp, f)
        
    filesize = target_file.stat().st_size
    return {
        'media_type': media_type,
        'filepath': str(target_file),
        'files': [str(target_file)],
        'filesize': filesize,
        'duration': 0,
        'width': 0,
        'height': 0,
        'title': info.get('title', 'Pinterest Pin'),
        'uploader': info.get('uploader', 'Pinterest')
    }

def get_video_fps(media_target: str) -> Optional[int]:
    """Extracts video framerate (FPS) using ffprobe from local file or media URL."""
    if not media_target:
        return None
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=r_frame_rate,avg_frame_rate',
            '-of', 'json',
            str(media_target)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout:
            import json
            data = json.loads(res.stdout)
            streams = data.get('streams', [])
            if streams:
                r_fps_str = streams[0].get('r_frame_rate') or streams[0].get('avg_frame_rate')
                if r_fps_str and '/' in r_fps_str:
                    num, den = r_fps_str.split('/')
                    num_f, den_f = float(num), float(den)
                    if den_f > 0:
                        val = round(num_f / den_f)
                        if val > 0:
                            return val
    except Exception as e:
        logger.debug(f"Error getting video fps for {media_target}: {e}")
    return None

def _fetch_tiktok_direct_info(url: str) -> Optional[Dict[str, Any]]:
    """Directly extracts media info for TikTok (videos, 120 FPS edits, and photo slideshows) via TikWM."""
    import urllib.request
    import urllib.parse
    import json

    clean_u = resolve_short_url_sync(url)
    clean_u = clean_social_url(clean_u)

    try:
        data_encoded = urllib.parse.urlencode({'url': clean_u, 'hd': 1}).encode()
        req = urllib.request.Request(
            'https://www.tikwm.com/api/',
            data=data_encoded,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'}
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            res_json = json.loads(resp.read().decode('utf-8'))

        if res_json.get('code') != 0 or not res_json.get('data'):
            logger.warning(f"TikWM returned non-zero code: {res_json.get('msg', 'Unknown error')}")
            return None

        data = res_json['data']
        post_id = str(data.get('id', 'tiktok_post'))
        title = data.get('title') or "TikTok Media"
        author_dict = data.get('author') or {}
        uploader = author_dict.get('nickname') or author_dict.get('unique_id') or "TikTok Creator"
        duration = data.get('duration', 0)
        cover = data.get('cover') or data.get('origin_cover')
        music_url = data.get('music')
        images = data.get('images')
        hdplay = data.get('hdplay')
        play = data.get('play')

        # 1. Handle Photo Slideshow (картинки / карусель)
        if images and isinstance(images, list) and len(images) > 0:
            photo_count = len(images)
            media_type = 'carousel' if photo_count > 1 else 'photo'
            formats = []
            for idx, img_u in enumerate(images):
                formats.append({
                    'url': img_u,
                    'format_id': f'photo_{idx+1}',
                    'ext': 'jpg',
                    'vcodec': 'none',
                    'acodec': 'none',
                })
            if music_url:
                formats.append({
                    'url': music_url,
                    'format_id': 'audio',
                    'ext': 'mp3',
                    'vcodec': 'none',
                    'acodec': 'mp3',
                })

            return {
                'id': post_id,
                'title': title,
                'uploader': uploader,
                'duration': duration,
                'thumbnail': images[0] if images else cover,
                'extractor': 'tiktok',
                'formats': formats,
                '_is_slideshow': True,
                '_photo_count': photo_count,
                '_images': images,
                '_music_url': music_url,
                '_media_type': media_type,
                'media_type': media_type,
                'fps': None
            }

        # 2. Handle Video (including 4K / 120 FPS edits)
        video_url = hdplay or play
        if not video_url:
            return None

        # Probe FPS with ffprobe on direct stream (timeout 4s)
        fps = get_video_fps(video_url)

        filesize = data.get('size')
        formats = [{
            'url': video_url,
            'format_id': 'direct_hd' if hdplay else 'direct_play',
            'ext': 'mp4',
            'filesize': filesize,
            'fps': fps,
            'vcodec': 'h264',
            'acodec': 'aac',
        }]
        if music_url:
            formats.append({
                'url': music_url,
                'format_id': 'audio',
                'ext': 'mp3',
                'vcodec': 'none',
                'acodec': 'mp3',
            })

        return {
            'id': post_id,
            'title': title,
            'uploader': uploader,
            'duration': duration,
            'thumbnail': cover or video_url,
            'extractor': 'tiktok',
            'formats': formats,
            '_direct_url': video_url,
            '_music_url': music_url,
            '_media_type': 'video',
            'media_type': 'video',
            'filesize': filesize,
            'fps': fps,
            '_is_slideshow': False,
            '_photo_count': 0
        }
    except Exception as e:
        logger.debug(f"TikWM direct info extraction failed: {e}")
        return None

def _download_tiktok_direct(url_or_info: Any, task_dir: Path) -> Optional[Dict[str, Any]]:
    """Directly downloads TikTok video or photo slideshow with high speed and full quality."""
    import urllib.request

    if isinstance(url_or_info, dict) and (url_or_info.get('_direct_url') or url_or_info.get('_images')):
        info = url_or_info
    else:
        info = _fetch_tiktok_direct_info(str(url_or_info))
        if not info:
            return None

    title = info.get('title', 'TikTok Media')
    uploader = info.get('uploader', 'TikTok')
    post_id = info.get('id', 'post')

    # Case 1: Photo Slideshow
    if info.get('_is_slideshow') and info.get('_images'):
        images = info['_images']
        downloaded_files = []
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'}

        for idx, img_url in enumerate(images, start=1):
            out_file = task_dir / f"image_{idx:02d}.jpg"
            req = urllib.request.Request(img_url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=15) as resp, open(out_file, 'wb') as f:
                    shutil.copyfileobj(resp, f)
                if out_file.exists() and out_file.stat().st_size > 0:
                    downloaded_files.append(out_file)
            except Exception as e_img:
                logger.warning(f"Failed to download TikTok slide image {idx} ({img_url}): {e_img}")

        if not downloaded_files:
            raise RuntimeError("Не удалось скачать ни одной картинки из слайдшоу TikTok")

        total_size = sum(f.stat().st_size for f in downloaded_files)
        media_type = 'carousel' if len(downloaded_files) > 1 else 'photo'

        return {
            'media_type': media_type,
            'filepath': str(downloaded_files[0]),
            'files': [str(f) for f in downloaded_files],
            'filesize': total_size,
            'duration': 0,
            'width': 0,
            'height': 0,
            'fps': None,
            'title': title,
            'uploader': uploader,
            'is_slideshow': True,
            'photo_count': len(downloaded_files),
            'music_url': info.get('_music_url')
        }

    # Case 2: Video (including 4K / 120 FPS)
    direct_url = info.get('_direct_url')
    if not direct_url:
        return None

    target_file = task_dir / f"tiktok_{post_id}.mp4"
    req = urllib.request.Request(
        direct_url,
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'}
    )
    with urllib.request.urlopen(req, timeout=60) as resp, open(target_file, 'wb') as f:
        shutil.copyfileobj(resp, f)

    filesize = target_file.stat().st_size
    actual_fps = get_video_fps(str(target_file)) or info.get('fps')

    return {
        'media_type': 'video',
        'filepath': str(target_file),
        'files': [str(target_file)],
        'filesize': filesize,
        'duration': info.get('duration', 0),
        'width': info.get('width', 0),
        'height': info.get('height', 0),
        'fps': actual_fps,
        'title': title,
        'uploader': uploader,
        'is_slideshow': False,
        'photo_count': 0
    }

def _download_tiktok_audio_direct(url: str, task_dir: Path) -> Dict[str, Any]:
    """Directly downloads TikTok audio as MP3 with tags and cover."""
    import urllib.request
    info = _fetch_tiktok_direct_info(url)
    if not info or not info.get('_music_url'):
        raise RuntimeError("Не удалось найти аудиодорожку TikTok")

    music_url = info['_music_url']
    title = info.get('title', 'TikTok Audio')
    uploader = info.get('uploader', 'TikTok')
    thumb_url = info.get('thumbnail')

    target_file = task_dir / "tiktok_audio.mp3"
    req = urllib.request.Request(music_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    with urllib.request.urlopen(req, timeout=30) as resp, open(target_file, 'wb') as f:
        shutil.copyfileobj(resp, f)

    cover_file = task_dir / "cover.jpg"
    if thumb_url:
        try:
            req_t = urllib.request.Request(thumb_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            with urllib.request.urlopen(req_t, timeout=15) as resp, open(cover_file, 'wb') as f:
                shutil.copyfileobj(resp, f)
        except Exception:
            pass

    # Embed ID3 tags & cover art
    if cover_file.exists() and cover_file.stat().st_size > 0:
        tagged_file = task_dir / "tagged_audio.mp3"
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
                '-metadata', f'artist={uploader}',
                '-metadata', f'album={title}',
                str(tagged_file)
            ]
            res = subprocess.run(cmd_tag, capture_output=True, timeout=20)
            if res.returncode == 0 and tagged_file.exists() and tagged_file.stat().st_size > 0:
                target_file.unlink(missing_ok=True)
                target_file = tagged_file
        except Exception as e:
            logger.warning(f"Failed to embed TikTok ID3 tags: {e}")

    return {
        'filepath': str(target_file),
        'filesize': target_file.stat().st_size,
        'duration': info.get('duration', 0),
        'title': title,
        'uploader': uploader,
        'thumb_path': str(cover_file) if cover_file.exists() else None
    }

def get_ydl_opts_for_url(url: str, custom_format: Optional[str] = None) -> Dict[str, Any]:
    """Generates optimal yt-dlp options tailored to the specific platform."""
    url_lower = url.lower()
    is_multi_item = any(p in url_lower for p in ['instagram.com', 'pinterest.com', 'tiktok.com', 'douyin.com'])

    opts: Dict[str, Any] = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': not is_multi_item,
        'js_runtimes': {'node': {}, 'deno': {}, 'quickjs': {}},
        'remote_components': ['ejs:github'],
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
        }
    }

    # Pass cookies if available (cookies.txt or cookies.json)
    ensure_cookies()
    cookie_path = BASE_DIR / COOKIES_FILE
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        opts['cookiefile'] = str(cookie_path)
    elif COOKIES_FROM_BROWSER:
        opts['cookiesfrombrowser'] = (COOKIES_FROM_BROWSER,)

    # YouTube specific configurations
    if is_youtube_url(url):
        if not (cookie_path.exists() and cookie_path.stat().st_size > 0) and not COOKIES_FROM_BROWSER:
            opts['extractor_args'] = {
                'youtube': {
                    'player_client': ['android'],
                    'player_skip': ['webpage', 'configs']
                }
            }

    # Proxy support
    if config.PROXY and (is_youtube_url(url) or 'instagram.com' in url_lower):
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
    url = resolve_short_url_sync(url)
    url = clean_social_url(url)

    # For Pinterest: try direct fetch first (fastest and handles all image/video pins)
    if any(p in url.lower() for p in ['pinterest.com', 'pin.it']):
        direct = _fetch_pinterest_direct_info(url)
        if direct:
            return direct

    # For TikTok: try direct fetch first (handles 120 FPS, HD watermark-free, and photos/slideshows without IP blocks)
    if any(p in url.lower() for p in ['tiktok.com', 'douyin.com']):
        direct = _fetch_tiktok_direct_info(url)
        if direct:
            return direct

    ydl_opts = {
        **get_ydl_opts_for_url(url),
        'extract_flat': False,
        'format': 'bestvideo+bestaudio/best/all',
    }
    try:
        return _run_ydl_with_retry(ydl_opts, url, download=False)
    except Exception as e:
        if any(p in url.lower() for p in ['pinterest.com', 'pin.it']):
            direct = _fetch_pinterest_direct_info(url)
            if direct:
                return direct
        if any(p in url.lower() for p in ['tiktok.com', 'douyin.com']):
            direct = _fetch_tiktok_direct_info(url)
            if direct:
                return direct
        raise

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
                
        # Filter supported qualities (YouTube and VK support multiple resolutions)
        resolutions: List[int] = []
        is_multi_quality = any(p in extractor for p in ['youtube', 'vk'])
        if is_multi_quality:
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

        fps = info.get('fps')
        if not fps and formats:
            fps_candidates = [f.get('fps') for f in formats if f.get('fps') and f.get('fps') > 0]
            if fps_candidates:
                fps = max(fps_candidates)
        if fps:
            fps = round(fps)

        media_type = info.get('_media_type') or info.get('media_type')
        is_slideshow = info.get('_is_slideshow', False)
        photo_count = info.get('_photo_count', 0)
            
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
            'fps': fps,
            'media_type': media_type,
            'is_slideshow': is_slideshow,
            'photo_count': photo_count,
            'raw_info': info
        }
    except Exception as e:
        logger.error(f"Error extracting info for {url}: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }

def _download_video_sync(url: str, task_dir: Path, height: Optional[int] = None) -> Dict[str, Any]:
    """Synchronous media download (supports video, photo, and multi-file carousels)."""
    url = resolve_short_url_sync(url)
    url = clean_social_url(url)

    # For TikTok: try direct download first (handles 120 FPS, HD watermark-free, and photos/slideshows)
    if any(p in url.lower() for p in ['tiktok.com', 'douyin.com']):
        try:
            res_tt = _download_tiktok_direct(url, task_dir)
            if res_tt:
                return res_tt
        except Exception as e_tt:
            logger.warning(f"TikTok direct download failed ({e_tt}), falling back to yt-dlp...")

    if height:
        format_selector = (
            f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={height}]+ba/"
            f"b[height<={height}]/"
            f"best/"
            f"all"
        )
    else:
        format_selector = "bestvideo+bestaudio/best/all"

    out_template = str(task_dir / "%(id)s_%(autonumber)s.%(ext)s")
    ydl_opts = {
        **get_ydl_opts_for_url(url),
        'format': format_selector,
        'outtmpl': out_template,
    }

    try:
        info = _run_ydl_with_retry(ydl_opts, url, download=True)
    except Exception as e:
        if any(p in url.lower() for p in ['pinterest.com', 'pin.it']):
            logger.info(f"yt-dlp Pinterest download failed ({e}), trying direct download...")
            return _download_pinterest_direct(url, task_dir)
        if any(p in url.lower() for p in ['tiktok.com', 'douyin.com']):
            logger.info(f"yt-dlp TikTok download failed ({e}), trying direct download fallback...")
            res_tt = _download_tiktok_direct(url, task_dir)
            if res_tt:
                return res_tt
        raise

    if 'entries' in info and info['entries']:
        info_first = info['entries'][0]
    else:
        info_first = info

    # Find all downloaded media files (excluding partial/temp files)
    all_files = [
        f for f in task_dir.glob("*")
        if f.is_file() and not f.name.endswith('.part') and not f.name.endswith('.ytdl')
    ]
    if not all_files:
        if any(p in url.lower() for p in ['pinterest.com', 'pin.it']):
            return _download_pinterest_direct(url, task_dir)
        if any(p in url.lower() for p in ['tiktok.com', 'douyin.com']):
            res_tt = _download_tiktok_direct(url, task_dir)
            if res_tt:
                return res_tt
        raise FileNotFoundError("Downloaded media file not found")

    image_exts = {'.jpg', '.jpeg', '.png', '.webp'}
    video_exts = {'.mp4', '.mov', '.webm', '.mkv'}

    images = [f for f in all_files if f.suffix.lower() in image_exts]
    videos = [f for f in all_files if f.suffix.lower() in video_exts]

    if len(all_files) > 1:
        media_type = 'carousel'
        target_file = all_files[0]
    elif len(images) == 1 and not videos:
        media_type = 'photo'
        target_file = images[0]
    else:
        media_type = 'video'
        target_file = videos[0] if videos else all_files[0]

    filesize = target_file.stat().st_size
    duration = info_first.get('duration', 0)
    width = info_first.get('width', 0)
    video_height = info_first.get('height', 0)
    actual_fps = get_video_fps(str(target_file)) if media_type == 'video' else info_first.get('fps')

    return {
        'media_type': media_type,
        'filepath': str(target_file),
        'files': [str(f) for f in all_files],
        'filesize': filesize,
        'duration': duration,
        'width': width,
        'height': video_height,
        'fps': actual_fps,
        'title': info_first.get('title', 'Media'),
        'is_slideshow': media_type in ('carousel', 'photo'),
        'photo_count': len(images) if images else 0
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

    try:
        info = _run_ydl_with_retry(ydl_opts, url, download=True)
    except Exception as e:
        if any(p in url.lower() for p in ['tiktok.com', 'douyin.com']):
            logger.info(f"yt-dlp TikTok audio download failed ({e}), trying direct audio download...")
            return _download_tiktok_audio_direct(url, task_dir)
        raise
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
