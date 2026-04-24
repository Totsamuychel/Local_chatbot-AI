#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Handlers for text messages, photos and image documents."""

import asyncio
import base64
import logging
from io import BytesIO

from PIL import Image
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import AVAILABLE_MODELS, DEFAULT_MODEL, LANGUAGES, SYSTEM_PROMPTS
from bot.context import add_message, build_prompt_prefix, get_language, t
from bot.ollama_client import query_ollama

log = logging.getLogger(__name__)

MAX_IMAGE_DIM = 1024


def _request_manager():
    from main import request_manager  # noqa: PLC0415
    return request_manager


# ── Image pre-processing helper ──────────────────────────────────────

def _encode_image(raw: bytes) -> str:
    """Resize, convert to JPEG and base64-encode an image for Ollama."""
    img = Image.open(BytesIO(raw)).convert("RGB")
    if max(img.size) > MAX_IMAGE_DIM:
        ratio = MAX_IMAGE_DIM / max(img.size)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)),
            Image.Resampling.LANCZOS,
        )
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ── Core image handler (shared by photo and document flows) ──────────────

async def _handle_image(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    file_id: str,
    user_prompt: str,
    prefix: str = "🖼\ufe0f",
) -> None:
    user_id = update.effective_user.id
    model_name = ctx.user_data.get("model", DEFAULT_MODEL)
    lang = get_language(ctx)

    if not AVAILABLE_MODELS.get(model_name, {}).get("vision", False):
        await update.message.reply_text(
            f"❌ Model {model_name} does not support images.\n"
            "Switch to a vision model: /models"
        )
        return

    proc_text = f"{prefix} {t(ctx, 'processing')}"
    proc_msg = await update.message.reply_text(proc_text)
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_request:{user_id}")]]
    )
    await proc_msg.edit_text(f"{proc_text}\n💡 You can cancel:", reply_markup=cancel_kb)

    try:
        file_obj = await ctx.bot.get_file(file_id)
        raw = await file_obj.download_as_bytearray()
        image_b64 = _encode_image(bytes(raw))

        context_prefix = build_prompt_prefix(user_id, ctx)
        full_prompt = f"{context_prefix}IMAGE ANALYSIS: {user_prompt}"
        system_prompt = (
            SYSTEM_PROMPTS["vision"] + " " + LANGUAGES[lang]["system_prompt_suffix"]
        )
        temperature = ctx.user_data.get("temperature", 0.7)

        answer = await _request_manager().execute_request(
            user_id,
            query_ollama,
            prompt=full_prompt,
            model_name=model_name,
            system_prompt=system_prompt,
            images=[image_b64],
            temperature=temperature,
            max_tokens=1500,
            use_cache=False,
        )

        await proc_msg.delete()
        add_message(user_id, ctx, f"[IMAGE] {user_prompt}", answer)
        await update.message.reply_text(answer)

    except asyncio.CancelledError:
        await proc_msg.edit_text(t(ctx, "cancelled"))
    except Exception as exc:
        log.exception("Image processing error: %s", exc)
        await proc_msg.edit_text(
            f"{t(ctx, 'error')} processing image.\n"
            "Possible causes:\n"
            "\u2022 Corrupted image\n"
            "\u2022 Model unavailable\n"
            "\u2022 File too large"
        )


# ── Public handlers ─────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    user_text = update.message.text.strip()
    if not user_text:
        return

    user_id = update.effective_user.id
    log.info("Text from %s: %s", user_id, user_text[:80])

    model_name = ctx.user_data.get("model", DEFAULT_MODEL)
    temperature = ctx.user_data.get("temperature", 0.7)
    lang = get_language(ctx)

    system_key = "code" if "cod" in model_name.lower() else "general"
    system_prompt = (
        SYSTEM_PROMPTS[system_key] + " " + LANGUAGES[lang]["system_prompt_suffix"]
    )

    context_prefix = build_prompt_prefix(user_id, ctx)
    full_prompt = f"{context_prefix}NEW QUESTION: {user_text}"

    proc_text = t(ctx, "processing")
    proc_msg = await update.message.reply_text(proc_text)
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_request:{user_id}")]]
    )
    await proc_msg.edit_text(f"{proc_text}\n💡 You can cancel:", reply_markup=cancel_kb)

    try:
        answer = await _request_manager().execute_request(
            user_id,
            query_ollama,
            prompt=full_prompt,
            model_name=model_name,
            system_prompt=system_prompt,
            temperature=temperature,
        )
        await proc_msg.delete()
        add_message(user_id, ctx, user_text, answer)
        await update.message.reply_text(answer)

    except asyncio.CancelledError:
        await proc_msg.edit_text(t(ctx, "cancelled"))
    except Exception as exc:
        log.exception("Text handling error: %s", exc)
        await proc_msg.edit_text(f"{t(ctx, 'error')}: {exc}")


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo:
        return
    photo = update.message.photo[-1]  # largest resolution
    caption = update.message.caption or "Analyse this image in detail."
    await _handle_image(update, ctx, photo.file_id, caption, prefix="🖼\ufe0f")


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.document:
        return
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("📎 Only image documents are supported.")
        return
    caption = update.message.caption or f"Analyse the image from document '{doc.file_name}'."
    await _handle_image(update, ctx, doc.file_id, caption, prefix="📎")
