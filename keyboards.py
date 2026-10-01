from typing import List, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def create_download_keyboard(
    task_id: str,
    resolutions: Optional[List[int]] = None,
    is_youtube: bool = False
) -> InlineKeyboardMarkup:
    """Builds an inline keyboard with quality and format options."""
    keyboard: List[List[InlineKeyboardButton]] = []

    if is_youtube and resolutions:
        # Group resolutions in pairs
        quality_row: List[InlineKeyboardButton] = []
        for res in resolutions:
            label = f"🎬 {res}p"
            if res >= 1080:
                label += " (FHD)"
            elif res == 720:
                label += " (HD)"
            
            btn = InlineKeyboardButton(
                text=label,
                callback_data=f"dl:{task_id}:v:{res}"
            )
            quality_row.append(btn)
            if len(quality_row) == 2:
                keyboard.append(quality_row)
                quality_row = []
        if quality_row:
            keyboard.append(quality_row)
    else:
        # TikTok or platform without multiple resolutions
        keyboard.append([
            InlineKeyboardButton(
                text="🎬 Скачать видео (HD)",
                callback_data=f"dl:{task_id}:v:best"
            )
        ])

    # Audio & Voice note row
    keyboard.append([
        InlineKeyboardButton(
            text="🎵 Аудио (MP3)",
            callback_data=f"dl:{task_id}:audio"
        ),
        InlineKeyboardButton(
            text="🎙 Голосовое (ГС)",
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
