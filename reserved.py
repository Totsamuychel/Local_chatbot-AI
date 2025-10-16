#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import logging
import json
import requests
import base64
from typing import List, Dict, Optional
from PIL import Image
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# -----------------------------------------------------------------
# 1. Настройки и логирование
# -----------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s — %(message)s",
    datefmt="%H:%M:%S",
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OLLAMA_SERVER_URL = "http://localhost:11434"

# Модели с описанием их возможностей
AVAILABLE_MODELS = {
    "qwen2.5vl:7b": {"vision": True, "description": "Быстрая модель с поддержкой изображений"},
    "gpt-oss:20b": {"vision": False, "description": "Мощная текстовая модель"},
    "qwen3:32b": {"vision": False, "description": "Большая текстовая модель"},
    "qwen2.5vl:32b": {"vision": True, "description": "Большая модель с поддержкой изображений"},
    "qwen2.5-coder:7b": {"vision": False, "description": "Специализированная модель для программирования"},
    "qwen2.5-coder:1.5b": {"vision": False, "description": "Быстрая модель для программирования"},
}

DEFAULT_MODEL = "qwen2.5vl:7b"

# Системные промпты для улучшения ответов
SYSTEM_PROMPTS = {
    "general": """Ты — полезный AI-ассистент. Отвечай четко, информативно и по делу. 
    Если не знаешь ответа, честно об этом скажи. Всегда стремись быть максимально полезным пользователю.
    Отвечай на русском языке, если не указано иное.""",
    
    "vision": """Ты — AI-ассистент с возможностью анализа изображений. 
    Внимательно рассматривай изображения и давай детальные, точные описания.
    Указывай на важные детали, цвета, объекты, людей, текст на изображениях.
    Если на изображении есть текст, обязательно его прочитай и включи в ответ.""",
    
    "code": """Ты — специализированный AI-ассистент для программирования.
    Пиши чистый, читаемый код с комментариями.
    Объясняй сложные концепции простым языком.
    Всегда учитывай лучшие практики программирования."""
}

# -----------------------------------------------------------------
# 2. Улучшенная функция запроса к Ollama
# -----------------------------------------------------------------
def query_ollama(
    prompt: str, 
    model_name: str = DEFAULT_MODEL,
    system_prompt: Optional[str] = None,
    images: Optional[List[str]] = None,
    temperature: float = 0.7,
    max_tokens: int = 2000
) -> str:
    """
    Улучшенная функция запроса к Ollama с поддержкой изображений и системных промптов
    """
    url = f"{OLLAMA_SERVER_URL}/api/generate"

    # Формируем полный промпт с системным промптом
    full_prompt = prompt
    if system_prompt:
        full_prompt = f"SYSTEM: {system_prompt}\n\nUSER: {prompt}"

    payload = {
        "model": model_name,
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    }

    # Добавляем изображения если есть
    if images:
        payload["images"] = images

    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        
        answer = data.get("response", "").strip()
        if not answer:
            return "🤖 Не смог сгенерировать ответ, попробуйте переформулировать вопрос."
        
        return answer
        
    except requests.exceptions.Timeout:
        return "⏱️ Запрос занял слишком много времени. Попробуйте с более простым вопросом."
    except requests.exceptions.RequestException as exc:
        return f"❌ Ошибка соединения с Ollama: {exc}"
    except json.JSONDecodeError:
        return "❌ Не удалось разобрать ответ от Ollama."

# -----------------------------------------------------------------
# 3. Функции для работы с контекстом
# -----------------------------------------------------------------
def get_conversation_context(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> List[Dict]:
    """Получает контекст беседы для пользователя"""
    if "conversations" not in context.bot_data:
        context.bot_data["conversations"] = {}
    
    if user_id not in context.bot_data["conversations"]:
        context.bot_data["conversations"][user_id] = []
    
    return context.bot_data["conversations"][user_id]

def add_to_context(user_id: int, context: ContextTypes.DEFAULT_TYPE, user_msg: str, bot_response: str):
    """Добавляет сообщение в контекст беседы"""
    conversation = get_conversation_context(user_id, context)
    conversation.append({"user": user_msg, "bot": bot_response})
    
    # Ограничиваем контекст последними 10 сообщениями
    if len(conversation) > 10:
        conversation.pop(0)

def format_context_for_prompt(conversation: List[Dict]) -> str:
    """Форматирует контекст для промпта"""
    if not conversation:
        return ""
    
    context_str = "КОНТЕКСТ БЕСЕДЫ:\n"
    for msg in conversation[-5:]:  # Берем последние 5 сообщений
        context_str += f"Пользователь: {msg['user']}\nВы: {msg['bot']}\n\n"
    
    return context_str

# -----------------------------------------------------------------
# 4. Handlers Telegram
# -----------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я AI-ассистент, созданный Даном.\n\n"
        "🤖 Могу помочь с:\n"
        "• Ответами на вопросы\n"
        "• Анализом изображений\n"
        "• Программированием\n"
        "• Переводом и объяснениями\n\n"
        "📋 Команды:\n"
        "/models — показать доступные модели\n"
        "/model <имя> — установить модель\n"
        "/clear — очистить контекст беседы\n"
        "/settings — настройки бота\n"
        "/help — справка"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model_info = ""
    for name, info in AVAILABLE_MODELS.items():
        vision_support = "🖼️" if info["vision"] else "📝"
        model_info += f"{vision_support} {name} - {info['description']}\n"
    
    await update.message.reply_text(
        "📖 Справка по командам:\n\n"
        "/start — запустить бота\n"
        "/models — показать доступные модели\n"
        "/model <имя> — установить выбранную модель\n"
        "/clear — очистить контекст беседы\n"
        "/settings — настройки температуры и других параметров\n"
        "/help — эта справка\n\n"
        f"🤖 Доступные модели:\n{model_info}\n"
        "💡 Просто отправьте текст или изображение для обработки!"
    )

async def models_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показываем список моделей с кнопками и описанием."""
    kb = []
    for name, info in AVAILABLE_MODELS.items():
        vision_icon = "🖼️" if info["vision"] else "📝"
        current_icon = "✅" if context.user_data.get("model", DEFAULT_MODEL) == name else ""
        button_text = f"{vision_icon} {name} {current_icon}"
        kb.append([InlineKeyboardButton(button_text, callback_data=f"model:{name}")])
    
    await update.message.reply_text(
        "🔧 Выберите модель:\n\n"
        "🖼️ = поддержка изображений\n"
        "📝 = только текст\n"
        "✅ = текущая модель",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очищает контекст беседы"""
    user_id = update.effective_user.id
    if "conversations" in context.bot_data and user_id in context.bot_data["conversations"]:
        context.bot_data["conversations"][user_id] = []
    
    await update.message.reply_text("🧹 Контекст беседы очищен!")

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройки бота"""
    current_model = context.user_data.get("model", DEFAULT_MODEL)
    current_temp = context.user_data.get("temperature", 0.7)
    
    kb = [
        [InlineKeyboardButton("🌡️ Температура: Низкая (0.3)", callback_data="temp:0.3")],
        [InlineKeyboardButton("🌡️ Температура: Средняя (0.7)", callback_data="temp:0.7")],
        [InlineKeyboardButton("🌡️ Температура: Высокая (1.0)", callback_data="temp:1.0")],
    ]
    
    await update.message.reply_text(
        f"⚙️ Текущие настройки:\n"
        f"🤖 Модель: {current_model}\n"
        f"🌡️ Температура: {current_temp}\n\n"
        f"Температура влияет на креативность ответов:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def set_model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /model <имя>."""
    if not context.args:
        await update.message.reply_text(
            "Пожалуйста, укажите имя модели.\n"
            "Список доступных: /models"
        )
        return

    model_name = context.args[0]
    if model_name not in AVAILABLE_MODELS:
        available = ", ".join(AVAILABLE_MODELS.keys())
        await update.message.reply_text(
            f"Модель `{model_name}` недоступна.\n"
            f"Доступные модели: {available}"
        )
        return

    context.user_data["model"] = model_name
    model_info = AVAILABLE_MODELS[model_name]
    vision_support = "🖼️ с поддержкой изображений" if model_info["vision"] else "📝 только текст"
    
    await update.message.reply_text(
        f"✅ Выбрана модель `{model_name}`\n"
        f"{vision_support}\n"
        f"📝 {model_info['description']}"
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline-кнопок."""
    query = update.callback_query
    await query.answer()

    data = query.data
    
    if data.startswith("model:"):
        _, model_name = data.split(":", 1)
        context.user_data["model"] = model_name
        model_info = AVAILABLE_MODELS[model_name]
        vision_support = "🖼️ с поддержкой изображений" if model_info["vision"] else "📝 только текст"
        
        await query.edit_message_text(
            f"✅ Выбрана модель `{model_name}`\n"
            f"{vision_support}\n"
            f"📝 {model_info['description']}"
        )
    
    elif data.startswith("temp:"):
        _, temp_str = data.split(":", 1)
        temperature = float(temp_str)
        context.user_data["temperature"] = temperature
        
        temp_desc = {
            0.3: "Низкая (более предсказуемые ответы)",
            0.7: "Средняя (баланс креативности и точности)", 
            1.0: "Высокая (более креативные ответы)"
        }
        
        await query.edit_message_text(
            f"🌡️ Температура установлена: {temperature}\n"
            f"📝 {temp_desc.get(temperature, 'Пользовательская настройка')}"
        )
    
    else:
        await query.edit_message_text("❌ Неверный запрос.")

# ------------------------------------------------------------------
# 5. Обработка текстовых сообщений с контекстом
# ------------------------------------------------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    user_id = update.effective_user.id
    logging.info("Сообщение от %s: %s", user_id, user_text[:100])

    # Получаем настройки пользователя
    model_name = context.user_data.get("model", DEFAULT_MODEL)
    temperature = context.user_data.get("temperature", 0.7)
    
    # Определяем системный промпт
    system_prompt = SYSTEM_PROMPTS["general"]
    if "cod" in model_name.lower():
        system_prompt = SYSTEM_PROMPTS["code"]
    
    # Получаем контекст беседы
    conversation = get_conversation_context(user_id, context)
    context_str = format_context_for_prompt(conversation)
    
    # Формируем финальный промпт с контекстом
    final_prompt = f"{context_str}НОВЫЙ ВОПРОС: {user_text}"
    
    # Отправляем запрос
    answer = query_ollama(
        prompt=final_prompt,
        model_name=model_name,
        system_prompt=system_prompt,
        temperature=temperature
    )

    # Сохраняем в контекст
    add_to_context(user_id, context, user_text, answer)
    
    await update.message.reply_text(answer)

# ------------------------------------------------------------------
# 6. Улучшенная обработка изображений
# ------------------------------------------------------------------
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка изображений с улучшенным анализом"""
    if not update.message or not update.message.photo:
        return

    user_id = update.effective_user.id
    model_name = context.user_data.get("model", DEFAULT_MODEL)
    
    # Проверяем поддержку изображений
    if not AVAILABLE_MODELS.get(model_name, {}).get("vision", False):
        await update.message.reply_text(
            f"❌ Модель {model_name} не поддерживает изображения.\n"
            f"Переключитесь на модель с поддержкой изображений: /models"
        )
        return

    # Берём самую крупную версию фото
    photo = update.message.photo[-1]
    file_id = photo.file_id

    try:
        # Отправляем сообщение о начале обработки
        processing_msg = await update.message.reply_text("🔍 Анализирую изображение...")
        
        file_obj = await context.bot.get_file(file_id)
        file_bytes = await file_obj.download_as_bytearray()
 
        # Открываем через PIL и оптимизируем
        img = Image.open(BytesIO(file_bytes))
        
        # Конвертируем в RGB если нужно
        if img.mode != "RGB":
            img = img.convert("RGB")
       
        # Оптимальный размер для большинства моделей
        max_dim = 1024
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # Сохраняем в JPEG с хорошим качеством
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        buf.seek(0)
        jpeg_bytes = buf.getvalue()

        # Кодируем в base64
        image_base64 = base64.b64encode(jpeg_bytes).decode("utf-8")

        # Формируем промпт
        user_prompt = "Детально проанализируй это изображение."
        
        # Добавляем подпись пользователя если есть
        if update.message.caption:
            user_prompt = f"{update.message.caption}\n\nДополнительно: {user_prompt}"

        # Получаем контекст и системный промпт
        conversation = get_conversation_context(user_id, context)
        context_str = format_context_for_prompt(conversation)
        
        final_prompt = f"{context_str}АНАЛИЗ ИЗОБРАЖЕНИЯ: {user_prompt}"
        
        temperature = context.user_data.get("temperature", 0.7)
        
        # Отправляем запрос с изображением
        answer = query_ollama(
            prompt=final_prompt,
            model_name=model_name,
            system_prompt=SYSTEM_PROMPTS["vision"],
            images=[image_base64],
            temperature=temperature,
            max_tokens=1500
        )

        # Удаляем сообщение о обработке
        await processing_msg.delete()
        
        # Сохраняем в контекст
        add_to_context(user_id, context, f"[ИЗОБРАЖЕНИЕ] {user_prompt}", answer)
        
        await update.message.reply_text(answer)

    except Exception as exc:
        logging.exception("Ошибка при обработке изображения: %s", exc)
        try:
            await processing_msg.delete()
        except:
            pass
        await update.message.reply_text(
            "❌ Не удалось обработать изображение.\n"
            "Возможные причины:\n"
            "• Изображение повреждено\n"
            "• Модель временно недоступна\n"
            "• Слишком большой размер файла"
        )

# ------------------------------------------------------------------
# 7. Обработка документов с изображениями
# ------------------------------------------------------------------
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка документов-изображений"""
    if not update.message or not update.message.document:
        return
    
    doc = update.message.document
    # Проверяем, что это изображение
    if not doc.mime_type or not doc.mime_type.startswith('image/'):
        await update.message.reply_text("📎 Поддерживаются только изображения в формате документов.")
        return
    
    # Используем ту же логику что и для фото
    await handle_photo_document(update, context, doc)

async def handle_photo_document(update: Update, context: ContextTypes.DEFAULT_TYPE, document):
    """Вспомогательная функция для обработки изображений-документов"""
    user_id = update.effective_user.id
    model_name = context.user_data.get("model", DEFAULT_MODEL)
    
    if not AVAILABLE_MODELS.get(model_name, {}).get("vision", False):
        await update.message.reply_text(
            f"❌ Модель {model_name} не поддерживает изображения."
        )
        return

    try:
        processing_msg = await update.message.reply_text("🔍 Обрабатываю документ с изображением...")
        
        file_obj = await context.bot.get_file(document.file_id)
        file_bytes = await file_obj.download_as_bytearray()
        
        # Аналогичная обработка как для фото
        img = Image.open(BytesIO(file_bytes)).convert("RGB")
        
        max_dim = 1024
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        buf.seek(0)
        image_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        user_prompt = f"Проанализируй изображение из документа '{document.file_name}'."
        if update.message.caption:
            user_prompt = f"{update.message.caption}\n\n{user_prompt}"

        conversation = get_conversation_context(user_id, context)
        context_str = format_context_for_prompt(conversation)
        final_prompt = f"{context_str}АНАЛИЗ ДОКУМЕНТА: {user_prompt}"
        
        answer = query_ollama(
            prompt=final_prompt,
            model_name=model_name,
            system_prompt=SYSTEM_PROMPTS["vision"],
            images=[image_base64],
            temperature=context.user_data.get("temperature", 0.7)
        )

        await processing_msg.delete()
        add_to_context(user_id, context, f"[ДОКУМЕНТ] {user_prompt}", answer)
        await update.message.reply_text(answer)

    except Exception as exc:
        logging.exception("Ошибка при обработке документа: %s", exc)
        try:
            await processing_msg.delete()
        except:
            pass
        await update.message.reply_text("❌ Не удалось обработать документ с изображением.")

# -----------------------------------------------------------------
# 8. Запуск
# -----------------------------------------------------------------
if __name__ == "__main__":
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("models", models_cmd))
    app.add_handler(CommandHandler("model", set_model_cmd))
    app.add_handler(CommandHandler("clear", clear_context))
    app.add_handler(CommandHandler("settings", settings_cmd))
    
    # Inline кнопки
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Изображения
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(
        filters.Document.IMAGE, 
        handle_document
    ))

    # Обработка ошибок
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logging.exception("Ошибка при обработке update %s", update)
        
        # Пытаемся отправить сообщение об ошибке пользователю
        if update and hasattr(update, 'effective_message') and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "❌ Произошла ошибка при обработке запроса. Попробуйте еще раз."
                )
            except:
                pass

    app.add_error_handler(error_handler)

    logging.info("🚀 Бот запущен и готов к работе!")
    app.run_polling()