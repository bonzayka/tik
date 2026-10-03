from typing import List, Optional, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils import format_size


def create_download_keyboard(
    task_id: str,
    resolutions: Optional[List[int]] = None,
    is_youtube: bool = False,
    estimated_sizes: Optional[Dict[str, Any]] = None,
    has_time_range: bool = False,
    fps: Optional[int] = None,
    media_type: Optional[str] = None,
    photo_count: Optional[int] = None,
    is_slideshow: bool = False
) -> InlineKeyboardMarkup:
    """Builds an inline keyboard with quality, 120 FPS document, and photo slideshow options."""
    keyboard: List[List[InlineKeyboardButton]] = []

    res_sizes = (estimated_sizes or {}).get("resolutions", {})
    audio_size = (estimated_sizes or {}).get("audio_size")
    voice_size = (estimated_sizes or {}).get("voice_size")
    video_best_size = (estimated_sizes or {}).get("video_best")

    # 1. Handle Photo Slideshow (TikTok photos, carousels)
    if is_slideshow or (media_type in ('carousel', 'photo') and photo_count and photo_count > 0):
        cnt_label = f" ({photo_count} шт)" if photo_count else ""
        keyboard.append([
            InlineKeyboardButton(
                text=f"📸 Скачать все фото{cnt_label}",
                callback_data=f"dl:{task_id}:v:best"
            )
        ])
        keyboard.append([
            InlineKeyboardButton(
                text="📁 Скачать архивом ZIP (без сжатия)",
                callback_data=f"dl:{task_id}:doc"
            )
        ])
        keyboard.append([
            InlineKeyboardButton(
                text="🎵 MP3 музыка",
                callback_data=f"dl:{task_id}:audio"
            ),
            InlineKeyboardButton(
                text="🎙 ГС",
                callback_data=f"dl:{task_id}:voice"
            )
        ])
        keyboard.append([
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"dl:{task_id}:cancel"
            )
        ])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    # 2. Handle YouTube / Multi-resolution videos
    if resolutions:
        quality_row: List[InlineKeyboardButton] = []
        for res in resolutions:
            # Build clean label with badge
            if res >= 2160:
                badge = "4K"
            elif res >= 1440:
                badge = "2K"
            elif res >= 1080:
                badge = "FHD"
            elif res == 720:
                badge = "HD"
            else:
                badge = None

            size_val = res_sizes.get(res)
            if badge:
                label = f"🎬 {res}p ({badge})"
            else:
                label = f"🎬 {res}p"

            if size_val:
                label += f" • ~{format_size(size_val)}"

            btn = InlineKeyboardButton(
                text=label,
                callback_data=f"dl:{task_id}:v:{res}"
            )

            # 4K gets its own prominent full-width top row
            if res >= 2160:
                keyboard.append([btn])
            else:
                quality_row.append(btn)
                if len(quality_row) == 2:
                    keyboard.append(quality_row)
                    quality_row = []
        if quality_row:
            keyboard.append(quality_row)

        # File download button for multi-resolution
        if fps and fps > 60:
            doc_label = f"⚡️ 📄 Файлом без сжатия ({fps} FPS / Оригинал)"
        else:
            doc_label = "📄 Скачать файлом (без сжатия / 120 FPS)"
        keyboard.append([
            InlineKeyboardButton(
                text=doc_label,
                callback_data=f"dl:{task_id}:doc"
            )
        ])
    else:
        # TikTok, Instagram, Pinterest, Twitter, Reddit, etc.
        label = "🎬 Скачать видео (HD / Плеер TG)"
        if video_best_size:
            label += f" • ~{format_size(video_best_size)}"

        keyboard.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"dl:{task_id}:v:best"
            )
        ])

        # Prominent uncompressed document button for 120 FPS
        if fps and fps > 60:
            doc_label = f"⚡️ 📄 Файлом без сжатия ({fps} FPS)"
        else:
            doc_label = "📄 Скачать файлом (120 FPS / без сжатия)"

        keyboard.append([
            InlineKeyboardButton(
                text=doc_label,
                callback_data=f"dl:{task_id}:doc"
            )
        ])

    # Audio & Voice note row with sizes
    audio_label = "🎵 MP3"
    if audio_size:
        audio_label += f" • ~{format_size(audio_size)}"

    voice_label = "🎙 ГС"
    if voice_size:
        voice_label += f" • ~{format_size(voice_size)}"

    keyboard.append([
        InlineKeyboardButton(
            text=audio_label,
            callback_data=f"dl:{task_id}:audio"
        ),
        InlineKeyboardButton(
            text=voice_label,
            callback_data=f"dl:{task_id}:voice"
        )
    ])

    # Trimming option (if not already trimmed)
    if not has_time_range:
        keyboard.append([
            InlineKeyboardButton(
                text="✂️ Нарезать фрагмент (таймкод)",
                callback_data=f"dl:{task_id}:trim"
            )
        ])

    # Cancel button
    keyboard.append([
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"dl:{task_id}:cancel"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_playlist_keyboard(task_id: str, count: int) -> InlineKeyboardMarkup:
    """Builds an inline keyboard for YouTube Playlists."""
    keyboard = [
        [
            InlineKeyboardButton(
                text="🎵 Первые 5 треков (MP3)",
                callback_data=f"pl:{task_id}:mp3:5"
            ),
            InlineKeyboardButton(
                text=f"🎵 Первые {min(10, count)} (MP3)",
                callback_data=f"pl:{task_id}:mp3:10"
            )
        ],
        [
            InlineKeyboardButton(
                text="📦 Скачать архивом ZIP (MP3)",
                callback_data=f"pl:{task_id}:zip:10"
            )
        ],
        [
            InlineKeyboardButton(
                text="🎬 Скачать первое видео",
                callback_data=f"pl:{task_id}:v1"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"pl:{task_id}:cancel"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_trim_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """Keyboard shown when prompting user for timecode."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена нарезки", callback_data=f"dl:{task_id}:cancel_trim")]
    ])
