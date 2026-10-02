import html
import logging
from pathlib import Path
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatAction
from config import MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB, HAS_MTPROTO
from mtproto_uploader import uploader as mtproto_uploader, UploadProgressTracker
from downloader import (
    get_video_info,
    download_video,
    download_audio,
    download_voice,
    cleanup_task_dir
)
from keyboards import create_download_keyboard
from task_manager import task_manager
from utils import find_first_url, format_duration, format_size, detect_platform

logger = logging.getLogger(__name__)
router = Router()

STANDARD_LIMIT = 50 * 1024 * 1024  # Standard Telegram Bot API upload limit

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handles /start command with a welcome message."""
    text = (
        "👋 <b>Привет! Я бот для скачивания видео и аудио.</b>\n\n"
        "🚀 <b>Мои возможности:</b>\n"
        "• 🎬 <b>YouTube & Shorts</b> — выбор качества (1080p, 720p, 480p, 360p)\n"
        "• 📱 <b>TikTok</b> — скачивание в лучшем качестве без водяного знака\n"
        "• 🎵 <b>Извлечение аудио</b> — конвертация любого видео в MP3\n"
        "• 🎙 <b>Голосовое сообщение (ГС)</b> — преобразование в настоящее голосовое Telegram с визуальной дорожкой\n\n"
        "💡 <b>Как пользоваться:</b>\n"
        "Просто отправь мне ссылку на видео из <b>YouTube</b> или <b>TikTok</b>!"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Handles /help command."""
    text = (
        "ℹ️ <b>Справка по использованию:</b>\n\n"
        "1. Скопируй ссылку на видео из YouTube или TikTok.\n"
        "2. Отправь её сюда сообщением.\n"
        "3. Бот покажет превью ролика и кнопки с выбором:\n"
        "   — Разрешение видео (1080p / 720p / 480p / 360p)\n"
        "   — Аудио в формате MP3\n"
        "   — Голосовое сообщение (ГС)\n\n"
        "⚠️ <i>Примечание: Лимит Telegram на загрузку файлов ботом составляет 50 МБ. "
        "Для длинных видео в 1080p рекомендуется выбирать 720p/480p или MP3.</i>"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(F.text)
async def handle_url_message(message: Message):
    """Detects URLs in text and displays download options."""
    url = find_first_url(message.text)
    if not url:
        await message.reply(
            "❌ Ссылка не найдена.\nПожалуйста, отправьте ссылку на видео из <b>YouTube</b> или <b>TikTok</b>.",
            parse_mode="HTML"
        )
        return

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

    # Extract fields
    title = info.get("title", "Без названия")
    uploader = info.get("uploader", "Неизвестный автор")
    duration = info.get("duration", 0)
    thumbnail = info.get("thumbnail")
    extractor = info.get("extractor", "")
    resolutions = info.get("resolutions", [])
    estimated_sizes = info.get("estimated_sizes", {})
    platform_name = detect_platform(url, extractor)
    is_youtube = "youtube" in extractor

    # Save to task manager
    task_id = task_manager.create_task({
        "url": url,
        "title": title,
        "uploader": uploader,
        "duration": duration,
        "extractor": extractor,
        "chat_id": message.chat.id
    })

    keyboard = create_download_keyboard(
        task_id=task_id,
        resolutions=resolutions,
        is_youtube=is_youtube,
        estimated_sizes=estimated_sizes
    )

    # Format optional size breakdown in description
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

    caption_text = (
        f"{platform_name}\n"
        f"📌 <b>{html.escape(title)}</b>\n\n"
        f"👤 <b>Автор:</b> {html.escape(uploader)}\n"
        f"⏱ <b>Длительность:</b> {format_duration(duration)}\n"
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

    # Check task existence
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
    chat_id = callback.message.chat.id
    bot = callback.bot

    await callback.answer()

    # Progress message
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

            # Check Telegram limit
            if filesize > MAX_FILE_SIZE_BYTES:
                await progress_msg.edit_text(
                    f"⚠️ <b>Файл слишком большой для отправки через Telegram!</b>\n\n"
                    f"Размер файла: <code>{format_size(filesize)}</code> (лимит бота — {MAX_FILE_SIZE_MB} МБ).\n\n"
                    f"💡 <i>Попробуйте выбрать качество ниже (например 720p или 480p), либо скачайте только аудио/ГС.</i>",
                    parse_mode="HTML"
                )
                return

            video_caption = (
                f"🎬 <b>{html.escape(title)}</b>\n\n"
                f"👤 {html.escape(uploader)}\n"
                f"⏱ {format_duration(video_duration)} | 📦 {format_size(filesize)}"
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
                await progress_msg.delete()

        elif action_type == "audio":
            # Audio download
            await progress_msg.edit_text(
                "⏳ <b>Извлекаю аудио в формате MP3...</b>",
                parse_mode="HTML"
            )
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)

            res, task_dir = await download_audio(url)
            filepath = res["filepath"]
            filesize = res["filesize"]
            audio_duration = res.get("duration") or duration

            if filesize > MAX_FILE_SIZE_BYTES:
                await progress_msg.edit_text(
                    f"⚠️ <b>Аудиофайл превышает {MAX_FILE_SIZE_MB} МБ ({format_size(filesize)}).</b>",
                    parse_mode="HTML"
                )
                return

            audio_caption = f"🎵 <b>{html.escape(title)}</b>\n👤 {html.escape(uploader)}"

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
                    title=title,
                    performer=uploader,
                    duration=int(audio_duration) if audio_duration else None,
                    caption=audio_caption,
                    parse_mode="HTML"
                )
                await progress_msg.delete()

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

            if filesize > MAX_FILE_SIZE_BYTES:
                await progress_msg.edit_text(
                    f"⚠️ <b>Голосовое сообщение превышает {MAX_FILE_SIZE_MB} МБ ({format_size(filesize)}).</b>",
                    parse_mode="HTML"
                )
                return

            voice_caption = f"🎙 <b>{html.escape(title)}</b>"

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
                await progress_msg.delete()


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
