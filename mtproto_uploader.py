import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeAudio

from config import API_ID, API_HASH, BOT_TOKEN, HAS_MTPROTO, BASE_DIR

logger = logging.getLogger(__name__)

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
        return await self.client.send_file(
            entity=chat_id,
            file=filepath,
            caption=caption,
            parse_mode="html",
            attributes=attrs,
            supports_streaming=True,
            progress_callback=progress_callback
        )

    async def send_audio(
        self,
        chat_id: int,
        filepath: str,
        caption: str = "",
        title: str = "",
        performer: str = "",
        duration: int = 0,
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
        return await self.client.send_file(
            entity=chat_id,
            file=filepath,
            caption=caption,
            parse_mode="html",
            attributes=attrs,
            progress_callback=progress_callback
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
        return await self.client.send_file(
            entity=chat_id,
            file=filepath,
            caption=caption,
            parse_mode="html",
            voice_note=True,
            attributes=attrs,
            progress_callback=progress_callback
        )

uploader = MTProtoUploader()
