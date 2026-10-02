import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional, Callable, Any

from telethon import TelegramClient, types, functions, helpers
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeAudio

from config import API_ID, API_HASH, BOT_TOKEN, HAS_MTPROTO, BASE_DIR

logger = logging.getLogger(__name__)


def make_progress_bar(percent: float, length: int = 10) -> str:
    filled = max(0, min(length, int(length * percent / 100)))
    return "█" * filled + "░" * (length - filled)


class UploadProgressTracker:
    """Tracks MTProto upload progress and updates the Telegram progress message periodically."""

    def __init__(self, message, total_size: int, item_name: str = "видео"):
        self.message = message
        self.total_size = total_size
        self.item_name = item_name
        self.start_time = time.time()
        self.last_update_time = 0.0
        self._lock = asyncio.Lock()

    async def __call__(self, current: int, total: int):
        now = time.time()
        total = total or self.total_size

        # Throttle updates to once every 3.5 seconds unless 100% complete
        if (now - self.last_update_time < 3.5) and (current < total):
            return

        async with self._lock:
            if (now - self.last_update_time < 3.5) and (current < total):
                return
            self.last_update_time = now

            elapsed = max(0.1, now - self.start_time)
            speed = current / elapsed  # bytes/sec
            percent = min(100.0, (current / total * 100)) if total > 0 else 0

            bar = make_progress_bar(percent, length=10)
            curr_mb = current / (1024 * 1024)
            tot_mb = total / (1024 * 1024)
            speed_mb = speed / (1024 * 1024)

            remaining_bytes = max(0, total - current)
            eta_sec = int(remaining_bytes / speed) if speed > 0 else 0
            if eta_sec < 60:
                eta_str = f"{eta_sec} сек"
            else:
                eta_str = f"{eta_sec // 60} мин {eta_sec % 60} сек"

            text = (
                f"📤 <b>Загрузка {self.item_name} в Telegram (MTProto)...</b>\n\n"
                f"<code>[{bar}] {percent:.1f}%</code>\n"
                f"📦 <b>{curr_mb:.1f}</b> из <b>{tot_mb:.1f} МБ</b>\n"
                f"⚡️ Скорость: <b>{speed_mb:.1f} МБ/с</b>\n"
                f"⏱ Осталось: ~<b>{eta_str}</b>"
            )

            try:
                await self.message.edit_text(text, parse_mode="HTML")
            except Exception:
                pass


async def fast_upload_file(
    client: TelegramClient,
    file_path: str,
    progress_callback: Optional[Callable[[int, int], Any]] = None,
    max_workers: int = 6
) -> types.TypeInputFile:
    """
    High-speed parallel file uploader using 512KB chunks and pipelined asyncio requests.
    Significantly faster than default Telethon sequential upload.
    """
    path = Path(file_path)
    file_size = path.stat().st_size
    file_name = path.name

    part_size = 512 * 1024  # Maximum 512KB chunk size supported by Telegram MTProto
    part_count = (file_size + part_size - 1) // part_size
    is_big = file_size > 10 * 1024 * 1024
    file_id = helpers.generate_random_long()

    bytes_sent = 0
    progress_lock = asyncio.Lock()
    queue: asyncio.Queue = asyncio.Queue()

    for part_index in range(part_count):
        offset = part_index * part_size
        length = min(part_size, file_size - offset)
        queue.put_nowait((part_index, offset, length))

    async def worker():
        nonlocal bytes_sent
        with open(file_path, "rb") as f:
            while not queue.empty():
                try:
                    part_index, offset, length = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                f.seek(offset)
                chunk = f.read(length)

                if is_big:
                    req = functions.upload.SaveBigFilePartRequest(
                        file_id=file_id,
                        file_part=part_index,
                        file_total_parts=part_count,
                        bytes=chunk
                    )
                else:
                    req = functions.upload.SaveFilePartRequest(
                        file_id=file_id,
                        file_part=part_index,
                        bytes=chunk
                    )

                for attempt in range(3):
                    try:
                        res = await client(req)
                        if not res:
                            raise RuntimeError(f"Telegram rejected part {part_index}")
                        break
                    except Exception as e:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(1 + attempt)

                async with progress_lock:
                    bytes_sent += length
                    if progress_callback:
                        try:
                            cb_res = progress_callback(bytes_sent, file_size)
                            if asyncio.iscoroutine(cb_res):
                                await cb_res
                        except Exception:
                            pass

                queue.task_done()

    num_workers = min(max_workers, max(1, part_count))
    workers = [asyncio.create_task(worker()) for _ in range(num_workers)]
    await asyncio.gather(*workers)

    if is_big:
        return types.InputFileBig(id=file_id, parts=part_count, name=file_name)
    else:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hash_md5.update(chunk)
        return types.InputFile(id=file_id, parts=part_count, name=file_name, md5_checksum=hash_md5.hexdigest())


class MTProtoUploader:
    """Telethon MTProto client for sending files up to 2GB directly through Telegram servers."""

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.session_path = BASE_DIR / "bot_mtproto"
        self._is_started = False

    async def start(self):
        if not HAS_MTPROTO:
            logger.info("MTProto credentials (API_ID/API_HASH) not configured. Using standard Bot API (50MB limit).")
            return

        try:
            self.client = TelegramClient(str(self.session_path), API_ID, API_HASH)
            await self.client.start(bot_token=BOT_TOKEN)
            self._is_started = True
            logger.info("🚀 MTProto Uploader successfully started! Upload limit extended to 2000 MB (2 GB).")
        except Exception as e:
            logger.error(f"Failed to start MTProto Uploader: {e}", exc_info=True)
            self._is_started = False

    async def stop(self):
        if self.client and self._is_started:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self._is_started = False

    @property
    def is_available(self) -> bool:
        return self._is_started and self.client is not None

    async def send_video(
        self,
        chat_id: int,
        filepath: str,
        caption: str = "",
        duration: int = 0,
        width: int = 0,
        height: int = 0,
        thumb_path: Optional[str] = None,
        progress_callback: Optional[Callable] = None
    ):
        if not self.is_available:
            raise RuntimeError("MTProto Uploader is not available")

        attrs = [
            DocumentAttributeVideo(
                duration=int(duration or 0),
                w=int(width or 0),
                h=int(height or 0),
                supports_streaming=True
            )
        ]

        try:
            file_handle = await fast_upload_file(
                client=self.client,
                file_path=filepath,
                progress_callback=progress_callback,
                max_workers=6
            )
        except Exception as e:
            logger.warning(f"Fast upload encountered an error: {e}. Falling back to standard send_file.")
            file_handle = filepath

        return await self.client.send_file(
            entity=chat_id,
            file=file_handle,
            caption=caption,
            thumb=thumb_path,
            parse_mode="html",
            attributes=attrs,
            supports_streaming=True,
            progress_callback=progress_callback if file_handle == filepath else None
        )

    async def send_audio(
        self,
        chat_id: int,
        filepath: str,
        caption: str = "",
        title: str = "",
        performer: str = "",
        duration: int = 0,
        thumb_path: Optional[str] = None,
        progress_callback: Optional[Callable] = None
    ):
        if not self.is_available:
            raise RuntimeError("MTProto Uploader is not available")

        attrs = [
            DocumentAttributeAudio(
                duration=int(duration or 0),
                title=title or "Audio",
                performer=performer or "Unknown",
                voice=False
            )
        ]

        try:
            file_handle = await fast_upload_file(
                client=self.client,
                file_path=filepath,
                progress_callback=progress_callback,
                max_workers=6
            )
        except Exception as e:
            logger.warning(f"Fast upload encountered an error: {e}. Falling back to standard send_file.")
            file_handle = filepath

        return await self.client.send_file(
            entity=chat_id,
            file=file_handle,
            caption=caption,
            thumb=thumb_path,
            parse_mode="html",
            attributes=attrs,
            progress_callback=progress_callback if file_handle == filepath else None
        )

    async def send_voice(
        self,
        chat_id: int,
        filepath: str,
        caption: str = "",
        duration: int = 0,
        progress_callback: Optional[Callable] = None
    ):
        if not self.is_available:
            raise RuntimeError("MTProto Uploader is not available")

        attrs = [
            DocumentAttributeAudio(
                duration=int(duration or 0),
                voice=True
            )
        ]

        try:
            file_handle = await fast_upload_file(
                client=self.client,
                file_path=filepath,
                progress_callback=progress_callback,
                max_workers=6
            )
        except Exception as e:
            logger.warning(f"Fast upload encountered an error: {e}. Falling back to standard send_file.")
            file_handle = filepath

        return await self.client.send_file(
            entity=chat_id,
            file=file_handle,
            caption=caption,
            parse_mode="html",
            voice_note=True,
            attributes=attrs,
            progress_callback=progress_callback if file_handle == filepath else None
        )

uploader = MTProtoUploader()
