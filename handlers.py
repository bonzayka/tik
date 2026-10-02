import html
import logging
from pathlib import Path
from typing import Optional

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    FSInputFile,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent
)
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatAction

from config import MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB, HAS_MTPROTO
from mtproto_uploader import uploader as mtproto_uploader, UploadProgressTracker
from downloader import (
    get_video_info,
    download_video,
    download_audio,
    download_voice,
    cleanup_task_dir,
    trim_media,
    is_playlist_url,
    get_playlist_info,
    download_playlist_mp3
)
from keyboards import (
    create_download_keyboard,
    create_playlist_keyboard,
    create_trim_keyboard
)
from task_manager import task_manager
from utils import (
    find_first_url,
    format_duration,
    format_size,
    detect_platform,
    parse_time_range
)

logger = logging.getLogger(__name__)
router = Router()

STANDARD_LIMIT = 50 * 1024 * 1024  # Standard Telegram Bot API upload limit


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handles /start command with a welcome message."""
    text = (
        "👋 <b>Привет! Я всеядный бот для скачивания видео, фото и аудио в высоком качестве.</b>\n\n"
        "🌐 <b>Поддерживаемые платформы:</b>\n"
        "• 🎬 <b>YouTube & Shorts</b> — выбор качества от 360p до <b>1080p, 2K и 4K</b>\n"
        "• 📱 <b>TikTok</b> — видео в лучшем качестве без водяного знака\n"
        "• 📸 <b>Instagram</b> — Reels, Stories, фото и карусели\n"
        "• 🔵 <b>VK Видео & Клипы</b> — видео с выбором разрешения\n"
        "• 📌 <b>Pinterest</b> — видео-пины и оригиналы картинок\n"
        "• 🐦 <b>Twitter (X) & Reddit</b> — ролики в максимальном качестве со звуком\n\n"
        "🚀 <b>Киллер-фичи:</b>\n"
        "• 🎵 <b>MP3 с обложками</b> — квадратные обложки и правильные ID3-теги (как в Spotify)\n"
        "• 🎙 <b>Голосовые сообщения (ГС)</b> — в формате голосовых Telegram\n"
        "• ✂️ <b>Нарезка по таймкодам</b> — скачивание нужного отрезка ролика\n"
        "• 📑 <b>Плейлисты YouTube</b> — поштучно или единым ZIP-архивом\n"
        "• ⚡️ <b>Файлы до 2 ГБ</b> — скоростная отправка через MTProto\n\n"
        "💡 <b>Как пользоваться:</b>\n"
        "Просто отправьте ссылку на видео, фото или плейлист!"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Handles /help command."""
    text = (
        "ℹ️ <b>Справка по использованию:</b>\n\n"
        "1. Отправьте ссылку из <b>YouTube</b>, <b>TikTok</b>, <b>Instagram</b>, <b>VK</b>, <b>Pinterest</b>, <b>Twitter (X)</b> или <b>Reddit</b>.\n"
        "2. Выберите нужное качество или формат.\n\n"
        "✂️ <b>Нарезка видео/аудио:</b>\n"
        "• Укажите таймкод вместе с ссылкой: <code>https://... 01:15-02:30</code>\n"
        "• Или нажмите кнопку <b>«✂️ Нарезать фрагмент»</b> под сообщением с медиа.\n\n"
        "📑 <b>Плейлисты YouTube:</b>\n"
        "• Отправьте ссылку на плейлист — бот предложит скачать треки или упаковать в ZIP архив.\n\n"
        "🔍 <b>Инлайн-режим:</b>\n"
        "• Напишите <code>@имя_бота &lt;ссылка&gt;</code> в любом чате или переписке.\n\n"
        "⚡️ <b>Лимиты:</b>\n"
        "Бот поддерживает отправку файлов до <b>2000 МБ (2 ГБ)</b>!"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(F.text)
async def handle_url_message(message: Message):
    """Detects URLs, playlists, and timecodes in text and displays options."""
    text = message.text.strip()
    url = find_first_url(text)
    time_range = parse_time_range(text)

    # If message has no URL but contains timecode: apply to recent task for this chat
    if not url and time_range:
        latest = task_manager.get_latest_task_for_chat(message.chat.id)
        if latest:
            t_id, t_data = latest
            t_data["time_range"] = time_range
            s_sec, e_sec = time_range
            frag_str = f"{format_duration(s_sec)} — {format_duration(e_sec)} ({format_duration(e_sec - s_sec)})"

            kb = create_download_keyboard(
                task_id=t_id,
                resolutions=t_data.get("resolutions", []),
                is_youtube=t_data.get("is_youtube", False),
                estimated_sizes=t_data.get("estimated_sizes"),
                has_time_range=True
            )
            await message.reply(
                f"✂️ <b>Фрагмент выбран:</b> <code>{frag_str}</code>\n\n"
                f"📌 <b>{html.escape(t_data.get('title', ''))}</b>\n\n"
                f"👇 <i>Выберите, в каком формате скачать фрагмент:</i>",
                reply_markup=kb,
                parse_mode="HTML"
            )
            return

    if not url:
        await message.reply(
            "❌ Ссылка не найдена.\nПожалуйста, отправьте ссылку на видео из <b>YouTube</b>, <b>TikTok</b> или <b>плейлист</b>.",
            parse_mode="HTML"
        )
        return

    # Check if URL is a YouTube Playlist
    if is_playlist_url(url):
        status_msg = await message.reply("🔎 <i>Получаю информацию о плейлисте...</i>", parse_mode="HTML")
        try:
            pl_info = await get_playlist_info(url)
            if pl_info.get("entries"):
                pl_task_id = task_manager.create_task({
                    "type": "playlist",
                    "url": url,
                    "title": pl_info["title"],
                    "uploader": pl_info["uploader"],
                    "count": pl_info["count"],
                    "entries": pl_info["entries"],
                    "chat_id": message.chat.id
                })
                keyboard = create_playlist_keyboard(pl_task_id, pl_info["count"])
                caption = (
                    f"📑 <b>YouTube Плейлист</b>\n"
                    f"📌 <b>{html.escape(pl_info['title'])}</b>\n\n"
                    f"👤 <b>Автор:</b> {html.escape(pl_info['uploader'])}\n"
                    f"🔢 <b>Всего треков/видео:</b> {pl_info['count']}\n\n"
                    f"👇 <i>Выберите вариант скачивания:</i>"
                )
                await status_msg.edit_text(caption, reply_markup=keyboard, parse_mode="HTML")
                return
        except Exception as e:
            logger.warning(f"Playlist extraction failed, falling back to single video: {e}")
        try:
            await status_msg.delete()
        except Exception:
            pass

    status_msg = await message.reply("🔎 <i>Получаю информацию о видео...</i>", parse_mode="HTML")

    # Fetch metadata
    info = await get_video_info(url)
    if not info.get("success"):
        err = html.escape(str(info.get("error", "Неизвестная ошибка")))
        if any(w in err.lower() for w in ["bot", "sign in", "cookies", "confirm"]):
            await status_msg.edit_text(
                "⚠️ <b>YouTube заблокировал IP-адрес сервера (Anti-Bot)</b>\n\n"
                "Сервер находится в дата-центре, и YouTube заблокировал доступ без куки.\n\n"
                "💡 <b>Как решить (выберите один из вариантов):</b>\n"
                "1. <b>Файл куки:</b> сохраните куки из браузера (расширение <i>Get cookies.txt LOCALLY</i> или <i>Cookie-Editor</i>) и положите <code>cookies.txt</code> (или <code>cookies.json</code>) в папку бота на сервере.\n"
                "2. <b>Прокси:</b> укажите прокси в файле <code>.env</code> (параметр <code>PROXY=http://login:pass@ip:port</code>).\n\n"
                f"<i>Детали: {err}</i>",
                parse_mode="HTML"
            )
        else:
            await status_msg.edit_text(
                f"❌ <b>Не удалось загрузить информацию о видео.</b>\n\n"
                f"Возможно, видео приватное, удалено или ссылка некорректна.\n"
                f"<i>Детали: {err}</i>",
                parse_mode="HTML"
            )
        return

    if info.get("is_live"):
        await status_msg.edit_text(
            "⚠️ <b>Прямые трансляции (стримы) не поддерживаются для скачивания.</b>",
            parse_mode="HTML"
        )
        return

    title = info.get("title", "Без названия")
    uploader = info.get("uploader", "Неизвестный автор")
    duration = info.get("duration", 0)
    thumbnail = info.get("thumbnail")
    extractor = info.get("extractor", "")
    resolutions = info.get("resolutions", [])
    estimated_sizes = info.get("estimated_sizes", {})
    platform_name = detect_platform(url, extractor)
    is_youtube = "youtube" in extractor

    task_id = task_manager.create_task({
        "url": url,
        "title": title,
        "uploader": uploader,
        "duration": duration,
        "extractor": extractor,
        "chat_id": message.chat.id,
        "resolutions": resolutions,
        "estimated_sizes": estimated_sizes,
        "is_youtube": is_youtube,
        "time_range": time_range
    })

    keyboard = create_download_keyboard(
        task_id=task_id,
        resolutions=resolutions,
        is_youtube=is_youtube,
        estimated_sizes=estimated_sizes,
        has_time_range=bool(time_range)
    )

    size_lines = []
    res_sizes = (estimated_sizes or {}).get("resolutions", {})
    if is_youtube and resolutions:
        for r in resolutions:
            s = res_sizes.get(r)
            badge = " (4K)" if r >= 2160 else (" (2K)" if r >= 1440 else (" (FHD)" if r >= 1080 else (" (HD)" if r == 720 else "")))
            if s:
                size_lines.append(f"• <b>{r}p{badge}:</b> ~{format_size(s)}")

        audio_s = (estimated_sizes or {}).get("audio_size")
        if audio_s:
            size_lines.append(f"• <b>MP3 Аудио:</b> ~{format_size(audio_s)}")

    sizes_text = ""
    if size_lines:
        sizes_text = "\n📊 <b>Примерный вес:</b>\n" + "\n".join(size_lines) + "\n"

    trim_info = ""
    if time_range:
        s_sec, e_sec = time_range
        trim_info = f"\n✂️ <b>Выбран фрагмент:</b> <code>{format_duration(s_sec)} — {format_duration(e_sec)}</code>\n"

    caption_text = (
        f"{platform_name}\n"
        f"📌 <b>{html.escape(title)}</b>\n\n"
        f"👤 <b>Автор:</b> {html.escape(uploader)}\n"
        f"⏱ <b>Длительность:</b> {format_duration(duration)}\n"
        f"{trim_info}"
        f"{sizes_text}\n"
        f"👇 <i>Выберите, в каком формате скачать:</i>"
    )

    try:
        if thumbnail:
            await status_msg.delete()
            await message.answer_photo(
                photo=thumbnail,
                caption=caption_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await status_msg.edit_text(
                caption_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.warning(f"Failed to send thumbnail photo: {e}. Falling back to text.")
        await status_msg.edit_text(
            caption_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("dl:"))
async def handle_download_callback(callback: CallbackQuery):
    """Handles download buttons from inline keyboard."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверный запрос.")
        return

    task_id = parts[1]
    action_type = parts[2]

    # Handle Cancel
    if action_type == "cancel":
        task_manager.remove_task(task_id)
        await callback.answer("Отменено")
        try:
            await callback.message.delete()
        except Exception:
            await callback.message.edit_reply_markup(reply_markup=None)
        return

    # Handle Trimming prompt
    if action_type == "trim":
        await callback.answer()
        await callback.message.reply(
            "✂️ <b>Нарезка видео или аудио</b>\n\n"
            "Пришлите нужный временной отрезок ответным сообщением (или просто текстом в чат).\n\n"
            "<b>Примеры форматов:</b>\n"
            "• <code>01:15-02:40</code> (с 1 мин 15 сек до 2 мин 40 сек)\n"
            "• <code>00:30-01:00</code>\n"
            "• <code>45-90</code> (в секундах)\n\n"
            "<i>После ввода отрезка бот обновит кнопки для скачивания фрагмента!</i>",
            reply_markup=create_trim_keyboard(task_id),
            parse_mode="HTML"
        )
        return

    if action_type == "cancel_trim":
        await callback.answer("Нарезка отменена")
        try:
            await callback.message.delete()
        except Exception:
            pass
        return

    task = task_manager.get_task(task_id)
    if not task:
        await callback.answer(
            "⚠️ Срок действия этой ссылки истёк. Пожалуйста, отправьте ссылку заново.",
            show_alert=True
        )
        return

    url = task["url"]
    title = task["title"]
    uploader = task["uploader"]
    duration = task["duration"]
    time_range = task.get("time_range")
    chat_id = callback.message.chat.id
    bot = callback.bot

    await callback.answer()

    progress_msg = await callback.message.reply(
        "⏳ <b>Начинаю обработку...</b> Пожалуйста, подождите.",
        parse_mode="HTML"
    )

    task_dir: Path | None = None

    try:
        if action_type == "v":
            # Video download
            res_str = parts[3] if len(parts) > 3 else "best"
            height = int(res_str) if res_str.isdigit() else None
            if height:
                if height >= 2160:
                    quality_label = f"{height}p (4K)"
                elif height >= 1440:
                    quality_label = f"{height}p (2K)"
                elif height >= 1080:
                    quality_label = f"{height}p (FHD)"
                elif height == 720:
                    quality_label = f"{height}p (HD)"
                else:
                    quality_label = f"{height}p"
            else:
                quality_label = "HD"

            await progress_msg.edit_text(
                f"⏳ <b>Скачиваю видео ({quality_label})...</b>\n<i>Это может занять некоторое время.</i>",
                parse_mode="HTML"
            )
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

            res, task_dir = await download_video(url, height=height)
            filepath = res["filepath"]
            filesize = res["filesize"]
            video_duration = res.get("duration") or duration
            width = res.get("width")
            video_height = res.get("height")
            media_type = res.get("media_type", "video")
            media_files = res.get("files", [filepath])

            # Handle single photo (Instagram Photo, Pinterest Image Pin)
            if media_type == "photo":
                await progress_msg.edit_text("📤 <i>Отправляю фотографию...</i>", parse_mode="HTML")
                photo_caption = f"📸 <b>{html.escape(title)}</b>\n\n👤 {html.escape(uploader)}"
                await callback.message.reply_photo(
                    photo=FSInputFile(filepath),
                    caption=photo_caption,
                    parse_mode="HTML"
                )
                try:
                    await progress_msg.delete()
                except Exception:
                    pass
                return

            # Handle carousel (multiple photos/videos, e.g. Instagram Carousel)
            if media_type == "carousel":
                await progress_msg.edit_text(f"📤 <i>Отправляю альбом из {len(media_files)} файлов...</i>", parse_mode="HTML")
                from aiogram.types import InputMediaPhoto, InputMediaVideo
                group = []
                image_exts = {'.jpg', '.jpeg', '.png', '.webp'}
                base_caption = f"📸 <b>{html.escape(title)}</b>\n\n👤 {html.escape(uploader)}"
                for idx, fpath in enumerate(media_files[:10]):
                    is_img = Path(fpath).suffix.lower() in image_exts
                    cap = base_caption if idx == 0 else None
                    if is_img:
                        group.append(InputMediaPhoto(media=FSInputFile(fpath), caption=cap, parse_mode="HTML"))
                    else:
                        group.append(InputMediaVideo(media=FSInputFile(fpath), caption=cap, parse_mode="HTML"))
                if group:
                    await callback.message.reply_media_group(media=group)
                try:
                    await progress_msg.delete()
                except Exception:
                    pass
                return

            # Apply Trimming if time_range was specified
            if time_range:
                s_sec, e_sec = time_range
                await progress_msg.edit_text(
                    f"✂️ <i>Обрезаю фрагмент ({format_duration(s_sec)} — {format_duration(e_sec)})...</i>",
                    parse_mode="HTML"
                )
                trimmed_file = task_dir / f"trimmed_{Path(filepath).name}"
                if trim_media(filepath, str(trimmed_file), s_sec, e_sec, is_video=True):
                    filepath = str(trimmed_file)
                    filesize = trimmed_file.stat().st_size
                    video_duration = e_sec - s_sec

            if filesize > MAX_FILE_SIZE_BYTES:
                await progress_msg.edit_text(
                    f"⚠️ <b>Файл слишком большой для отправки через Telegram!</b>\n\n"
                    f"Размер файла: <code>{format_size(filesize)}</code> (лимит бота — {MAX_FILE_SIZE_MB} МБ).\n\n"
                    f"💡 <i>Попробуйте выбрать качество ниже (например 720p или 480p), либо скачайте только аудио/ГС.</i>",
                    parse_mode="HTML"
                )
                return

            trim_tag = f"\n✂️ <i>Фрагмент: {format_duration(time_range[0])} — {format_duration(time_range[1])}</i>" if time_range else ""
            video_caption = (
                f"🎬 <b>{html.escape(title)}</b>\n\n"
                f"👤 {html.escape(uploader)}\n"
                f"⏱ {format_duration(video_duration)} | 📦 {format_size(filesize)}"
                f"{trim_tag}"
            )

            if filesize > STANDARD_LIMIT:
                if mtproto_uploader.is_available:
                    tracker = UploadProgressTracker(progress_msg, filesize, "видео")
                    await progress_msg.edit_text(
                        f"📤 <i>Загружаю большой файл ({format_size(filesize)}) через MTProto (до 2 ГБ)...</i>\n"
                        f"<i>Включена скоростная параллельная отправка 🚀</i>",
                        parse_mode="HTML"
                    )
                    await mtproto_uploader.send_video(
                        chat_id=chat_id,
                        filepath=filepath,
                        caption=video_caption,
                        duration=int(video_duration) if video_duration else 0,
                        width=width if width else 0,
                        height=video_height if video_height else 0,
                        progress_callback=tracker
                    )
                    try:
                        await progress_msg.delete()
                    except Exception:
                        pass
                else:
                    await progress_msg.edit_text(
                        f"⚠️ <b>Файл превышает 50 МБ ({format_size(filesize)}).</b>\n\n"
                        f"Для загрузки файлов до 2 ГБ проверьте настройки API_ID и API_HASH.",
                        parse_mode="HTML"
                    )
                    return
            else:
                await progress_msg.edit_text("📤 <i>Отправляю видео в Telegram...</i>", parse_mode="HTML")
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

                await callback.message.reply_video(
                    video=FSInputFile(filepath),
                    caption=video_caption,
                    duration=int(video_duration) if video_duration else None,
                    width=width if width else None,
                    height=video_height if video_height else None,
                    supports_streaming=True,
                    parse_mode="HTML"
                )
                try:
                    await progress_msg.delete()
                except Exception:
                    pass

        elif action_type == "audio":
            # Audio download as MP3 with album art and ID3 metadata
            await progress_msg.edit_text(
                "⏳ <b>Извлекаю аудио в формате MP3...</b>\n<i>Добавляю обложку и метаданные трека...</i>",
                parse_mode="HTML"
            )
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)

            res, task_dir = await download_audio(url)
            filepath = res["filepath"]
            filesize = res["filesize"]
            audio_duration = res.get("duration") or duration
            thumb_path = res.get("thumb_path")

            # Apply Trimming if time_range was specified
            if time_range:
                s_sec, e_sec = time_range
                await progress_msg.edit_text(
                    f"✂️ <i>Обрезаю аудио ({format_duration(s_sec)} — {format_duration(e_sec)})...</i>",
                    parse_mode="HTML"
                )
                trimmed_file = task_dir / f"trimmed_{Path(filepath).name}"
                if trim_media(filepath, str(trimmed_file), s_sec, e_sec, is_video=False):
                    filepath = str(trimmed_file)
                    filesize = trimmed_file.stat().st_size
                    audio_duration = e_sec - s_sec

            if filesize > MAX_FILE_SIZE_BYTES:
                await progress_msg.edit_text(
                    f"⚠️ <b>Аудиофайл превышает {MAX_FILE_SIZE_MB} МБ ({format_size(filesize)}).</b>",
                    parse_mode="HTML"
                )
                return

            trim_tag = f"\n✂️ <i>Фрагмент: {format_duration(time_range[0])} — {format_duration(time_range[1])}</i>" if time_range else ""
            audio_caption = f"🎵 <b>{html.escape(title)}</b>\n👤 {html.escape(uploader)}{trim_tag}"

            if filesize > STANDARD_LIMIT:
                if mtproto_uploader.is_available:
                    tracker = UploadProgressTracker(progress_msg, filesize, "аудио")
                    await progress_msg.edit_text(
                        f"📤 <i>Загружаю аудиофайл ({format_size(filesize)}) через MTProto...</i>\n"
                        f"<i>Включена скоростная параллельная отправка 🚀</i>",
                        parse_mode="HTML"
                    )
                    await mtproto_uploader.send_audio(
                        chat_id=chat_id,
                        filepath=filepath,
                        caption=audio_caption,
                        title=title,
                        performer=uploader,
                        duration=int(audio_duration) if audio_duration else 0,
                        thumb_path=thumb_path,
                        progress_callback=tracker
                    )
                    try:
                        await progress_msg.delete()
                    except Exception:
                        pass
                else:
                    await progress_msg.edit_text(
                        f"⚠️ <b>Аудиофайл превышает 50 МБ ({format_size(filesize)}).</b>",
                        parse_mode="HTML"
                    )
                    return
            else:
                await progress_msg.edit_text("📤 <i>Отправляю аудиозапись...</i>", parse_mode="HTML")
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)

                await callback.message.reply_audio(
                    audio=FSInputFile(filepath),
                    thumbnail=FSInputFile(thumb_path) if thumb_path and Path(thumb_path).exists() else None,
                    title=title,
                    performer=uploader,
                    duration=int(audio_duration) if audio_duration else None,
                    caption=audio_caption,
                    parse_mode="HTML"
                )
                try:
                    await progress_msg.delete()
                except Exception:
                    pass

        elif action_type == "voice":
            # Voice note conversion (OGG Opus)
            await progress_msg.edit_text(
                "🎙 <b>Создаю голосовое сообщение (ГС)...</b>\n<i>Конвертация в OGG Opus...</i>",
                parse_mode="HTML"
            )
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

            res, task_dir = await download_voice(url)
            filepath = res["filepath"]
            filesize = res["filesize"]
            voice_duration = res.get("duration") or duration

            # Apply Trimming if time_range was specified
            if time_range:
                s_sec, e_sec = time_range
                await progress_msg.edit_text(
                    f"✂️ <i>Обрезаю голосовое ({format_duration(s_sec)} — {format_duration(e_sec)})...</i>",
                    parse_mode="HTML"
                )
                trimmed_file = task_dir / "trimmed_voice.ogg"
                if trim_media(filepath, str(trimmed_file), s_sec, e_sec, is_video=False):
                    filepath = str(trimmed_file)
                    filesize = trimmed_file.stat().st_size
                    voice_duration = e_sec - s_sec

            if filesize > MAX_FILE_SIZE_BYTES:
                await progress_msg.edit_text(
                    f"⚠️ <b>Голосовое сообщение превышает {MAX_FILE_SIZE_MB} МБ ({format_size(filesize)}).</b>",
                    parse_mode="HTML"
                )
                return

            trim_tag = f"\n✂️ <i>Фрагмент: {format_duration(time_range[0])} — {format_duration(time_range[1])}</i>" if time_range else ""
            voice_caption = f"🎙 <b>{html.escape(title)}</b>{trim_tag}"

            if filesize > STANDARD_LIMIT:
                if mtproto_uploader.is_available:
                    tracker = UploadProgressTracker(progress_msg, filesize, "голосового сообщения")
                    await progress_msg.edit_text(
                        f"📤 <i>Загружаю голосовое ({format_size(filesize)}) через MTProto...</i>\n"
                        f"<i>Включена скоростная параллельная отправка 🚀</i>",
                        parse_mode="HTML"
                    )
                    await mtproto_uploader.send_voice(
                        chat_id=chat_id,
                        filepath=filepath,
                        caption=voice_caption,
                        duration=int(voice_duration) if voice_duration else 0,
                        progress_callback=tracker
                    )
                    try:
                        await progress_msg.delete()
                    except Exception:
                        pass
                else:
                    await progress_msg.edit_text(
                        f"⚠️ <b>Голосовое сообщение превышает 50 МБ ({format_size(filesize)}).</b>",
                        parse_mode="HTML"
                    )
                    return
            else:
                await progress_msg.edit_text("📤 <i>Отправляю голосовое сообщение...</i>", parse_mode="HTML")
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)

                await callback.message.reply_voice(
                    voice=FSInputFile(filepath),
                    duration=int(voice_duration) if voice_duration else None,
                    caption=voice_caption,
                    parse_mode="HTML"
                )
                try:
                    await progress_msg.delete()
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"Error processing download callback: {e}", exc_info=True)
        err_msg = html.escape(str(e))
        if any(w in err_msg.lower() for w in ["bot", "sign in", "cookies", "confirm"]):
            user_friendly = (
                "⚠️ <b>YouTube заблокировал скачивание с IP-адреса сервера (Anti-Bot)</b>\n\n"
                "IP-адрес вашего сервера (дата-центра) временно ограничен YouTube для загрузки медиапотоков.\n\n"
                "💡 <b>Как решить (выберите один из вариантов):</b>\n"
                "1. <b>Файл куки:</b> сохраните куки из браузера (расширение <i>Get cookies.txt LOCALLY</i> или <i>Cookie-Editor</i>) и положите файл <code>cookies.txt</code> (или <code>cookies.json</code>) в папку бота на сервере.\n"
                "2. <b>Прокси:</b> укажите прокси в файле <code>.env</code> (параметр <code>PROXY=http://login:pass@ip:port</code>).\n\n"
                f"<i>Техническая ошибка: {err_msg}</i>"
            )
            await progress_msg.edit_text(user_friendly, parse_mode="HTML")
        else:
            await progress_msg.edit_text(
                f"❌ <b>Произошла ошибка при обработке:</b>\n<code>{err_msg}</code>",
                parse_mode="HTML"
            )
    finally:
        if task_dir:
            cleanup_task_dir(task_dir)


@router.callback_query(F.data.startswith("pl:"))
async def handle_playlist_callback(callback: CallbackQuery):
    """Handles playlist actions (top 5, top 10, zip archive, first video)."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return

    task_id = parts[1]
    action = parts[2]
    task = task_manager.get_task(task_id)

    if action == "cancel":
        task_manager.remove_task(task_id)
        await callback.answer("Отменено")
        try:
            await callback.message.delete()
        except Exception:
            pass
        return

    if not task:
        await callback.answer("⚠️ Срок действия плейлиста истёк. Отправьте ссылку заново.", show_alert=True)
        return

    await callback.answer()
    entries = task.get("entries", [])
    playlist_title = task.get("title", "Плейлист")
    chat_id = callback.message.chat.id
    bot = callback.bot

    if action == "v1":
        if not entries:
            await callback.message.reply("В плейлисте нет видео.")
            return
        v1_url = entries[0]["url"]
        status_msg = await callback.message.reply("⏳ <i>Загружаю первое видео из плейлиста...</i>", parse_mode="HTML")
        info = await get_video_info(v1_url)
        if not info.get("success"):
            await status_msg.edit_text("❌ Не удалось получить видео.")
            return

        v_task_id = task_manager.create_task({
            "url": v1_url,
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "extractor": info.get("extractor"),
            "chat_id": chat_id,
            "resolutions": info.get("resolutions", []),
            "estimated_sizes": info.get("estimated_sizes"),
            "is_youtube": True
        })
        kb = create_download_keyboard(
            v_task_id,
            info.get("resolutions", []),
            is_youtube=True,
            estimated_sizes=info.get("estimated_sizes")
        )
        await status_msg.edit_text(
            f"🎬 <b>{html.escape(info['title'])}</b>\n\n"
            f"⏱ <b>Длительность:</b> {format_duration(info.get('duration'))}\n\n"
            f"👇 <i>Выберите качество:</i>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return

    if action in ["mp3", "zip"]:
        count = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 5
        target_entries = entries[:count]
        as_zip = (action == "zip")

        label = "ZIP архив" if as_zip else f"{len(target_entries)} треков"
        progress_msg = await callback.message.reply(
            f"⏳ <b>Готовлю {label} из плейлиста...</b>\n"
            f"<i>Каждый трек будет с обложкой и тегами! Это займет некоторое время.</i>",
            parse_mode="HTML"
        )
        task_dir: Optional[Path] = None

        try:
            res, task_dir = await download_playlist_mp3(target_entries, as_zip=as_zip, playlist_title=playlist_title)

            if as_zip:
                zip_path = res["filepath"]
                filesize = res["filesize"]
                caption = f"📦 <b>{html.escape(playlist_title)}</b>\n🎵 Треков: {res['count']} | 📦 {format_size(filesize)}"

                if filesize > STANDARD_LIMIT and mtproto_uploader.is_available:
                    tracker = UploadProgressTracker(progress_msg, filesize, "ZIP архива")
                    await progress_msg.edit_text("📤 <i>Загружаю ZIP архив через MTProto...</i>", parse_mode="HTML")
                    await mtproto_uploader.send_video(
                        chat_id=chat_id,
                        filepath=zip_path,
                        caption=caption,
                        progress_callback=tracker
                    )
                else:
                    await callback.message.reply_document(
                        document=FSInputFile(zip_path),
                        caption=caption,
                        parse_mode="HTML"
                    )
                try:
                    await progress_msg.delete()
                except Exception:
                    pass
            else:
                tracks = res["tracks"]
                await progress_msg.edit_text(f"📤 <i>Отправляю {len(tracks)} аудиозаписей в чат...</i>", parse_mode="HTML")

                for item in tracks:
                    fpath = item["filepath"]
                    t_title = item.get("title", "Audio")
                    t_artist = item.get("uploader", "Artist")
                    t_dur = item.get("duration", 0)
                    t_thumb = item.get("thumb_path")
                    t_size = item.get("filesize", 0)

                    if t_size > STANDARD_LIMIT and mtproto_uploader.is_available:
                        await mtproto_uploader.send_audio(
                            chat_id=chat_id,
                            filepath=fpath,
                            caption=f"🎵 <b>{html.escape(t_title)}</b>\n👤 {html.escape(t_artist)}",
                            title=t_title,
                            performer=t_artist,
                            duration=int(t_dur) if t_dur else 0,
                            thumb_path=t_thumb
                        )
                    else:
                        await callback.message.reply_audio(
                            audio=FSInputFile(fpath),
                            thumbnail=FSInputFile(t_thumb) if t_thumb and Path(t_thumb).exists() else None,
                            title=t_title,
                            performer=t_artist,
                            duration=int(t_dur) if t_dur else None,
                            caption=f"🎵 <b>{html.escape(t_title)}</b>",
                            parse_mode="HTML"
                        )
                try:
                    await progress_msg.delete()
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error downloading playlist: {e}", exc_info=True)
            await progress_msg.edit_text(
                f"❌ <b>Произошла ошибка при обработке плейлиста:</b>\n<code>{html.escape(str(e))}</code>",
                parse_mode="HTML"
            )
        finally:
            if task_dir:
                cleanup_task_dir(task_dir)


@router.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    """Allows using the bot in any chat via inline mode (@bot_name <url>)."""
    query = inline_query.query.strip()
    url = find_first_url(query)

    if not url:
        await inline_query.answer(
            results=[
                InlineQueryResultArticle(
                    id="help",
                    title="Вставьте ссылку на YouTube или TikTok",
                    description="Пример: @bot_name https://youtu.be/...",
                    input_message_content=InputTextMessageContent(
                        message_text="👋 Отправьте мне ссылку на YouTube или TikTok, чтобы скачать видео или аудио в высоком качестве!"
                    )
                )
            ],
            cache_time=5,
            is_personal=True
        )
        return

    results = [
        InlineQueryResultArticle(
            id="video",
            title="🎬 Скачать видео",
            description=f"Отправить ссылку на видео: {url}",
            input_message_content=InputTextMessageContent(
                message_text=f"{url}"
            )
        ),
        InlineQueryResultArticle(
            id="audio",
            title="🎵 Скачать MP3 Аудио",
            description=f"Извлечь MP3: {url}",
            input_message_content=InputTextMessageContent(
                message_text=f"{url}"
            )
        )
    ]
    await inline_query.answer(results=results, cache_time=10, is_personal=True)
