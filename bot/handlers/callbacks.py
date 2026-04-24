#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Inline-keyboard callback handler."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import AVAILABLE_MODELS, LANGUAGES
from bot.context import clear_context, clear_context_partial, t
from bot.ollama_client import response_cache
from bot.handlers.commands import show_main_menu

log = logging.getLogger(__name__)

_TEMP_DESCRIPTIONS = {
    0.3: "Low (more predictable answers)",
    0.7: "Medium (balance of creativity and accuracy)",
    1.0: "High (more creative answers)",
}


async def button_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data: str = query.data
    user_id = update.effective_user.id

    # —— language selection ——————————————————————————————————————
    if data.startswith("lang:"):
        lang_code = data.split(":", 1)[1]
        if lang_code in LANGUAGES:
            ctx.user_data["language"] = lang_code
            lang_name = LANGUAGES[lang_code]["name"]
            await query.edit_message_text(f"✅ Language set to: {lang_name}")
            await show_main_menu(query.message, ctx)
        else:
            await query.edit_message_text("❌ Unknown language.")

    elif data == "change_lang":
        kb = [
            [InlineKeyboardButton("🇺🇸 English", callback_data="lang:en")],
            [InlineKeyboardButton("🇺🇦 Українська", callback_data="lang:uk")],
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru")],
        ]
        await query.edit_message_text(
            "🌍 Choose language:",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    # —— model selection ——————————————————————————————————————
    elif data.startswith("model:"):
        name = data.split(":", 1)[1]
        ctx.user_data["model"] = name
        info = AVAILABLE_MODELS[name]
        tag = "🖼\ufe0f image support" if info["vision"] else "📝 text only"
        await query.edit_message_text(
            f"✅ Model set to `{name}`\n{tag}\n{info['description']}"
        )

    # —— temperature ——————————————————————————————————————
    elif data.startswith("temp:"):
        temp = float(data.split(":", 1)[1])
        ctx.user_data["temperature"] = temp
        desc = _TEMP_DESCRIPTIONS.get(temp, "Custom")
        await query.edit_message_text(
            f"🌡\ufe0f Temperature set: {temp}\n📝 {desc}"
        )

    # —— context management ————————————————————————————————
    elif data.startswith("clear_context:"):
        action = data.split(":", 1)[1]
        if action == "all":
            clear_context(user_id, ctx)
            await query.edit_message_text("🧹 Context fully cleared!")
        else:
            try:
                removed = clear_context_partial(user_id, ctx, int(action))
                await query.edit_message_text(
                    f"🗑\ufe0f Removed {removed} old messages, kept last {action}."
                )
            except ValueError:
                await query.edit_message_text("❌ Invalid action.")

    # —— cache management ————————————————————————————————
    elif data == "clear_cache":
        response_cache.cache.clear()
        await query.edit_message_text("🗑\ufe0f Response cache cleared!")

    # —— cancel request ———————————————————————————————————
    elif data.startswith("cancel_request:"):
        target_id = int(data.split(":")[1])
        from main import request_manager  # noqa: PLC0415
        if request_manager.cancel_user_request(target_id):
            await query.edit_message_text(f"{t(ctx, 'cancelled')} ✅")
        else:
            await query.edit_message_text("🤷\u200d\u2642\ufe0f No active request to cancel.")

    else:
        await query.edit_message_text("❌ Unknown action.")
