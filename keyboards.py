from typing import List, Optional, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils import format_size


def create_download_keyboard(
    task_id: str,
    resolutions: Optional[List[int]] = None,
    is_youtube: bool = False,
    estimated_sizes: Optional[Dict[str, Any]] = None
) -> InlineKeyboardMarkup:
    """Builds an inline keyboard with quality and format options, displaying estimated file sizes."""
    keyboard: List[List[InlineKeyboardButton]] = []

    res_sizes = (estimated_sizes or {}).get("resolutions", {})
    audio_size = (estimated_sizes or {}).get("audio_size")
    voice_size = (estimated_sizes or {}).get("voice_size")
    video_best_size = (estimated_sizes or {}).get("video_best")

    if is_youtube and resolutions:
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
    else:
        # TikTok or platform without multiple resolutions
        label = "🎬 Скачать видео (HD)"
        if video_best_size:
            label += f" • ~{format_size(video_best_size)}"

        keyboard.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"dl:{task_id}:v:best"
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

    # Cancel button
    keyboard.append([
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"dl:{task_id}:cancel"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)
