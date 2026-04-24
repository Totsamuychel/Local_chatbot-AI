#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Per-user conversation context stored in bot_data."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from telegram.ext import ContextTypes

from bot.config import DEFAULT_LANGUAGE, LANGUAGES, MAX_CONTEXT_MESSAGES


@dataclass
class ContextStats:
    """Summary statistics for a user's conversation history."""
    total_messages: int
    memory_usage_kb: float
    oldest_message_time: Optional[datetime]
    newest_message_time: Optional[datetime]


# ── Low-level helpers ─────────────────────────────────────────

def _get_history(user_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> List[Dict]:
    convs = ctx.bot_data.setdefault("conversations", {})
    return convs.setdefault(user_id, [])


# ── Public API ──────────────────────────────────────────────

def add_message(user_id: int, ctx: ContextTypes.DEFAULT_TYPE,
               user_msg: str, bot_reply: str) -> None:
    """Append an exchange to the user's history; trim to MAX_CONTEXT_MESSAGES."""
    history = _get_history(user_id, ctx)
    history.append({"user": user_msg, "bot": bot_reply, "timestamp": datetime.now()})
    if len(history) > MAX_CONTEXT_MESSAGES:
        history.pop(0)


def clear_context(user_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Wipe the entire conversation history for a user."""
    ctx.bot_data.setdefault("conversations", {})[user_id] = []


def clear_context_partial(user_id: int, ctx: ContextTypes.DEFAULT_TYPE,
                          keep_last: int = 2) -> int:
    """Remove oldest messages, keep the last *keep_last*. Returns # removed."""
    history = _get_history(user_id, ctx)
    removed = max(0, len(history) - keep_last)
    ctx.bot_data["conversations"][user_id] = history[-keep_last:] if removed else history
    return removed


def get_stats(user_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> ContextStats:
    """Return statistics about the user's conversation context."""
    history = _get_history(user_id, ctx)
    if not history:
        return ContextStats(0, 0.0, None, None)
    timestamps = [m["timestamp"] for m in history if "timestamp" in m]
    return ContextStats(
        total_messages=len(history),
        memory_usage_kb=len(str(history)) / 1024,
        oldest_message_time=min(timestamps) if timestamps else None,
        newest_message_time=max(timestamps) if timestamps else None,
    )


def build_prompt_prefix(user_id: int, ctx: ContextTypes.DEFAULT_TYPE,
                        last_n: int = 5) -> str:
    """Format the last N exchanges as a context prefix for the LLM prompt."""
    history = _get_history(user_id, ctx)
    if not history:
        return ""
    lines = ["CONVERSATION HISTORY:"]
    for msg in history[-last_n:]:
        lines.append(f"User: {msg['user']}")
        lines.append(f"Assistant: {msg['bot']}")
        lines.append("")
    return "\n".join(lines)


# ── i18n helpers ───────────────────────────────────────────

def get_language(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return ctx.user_data.get("language", DEFAULT_LANGUAGE)


def t(ctx: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    """Translate *key* using the user's selected language."""
    lang = get_language(ctx)
    return LANGUAGES.get(lang, LANGUAGES[DEFAULT_LANGUAGE]).get(key, key)
