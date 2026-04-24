#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Entry point: build the Telegram application and start polling."""

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.config import (
    CACHE_TTL,
    CLEANUP_INTERVAL,
    MAX_CONCURRENT_REQUESTS,
    MAX_CONTEXT_MESSAGES,
    TELEGRAM_BOT_TOKEN,
)
from bot.ollama_client import response_cache
from bot.request_manager import RequestManager

# Module-level singleton used by handlers
request_manager = RequestManager(max_concurrent=MAX_CONCURRENT_REQUESTS)

log = logging.getLogger(__name__)


# ── Background task ───────────────────────────────────────────────
async def _cleanup_loop() -> None:
    """Periodically remove expired cache entries."""
    while True:
        try:
            removed = response_cache.clear_expired()
            log.info("Cache cleanup: removed %d stale entries", removed)
        except Exception as exc:
            log.exception("Cache cleanup error: %s", exc)
        await asyncio.sleep(CLEANUP_INTERVAL)


async def _post_init(application: Application) -> None:
    asyncio.create_task(_cleanup_loop())


# ── Error handler ───────────────────────────────────────────────
async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Update %s caused error", update)
    if update and hasattr(update, "effective_message") and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again."
            )
        except Exception:
            pass


# ── Application factory ───────────────────────────────────────────
def build_app() -> Application:
    from bot.handlers.callbacks import button_callback
    from bot.handlers.commands import (
        cache_stats_cmd,
        cancel_cmd,
        clear_context_cmd,
        context_stats_cmd,
        help_cmd,
        models_cmd,
        set_model_cmd,
        settings_cmd,
        start,
    )
    from bot.handlers.messages import handle_document, handle_photo, handle_text

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(_post_init)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("models",  models_cmd))
    app.add_handler(CommandHandler("model",   set_model_cmd))
    app.add_handler(CommandHandler("context", context_stats_cmd))
    app.add_handler(CommandHandler("clear",   clear_context_cmd))
    app.add_handler(CommandHandler("cancel",  cancel_cmd))
    app.add_handler(CommandHandler("settings",settings_cmd))
    app.add_handler(CommandHandler("cache",   cache_stats_cmd))

    # Inline keyboards
    app.add_handler(CallbackQueryHandler(button_callback))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))

    app.add_error_handler(_error_handler)
    return app


# ── Entry point ─────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(asctime)s — %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("🚀 Starting Local Chatbot AI...")
    log.info("📊 Max concurrent requests : %d", MAX_CONCURRENT_REQUESTS)
    log.info("💾 Cache TTL               : %d s", CACHE_TTL)
    log.info("💬 Max context messages    : %d", MAX_CONTEXT_MESSAGES)

    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)
