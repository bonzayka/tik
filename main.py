import asyncio
import logging
import sys
from pathlib import Path

# Fix Windows console UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, DOWNLOADS_DIR, HAS_MTPROTO, MAX_FILE_SIZE_MB
from handlers import router
from task_manager import task_manager
from mtproto_uploader import uploader

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("bot")

async def periodic_cleanup():
    """Periodically cleans up expired tasks and orphaned files."""
    while True:
        try:
            await asyncio.sleep(600)  # Every 10 minutes
            task_manager.cleanup_expired()
            
            # Clean up old empty subdirectories in downloads if any
            if DOWNLOADS_DIR.exists():
                for item in DOWNLOADS_DIR.iterdir():
                    if item.is_dir() and not any(item.iterdir()):
                        try:
                            item.rmdir()
                        except Exception:
                            pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Error during periodic cleanup: {e}")

async def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error("BOT_TOKEN is not set. Please set it in .env file.")
        sys.exit(1)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # Register handlers
    dp.include_router(router)

    # Start periodic cleanup task
    cleanup_task = asyncio.create_task(periodic_cleanup())

    try:
        # Start MTProto uploader if configured
        await uploader.start()

        me = await bot.get_me()
        logger.info(f"Bot started successfully: @{me.username} ({me.full_name}) [ID: {me.id}]")
        print("\n" + "="*50)
        print(f"🚀 Бот @{me.username} запущен и готов к работе!")
        print(f"📦 Максимальный размер загрузки: {MAX_FILE_SIZE_MB} МБ {'(MTProto 2GB активен)' if HAS_MTPROTO else '(50MB стандарт)'}")
        print("Отправьте ему ссылку на видео из YouTube или TikTok.")
        print("Нажмите Ctrl+C в терминале для остановки.")
        print("="*50 + "\n")
        
        # Drop pending updates to avoid processing ancient messages
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
    finally:
        cleanup_task.cancel()
        await uploader.stop()
        await bot.session.close()
        logger.info("Bot session closed.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nБот остановлен.")
