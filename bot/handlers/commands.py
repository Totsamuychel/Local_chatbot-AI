#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""All /command handlers."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import (
    AVAILABLE_MODELS,
    CACHE_TTL,
    DEFAULT_MODEL,
    LANGUAGES,
    MAX_CONCURRENT_REQUESTS,
    MAX_CONTEXT_MESSAGES,
)
from bot.context import clear_context, get_language, get_stats, t
from bot.ollama_client import response_cache
from bot.request_manager import RequestManager

log = logging.getLogger(__name__)


def _request_manager() -> RequestManager:
    """Lazy import to avoid circular deps; replaced by DI in main."""
    from main import request_manager  # noqa: PLC0415
    return request_manager


# ── /start ───────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("🇺🇸 English", callback_data="lang:en")],
        [InlineKeyboardButton("🇺🇦 Українська", callback_data="lang:uk")],
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru")],
    ]
    await update.message.reply_text(
        "👋 Hi! Choose your language / Выберите язык / Оберіть мову:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_main_menu(message, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await message.reply_text(
        "👋 I'm a local AI assistant powered by Ollama.\n\n"
        "🤖 I can help with:\n"
        "\u2022 Answering questions\n"
        "\u2022 Analysing images\n"
        "\u2022 Coding\n"
        "\u2022 Translation & explanations\n\n"
        "📋 Commands:\n"
        "/models \u2014 list available models\n"
        "/model <name> \u2014 switch model\n"
        "/context \u2014 conversation context stats\n"
        "/clear \u2014 clear conversation context\n"
        "/cancel \u2014 cancel current request\n"
        "/settings \u2014 bot settings\n"
        "/cache \u2014 cache stats\n"
        "/help \u2014 this help"
    )


# ── /help ───────────────────────────────────────────────────
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    rm = _request_manager()
    cache_stats = response_cache.stats()
    model_lines = "".join(
        f"{'\ud83d\uddbc\ufe0f' if i['vision'] else '\ud83d\udcdd'} {n} \u2014 {i['description']}\n"
        for n, i in AVAILABLE_MODELS.items()
    )
    await update.message.reply_text(
        "📖 Commands:\n\n"
        "/start \u2014 start bot\n"
        "/models \u2014 list models\n"
        "/model <name> \u2014 switch model\n"
        "/context \u2014 context stats\n"
        "/clear \u2014 clear context\n"
        "/cancel \u2014 cancel active request\n"
        "/settings \u2014 temperature & language\n"
        "/cache \u2014 cache stats\n"
        "/help \u2014 this message\n\n"
        f"🤖 Models:\n{model_lines}\n"
        f"📊 System:\n"
        f"\u2022 Active requests: {rm.active_count()}/{MAX_CONCURRENT_REQUESTS}\n"
        f"\u2022 Cached entries: {cache_stats['total_entries']}\n"
        f"\u2022 Cache hit rate: {cache_stats['hit_rate']:.1f}%\n\n"
        "💡 Just send text or an image to get started!"
    )


# ── /models ────────────────────────────────────────────────
async def models_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    current = ctx.user_data.get("model", DEFAULT_MODEL)
    rm = _request_manager()
    kb = [
        [
            InlineKeyboardButton(
                f"{'\ud83d\uddbc\ufe0f' if info['vision'] else '\ud83d\udcdd'} {name} {'\u2705' if name == current else ''}",
                callback_data=f"model:{name}",
            )
        ]
        for name, info in AVAILABLE_MODELS.items()
    ]
    await update.message.reply_text(
        "🔧 Choose a model:\n\n"
        "🖼\ufe0f = image support  \ud83d� = text only  \u2705 = current\n\n"
        f"📊 Active requests: {rm.active_count()}/{MAX_CONCURRENT_REQUESTS}",
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ── /model <name> ──────────────────────────────────────────
async def set_model_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Please specify a model name.\nSee /models")
        return
    name = ctx.args[0]
    if name not in AVAILABLE_MODELS:
        await update.message.reply_text(
            f"Model `{name}` is not available.\n"
            f"Available: {', '.join(AVAILABLE_MODELS)}"
        )
        return
    ctx.user_data["model"] = name
    info = AVAILABLE_MODELS[name]
    tag = "🖼\ufe0f image support" if info["vision"] else "📝 text only"
    await update.message.reply_text(f"✅ Model set to `{name}`\n{tag}\n{info['description']}")


# ── /context ───────────────────────────────────────────────
async def context_stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    stats = get_stats(user_id, ctx)
    if stats.total_messages == 0:
        await update.message.reply_text("📊 Context is empty. Start a conversation!")
        return
    kb = [
        [InlineKeyboardButton("🧹 Clear all", callback_data="clear_context:all")],
        [InlineKeyboardButton("📝 Keep last 2", callback_data="clear_context:2")],
        [InlineKeyboardButton("📝 Keep last 5", callback_data="clear_context:5")],
    ]
    oldest = stats.oldest_message_time.strftime("%d.%m %H:%M") if stats.oldest_message_time else "\u2014"
    newest = stats.newest_message_time.strftime("%d.%m %H:%M") if stats.newest_message_time else "\u2014"
    await update.message.reply_text(
        f"📊 Context stats:\n\n"
        f"💬 Messages: {stats.total_messages}/{MAX_CONTEXT_MESSAGES}\n"
        f"💾 Memory: {stats.memory_usage_kb:.1f} KB\n"
        f"📅 First: {oldest}\n"
        f"🕐 Last: {newest}\n\n"
        "💡 Manage context:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ── /clear ─────────────────────────────────────────────────
async def clear_context_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    clear_context(update.effective_user.id, ctx)
    await update.message.reply_text("🧹 Conversation context cleared!")


# ── /cancel ───────────────────────────────────────────────
async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rm = _request_manager()
    if rm.cancel_user_request(user_id):
        await update.message.reply_text(f"{t(ctx, 'cancelled')} ✅")
    else:
        await update.message.reply_text("🤷\u200d\u2642\ufe0f No active request to cancel.")


# ── /settings ──────────────────────────────────────────────
async def settings_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    current_model = ctx.user_data.get("model", DEFAULT_MODEL)
    current_temp = ctx.user_data.get("temperature", 0.7)
    lang = get_language(ctx)
    lang_name = LANGUAGES[lang]["name"]
    kb = [
        [InlineKeyboardButton("🌡\ufe0f Temperature: Low (0.3)", callback_data="temp:0.3")],
        [InlineKeyboardButton("🌡\ufe0f Temperature: Medium (0.7)", callback_data="temp:0.7")],
        [InlineKeyboardButton("🌡\ufe0f Temperature: High (1.0)", callback_data="temp:1.0")],
        [InlineKeyboardButton("🌍 Change language", callback_data="change_lang")],
    ]
    await update.message.reply_text(
        f"⚙\ufe0f Current settings:\n"
        f"🤖 Model: {current_model}\n"
        f"🌡\ufe0f Temperature: {current_temp}\n"
        f"🌍 Language: {lang_name}\n\n"
        "💡 Select what to change:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ── /cache ─────────────────────────────────────────────────
async def cache_stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    stats = response_cache.stats()
    kb = [[InlineKeyboardButton("🗑\ufe0f Clear cache", callback_data="clear_cache")]]
    await update.message.reply_text(
        f"📊 Cache stats:\n\n"
        f"💾 Entries: {stats['total_entries']}\n"
        f"📈 Hit rate: {stats['hit_rate']:.1f}%\n"
        f"🗄\ufe0f Memory: {stats['memory_usage_kb']:.1f} KB\n"
        f"\u23f1\ufe0f TTL: {CACHE_TTL // 60} minutes\n\n"
        "💡 Cache speeds up repeated requests.",
        reply_markup=InlineKeyboardMarkup(kb),
    )
