#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import logging
import json
import asyncio
import hashlib
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Set
from dataclasses import dataclass
from PIL import Image
from io import BytesIO
import base64
from dotenv import load_dotenv

load_dotenv() 

# Асинхронный HTTP клиент вместо requests
import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
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

# Ограничения для предотвращения перегрузки
MAX_CONCURRENT_REQUESTS = 3  # Максимум одновременных запросов к Ollama
REQUEST_TIMEOUT = 120  # Таймаут запроса в секундах
CACHE_TTL = 3600  # Время жизни кеша в секундах (1 час)
MAX_CONTEXT_MESSAGES = 10  # Максимум сообщений в контексте

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

# Поддержка языков (базовая версия)
LANGUAGES = {
    
    "en": {
        "name": "English", 
        "system_prompt_suffix": "Respond in English.",
        "processing": "🔍 Processing request...",
        "error": "❌ An error occurred",
        "cancelled": "🛑 Request cancelled",
    },
    "uk": {
        "name": "Українська",
        "system_prompt_suffix": "Відповідай українською мовою.",
        "processing": "🔍 Обробляю запит...",
        "error": "❌ Сталася помилка", 
        "cancelled": "🛑 Запит скасовано",
    },
    "ru": {
        "name": "Тест",
        "system_prompt_suffix": "Отвечай на русском языке.",
        "processing": "🔍 Обрабатываю запрос...",
        "error": "❌ Сталася помилка", 
        "cancelled": "🛑 Запит скасовано",
    }
}

# Системные промпты
SYSTEM_PROMPTS = {
    "general": """Ты — полезный AI-ассистент. Отвечай четко, информативно и по делу. 
    Если не знаешь ответа, честно об этом скажи. Всегда стремись быть максимально полезным пользователю.""",
    
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
# 2. Структуры данных и кеш
# -----------------------------------------------------------------

@dataclass
class CacheEntry:
    """Запись в кеше ответов"""
    response: str
    timestamp: float
    model: str

@dataclass
class ContextStats:
    """Статистика контекста пользователя"""
    total_messages: int
    memory_usage_kb: float
    oldest_message_time: Optional[datetime]
    newest_message_time: Optional[datetime]

class RequestManager:
    """Менеджер для ограничения одновременных запросов"""
    def __init__(self, max_concurrent: int = MAX_CONCURRENT_REQUESTS):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active_requests: Dict[int, asyncio.Task] = {}  # user_id -> task
        
    async def execute_request(self, user_id: int, request_func, *args, **kwargs):
        """Выполняет запрос с ограничением одновременности"""
        async with self.semaphore:
            try:
                # Сохраняем текущую задачу для возможности отмены
                task = asyncio.current_task()
                self.active_requests[user_id] = task
                
                return await request_func(*args, **kwargs)
            finally:
                # Удаляем задачу после завершения
                self.active_requests.pop(user_id, None)
                
    def cancel_user_request(self, user_id: int) -> bool:
        """Отменяет активный запрос пользователя"""
        if user_id in self.active_requests:
            task = self.active_requests[user_id]
            if not task.done():
                task.cancel()
                return True
        return False
        
    def get_active_requests(self) -> int:
        """Возвращает количество активных запросов"""
        return len([task for task in self.active_requests.values() if not task.done()])

class ResponseCache:
    """Кеш для ответов модели"""
    def __init__(self, ttl: int = CACHE_TTL):
        self.cache: Dict[str, CacheEntry] = {}
        self.ttl = ttl
        
    def _generate_key(self, prompt: str, model: str, temperature: float) -> str:
        """Генерирует ключ для кеша"""
        content = f"{prompt}|{model}|{temperature}"
        return hashlib.md5(content.encode()).hexdigest()
        
    def get(self, prompt: str, model: str, temperature: float) -> Optional[str]:
        """Получает ответ из кеша"""
        key = self._generate_key(prompt, model, temperature)
        
        if key in self.cache:
            entry = self.cache[key]
            if time.time() - entry.timestamp < self.ttl:
                return entry.response
            else:
                # Удаляем устаревшую запись
                del self.cache[key]
        
        return None
        
    def set(self, prompt: str, model: str, temperature: float, response: str):
        """Сохраняет ответ в кеш"""
        key = self._generate_key(prompt, model, temperature)
        self.cache[key] = CacheEntry(
            response=response,
            timestamp=time.time(),
            model=model
        )
        
    def clear_expired(self):
        """Очищает устаревшие записи"""
        current_time = time.time()
        expired_keys = [
            key for key, entry in self.cache.items()
            if current_time - entry.timestamp >= self.ttl
        ]
        for key in expired_keys:
            del self.cache[key]
            
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику кеша"""
        self.clear_expired()
        return {
            "total_entries": len(self.cache),
            "memory_usage_kb": len(str(self.cache)) / 1024,
            "hit_rate": getattr(self, '_hits', 0) / max(getattr(self, '_requests', 1), 1) * 100
        }

# Глобальные объекты
request_manager = RequestManager()
response_cache = ResponseCache()

# -----------------------------------------------------------------
# 3. Асинхронная функция запроса к Ollama
# -----------------------------------------------------------------

async def query_ollama_async(
    prompt: str, 
    model_name: str = DEFAULT_MODEL,
    system_prompt: Optional[str] = None,
    images: Optional[List[str]] = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    use_cache: bool = True
) -> str:
    """
    Асинхронная функция запроса к Ollama с кешированием
    """
    
    # Проверяем кеш (только для текстовых запросов без изображений)
    if use_cache and not images:
        cached_response = response_cache.get(prompt, model_name, temperature)
        if cached_response:
            response_cache._hits = getattr(response_cache, '_hits', 0) + 1
            return f"🔄 {cached_response}"
    
    response_cache._requests = getattr(response_cache, '_requests', 0) + 1
    
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
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            
            answer = data.get("response", "").strip()
            if not answer:
                return "🤖 Не смог сгенерировать ответ, попробуйте переформулировать вопрос."
            
            # Сохраняем в кеш (только текстовые ответы без изображений)
            if use_cache and not images:
                response_cache.set(prompt, model_name, temperature, answer)
            
            return answer
            
    except asyncio.CancelledError:
        return "🛑 Запрос был отменен"
    except httpx.TimeoutException:
        return "⏱️ Запрос занял слишком много времени. Попробуйте с более простым вопросом."
    except httpx.RequestError as exc:
        return f"❌ Ошибка соединения с Ollama: {exc}"
    except json.JSONDecodeError:
        return "❌ Не удалось разобрать ответ от Ollama."

# -----------------------------------------------------------------
# 4. Функции для работы с контекстом
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
    conversation.append({
        "user": user_msg, 
        "bot": bot_response,
        "timestamp": datetime.now()
    })
    
    # Ограничиваем контекст
    if len(conversation) > MAX_CONTEXT_MESSAGES:
        conversation.pop(0)

def get_context_stats(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> ContextStats:
    """Возвращает статистику контекста пользователя"""
    conversation = get_conversation_context(user_id, context)
    
    if not conversation:
        return ContextStats(0, 0.0, None, None)
    
    # Подсчитываем размер контекста в памяти
    memory_usage = len(str(conversation)) / 1024  # в KB
    
    timestamps = [msg.get("timestamp") for msg in conversation if msg.get("timestamp")]
    oldest = min(timestamps) if timestamps else None
    newest = max(timestamps) if timestamps else None
    
    return ContextStats(
        total_messages=len(conversation),
        memory_usage_kb=memory_usage,
        oldest_message_time=oldest,
        newest_message_time=newest
    )

def clear_context_partial(user_id: int, context: ContextTypes.DEFAULT_TYPE, keep_last: int = 2):
    """Очищает контекст, оставляя последние N сообщений"""
    conversation = get_conversation_context(user_id, context)
    if len(conversation) > keep_last:
        # Оставляем только последние сообщения
        context.bot_data["conversations"][user_id] = conversation[-keep_last:]
        return len(conversation) - keep_last
    return 0

def format_context_for_prompt(conversation: List[Dict]) -> str:
    """Форматирует контекст для промпта"""
    if not conversation:
        return ""
    
    context_str = "КОНТЕКСТ БЕСЕДЫ:\n"
    for msg in conversation[-5:]:  # Берем последние 5 сообщений
        context_str += f"Пользователь: {msg['user']}\nВы: {msg['bot']}\n\n"
    
    return context_str

def get_user_language(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Получает язык пользователя"""
    return context.user_data.get("language", "ru")

def get_localized_text(context: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    """Получает локализованный текст"""
    lang = get_user_language(context)
    return LANGUAGES.get(lang, LANGUAGES["ru"]).get(key, key)

# -----------------------------------------------------------------
# 5. Handlers Telegram
# -----------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_language(context)
    
    keyboard = [
        [InlineKeyboardButton("Тест", callback_data="lang:ru")],
        [InlineKeyboardButton("🇺🇸 English", callback_data="lang:en")], 
        [InlineKeyboardButton("🇺🇦 Українська", callback_data="lang:uk")],
    ]
    
    await update.message.reply_text(
        "👋 Привет! Выберите язык / Choose language / Оберіть мову:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает основное меню после выбора языка"""
    await update.reply_text(
        "👋 Привет! Я AI-ассистент, созданный Даней.\n\n"
        "🤖 Могу помочь с:\n"
        "• Ответами на вопросы\n"
        "• Анализом изображений\n"
        "• Программированием\n"
        "• Переводом и объяснениями\n\n"
        "📋 Команды:\n"
        "/models — показать доступные модели\n"
        "/model <имя> — установить модель\n"
        "/context — показать статистику контекста\n"
        "/clear — очистить контекст беседы\n"
        "/cancel — отменить текущий запрос\n"
        "/settings — настройки бота\n"
        "/cache — статистика кеша\n"
        "/help — справка"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model_info = ""
    for name, info in AVAILABLE_MODELS.items():
        vision_support = "🖼️" if info["vision"] else "📝"
        model_info += f"{vision_support} {name} - {info['description']}\n"
    
    # Статистика системы
    active_requests = request_manager.get_active_requests()
    cache_stats = response_cache.get_stats()
    
    await update.message.reply_text(
        "📖 Справка по командам:\n\n"
        "/start — запустить бота\n"
        "/models — показать доступные модели\n"
        "/model <имя> — установить выбранную модель\n"
        "/context — статистика контекста беседы\n"
        "/clear — очистить контекст беседы\n"
        "/cancel — отменить текущий запрос к модели\n"
        "/settings — настройки температуры и языка\n"
        "/cache — статистика кеша ответов\n"
        "/help — эта справка\n\n"
        f"🤖 Доступные модели:\n{model_info}\n"
        f"📊 Система:\n"
        f"• Активные запросы: {active_requests}/{MAX_CONCURRENT_REQUESTS}\n"
        f"• Записей в кеше: {cache_stats['total_entries']}\n"
        f"• Попадания в кеш: {cache_stats['hit_rate']:.1f}%\n\n"
        "💡 Просто отправьте текст или изображение для обработки!"
    )

async def context_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику контекста пользователя"""
    user_id = update.effective_user.id
    stats = get_context_stats(user_id, context)
    
    if stats.total_messages == 0:
        await update.message.reply_text("📊 Контекст пуст. Начните диалог!")
        return
    
    # Создаем кнопки для управления контекстом
    keyboard = [
        [InlineKeyboardButton("🧹 Очистить все", callback_data="clear_context:all")],
        [InlineKeyboardButton("📝 Оставить последние 2", callback_data="clear_context:2")],
        [InlineKeyboardButton("📝 Оставить последние 5", callback_data="clear_context:5")],
    ]
    
    oldest_str = stats.oldest_message_time.strftime("%d.%m %H:%M") if stats.oldest_message_time else "—"
    newest_str = stats.newest_message_time.strftime("%d.%m %H:%M") if stats.newest_message_time else "—"
    
    await update.message.reply_text(
        f"📊 Статистика контекста:\n\n"
        f"💬 Сообщений в памяти: {stats.total_messages}/{MAX_CONTEXT_MESSAGES}\n"
        f"💾 Используется памяти: {stats.memory_usage_kb:.1f} KB\n"
        f"📅 Первое сообщение: {oldest_str}\n"
        f"🕐 Последнее сообщение: {newest_str}\n\n"
        f"💡 Управление контекстом:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cache_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику кеша"""
    stats = response_cache.get_stats()
    
    keyboard = [
        [InlineKeyboardButton("🗑️ Очистить кеш", callback_data="clear_cache")],
    ]
    
    await update.message.reply_text(
        f"📊 Статистика кеша ответов:\n\n"
        f"💾 Записей в кеше: {stats['total_entries']}\n"
        f"📈 Попадания в кеш: {stats['hit_rate']:.1f}%\n"
        f"🗄️ Используется памяти: {stats['memory_usage_kb']:.1f} KB\n"
        f"⏱️ Время жизни записи: {CACHE_TTL//60} минут\n\n"
        f"💡 Кеш ускоряет повторные запросы",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cancel_request_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменяет текущий запрос пользователя к модели"""
    user_id = update.effective_user.id
    
    if request_manager.cancel_user_request(user_id):
        lang_text = get_localized_text(context, "cancelled")
        await update.message.reply_text(f"{lang_text} ✅")
    else:
        await update.message.reply_text("🤷‍♂️ Нет активных запросов для отмены")

async def models_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показываем список моделей с кнопками и описанием."""
    kb = []
    for name, info in AVAILABLE_MODELS.items():
        vision_icon = "🖼️" if info["vision"] else "📝"
        current_icon = "✅" if context.user_data.get("model", DEFAULT_MODEL) == name else ""
        button_text = f"{vision_icon} {name} {current_icon}"
        kb.append([InlineKeyboardButton(button_text, callback_data=f"model:{name}")])
    
    active_requests = request_manager.get_active_requests()
    
    await update.message.reply_text(
        "🔧 Выберите модель:\n\n"
        "🖼️ = поддержка изображений\n"
        "📝 = только текст\n"
        "✅ = текущая модель\n\n"
        f"📊 Активные запросы: {active_requests}/{MAX_CONCURRENT_REQUESTS}",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очищает контекст беседы"""
    user_id = update.effective_user.id
    if "conversations" in context.bot_data and user_id in context.bot_data["conversations"]:
        context.bot_data["conversations"][user_id] = []
    
    await update.message.reply_text("🧹 Контекст беседы полностью очищен!")

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройки бота"""
    current_model = context.user_data.get("model", DEFAULT_MODEL)
    current_temp = context.user_data.get("temperature", 0.7)
    current_lang = get_user_language(context)
    lang_name = LANGUAGES[current_lang]["name"]
    
    kb = [
        [InlineKeyboardButton("🌡️ Температура: Низкая (0.3)", callback_data="temp:0.3")],
        [InlineKeyboardButton("🌡️ Температура: Средняя (0.7)", callback_data="temp:0.7")],
        [InlineKeyboardButton("🌡️ Температура: Высокая (1.0)", callback_data="temp:1.0")],
        [InlineKeyboardButton("🌍 Изменить язык", callback_data="change_lang")],
    ]
    
    await update.message.reply_text(
        f"⚙️ Текущие настройки:\n"
        f"🤖 Модель: {current_model}\n"
        f"🌡️ Температура: {current_temp}\n"
        f"🌍 Язык: {lang_name}\n\n"
        f"💡 Выберите что изменить:",
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
    user_id = update.effective_user.id

    if data.startswith("lang:"):
        _, lang_code = data.split(":", 1)
        if lang_code in LANGUAGES:
            context.user_data["language"] = lang_code
            lang_name = LANGUAGES[lang_code]["name"]
            await query.edit_message_text(f"✅ Язык изменен на: {lang_name}")
            # После выбора языка, покажем основное меню новым сообщением
            await show_main_menu(query.message, context)
        else:
            await query.edit_message_text("❌ Неизвестный язык.")

    elif data == "change_lang":
        keyboard = [
            [InlineKeyboardButton("🇺🇸 English", callback_data="lang:en")],
            [InlineKeyboardButton("🇺🇦 Українська", callback_data="lang:uk")],
        ]
        await query.edit_message_text(
            "🌍 Выберите язык / Choose language / Оберіть мову:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("model:"):
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

    elif data.startswith("clear_context:"):
        _, action = data.split(":", 1)
        if action == "all":
            if "conversations" in context.bot_data and user_id in context.bot_data["conversations"]:
                context.bot_data["conversations"][user_id] = []
            await query.edit_message_text("🧹 Контекст полностью очищен!")
        else:
            try:
                keep_last = int(action)
                cleared_count = clear_context_partial(user_id, context, keep_last)
                await query.edit_message_text(f"🗑️ Удалено {cleared_count} старых сообщений, оставлено последние {keep_last}.")
            except ValueError:
                await query.edit_message_text("❌ Неверное действие.")

    elif data == "clear_cache":
        response_cache.cache.clear()
        await query.edit_message_text("🗑️ Кеш ответов очищен!")

    elif data.startswith("cancel_request:"):
        target_user_id = int(data.split(":")[1])
        if request_manager.cancel_user_request(target_user_id):
            lang_text = get_localized_text(context, "cancelled")
            await query.edit_message_text(f"{lang_text} ✅")
        else:
            await query.edit_message_text("🤷‍♂️ Нет активного запроса для отмены.")

    else:
        await query.edit_message_text("❌ Неизвестный запрос.")

# ------------------------------------------------------------------
# 6. Обработка текстовых сообщений с улучшениями
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
    lang = get_user_language(context)
    
    # Определяем системный промпт с учетом языка
    system_prompt = SYSTEM_PROMPTS["general"]
    if "cod" in model_name.lower():
        system_prompt = SYSTEM_PROMPTS["code"]
    
    # Добавляем языковую инструкцию
    system_prompt += f" {LANGUAGES[lang]['system_prompt_suffix']}"
    
    # Получаем контекст беседы
    conversation = get_conversation_context(user_id, context)
    context_str = format_context_for_prompt(conversation)
    
    # Формируем финальный промпт с контекстом
    final_prompt = f"{context_str}НОВЫЙ ВОПРОС: {user_text}"
    
    # Показываем индикатор обработки
    processing_text = get_localized_text(context, "processing")
    processing_msg = await update.message.reply_text(processing_text)
    
    # Создаем кнопку отмены
    cancel_keyboard = [
        [InlineKeyboardButton("🛑 Отменить запрос", callback_data=f"cancel_request:{user_id}")],
    ]
    
    try:
        # Обновляем сообщение с кнопкой отмены
        await processing_msg.edit_text(
            f"{processing_text}\n💡 Можете отменить запрос:",
            reply_markup=InlineKeyboardMarkup(cancel_keyboard)
        )
        
        # Выполняем запрос через менеджер запросов
        answer = await request_manager.execute_request(
            user_id,
            query_ollama_async,
            prompt=final_prompt,
            model_name=model_name,
            system_prompt=system_prompt,
            temperature=temperature
        )
        
        # Удаляем сообщение о обработке
        await processing_msg.delete()
        
        # Сохраняем в контекст
        add_to_context(user_id, context, user_text, answer)
        
        await update.message.reply_text(answer)
        
    except asyncio.CancelledError:
        await processing_msg.edit_text(get_localized_text(context, "cancelled"))
    except Exception as exc:
        logging.exception("Ошибка при обработке текста: %s", exc)
        error_text = get_localized_text(context, "error")
        await processing_msg.edit_text(f"{error_text}: {str(exc)}")

# ------------------------------------------------------------------
# 7. Улучшенная обработка изображений
# ------------------------------------------------------------------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка изображений с улучшенным анализом"""
    if not update.message or not update.message.photo:
        return

    user_id = update.effective_user.id
    model_name = context.user_data.get("model", DEFAULT_MODEL)
    lang = get_user_language(context)
    
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
        processing_text = get_localized_text(context, "processing")
        processing_msg = await update.message.reply_text(f"🖼️ {processing_text}")
        
        # Создаем кнопку отмены
        cancel_keyboard = [
            [InlineKeyboardButton("🛑 Отменить", callback_data=f"cancel_request:{user_id}")],
        ]
        
        await processing_msg.edit_text(
            f"🖼️ {processing_text}\n💡 Можете отменить:",
            reply_markup=InlineKeyboardMarkup(cancel_keyboard)
        )
        
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
        
        # Системный промпт с языковой инструкцией
        system_prompt = SYSTEM_PROMPTS["vision"] + f" {LANGUAGES[lang]['system_prompt_suffix']}"
        
        temperature = context.user_data.get("temperature", 0.7)
        
        # Отправляем запрос с изображением через менеджер запросов
        answer = await request_manager.execute_request(
            user_id,
            query_ollama_async,
            prompt=final_prompt,
            model_name=model_name,
            system_prompt=system_prompt,
            images=[image_base64],
            temperature=temperature,
            max_tokens=1500,
            use_cache=False  # Не кешируем ответы с изображениями
        )

        # Удаляем сообщение о обработке
        await processing_msg.delete()
        
        # Сохраняем в контекст
        add_to_context(user_id, context, f"[ИЗОБРАЖЕНИЕ] {user_prompt}", answer)
        
        await update.message.reply_text(answer)

    except asyncio.CancelledError:
        await processing_msg.edit_text(get_localized_text(context, "cancelled"))
    except Exception as exc:
        logging.exception("Ошибка при обработке изображения: %s", exc)
        error_text = get_localized_text(context, "error")
        await processing_msg.edit_text(
            f"{error_text} при обработке изображения.\n"
            "Возможные причины:\n"
            "• Изображение повреждено\n"
            "• Модель временно недоступна\n"
            "• Слишком большой размер файла"
        )

# ------------------------------------------------------------------
# 8. Обработка документов с изображениями
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
    lang = get_user_language(context)
    
    if not AVAILABLE_MODELS.get(model_name, {}).get("vision", False):
        await update.message.reply_text(
            f"❌ Модель {model_name} не поддерживает изображения."
        )
        return

    try:
        processing_text = get_localized_text(context, "processing")
        processing_msg = await update.message.reply_text(f"📎 {processing_text}")
        
        cancel_keyboard = [
            [InlineKeyboardButton("🛑 Отменить", callback_data=f"cancel_request:{user_id}")],
        ]
        
        await processing_msg.edit_text(
            f"📎 {processing_text}\n💡 Можете отменить:",
            reply_markup=InlineKeyboardMarkup(cancel_keyboard)
        )
        
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
        
        system_prompt = SYSTEM_PROMPTS["vision"] + f" {LANGUAGES[lang]['system_prompt_suffix']}"
        
        answer = await request_manager.execute_request(
            user_id,
            query_ollama_async,
            prompt=final_prompt,
            model_name=model_name,
            system_prompt=system_prompt,
            images=[image_base64],
            temperature=context.user_data.get("temperature", 0.7),
            use_cache=False
        )

        await processing_msg.delete()
        add_to_context(user_id, context, f"[ДОКУМЕНТ] {user_prompt}", answer)
        await update.message.reply_text(answer)

    except asyncio.CancelledError:
        await processing_msg.edit_text(get_localized_text(context, "cancelled"))
    except Exception as exc:
        logging.exception("Ошибка при обработке документа: %s", exc)
        error_text = get_localized_text(context, "error")
        await processing_msg.edit_text(f"{error_text} при обработке документа.")

# -----------------------------------------------------------------
# 9. Фоновые задачи
# -----------------------------------------------------------------

async def cleanup_task():
    """Фоновая задача для очистки кеша и статистики"""
    while True:
        try:
            # Очищаем устаревшие записи из кеша каждые 10 минут
            response_cache.clear_expired()
            logging.info(f"Очистка кеша завершена. Записей: {len(response_cache.cache)}")
            await asyncio.sleep(600)  # 10 минут
        except Exception as exc:
            logging.exception("Ошибка в фоновой задаче очистки: %s", exc)
            await asyncio.sleep(60)  # При ошибке ждем 1 минуту

# -----------------------------------------------------------------
# 10. Тесты (базовая структура)
# -----------------------------------------------------------------

class TestBot:
    """Базовые тесты для бота"""
    
    @staticmethod
    def test_cache():
        """Тест кеширования ответов"""
        # Создаем тестовый кеш
        cache = ResponseCache(ttl=10)
        
        # Тестируем сохранение и получение
        cache.set("test prompt", "test_model", 0.7, "test response")
        result = cache.get("test prompt", "test_model", 0.7)
        assert result == "test response", "Кеш не работает правильно"
        
        # Тестируем истечение времени
        time.sleep(11)
        result = cache.get("test prompt", "test_model", 0.7)
        assert result is None, "Устаревшие записи не удаляются"
        
        print("✅ Тест кеша пройден")
    
    @staticmethod
    def test_request_manager():
        """Тест менеджера запросов"""
        manager = RequestManager(max_concurrent=2)
        assert manager.get_active_requests() == 0, "Начальное состояние неверно"
        print("✅ Тест менеджера запросов пройден")

# -----------------------------------------------------------------
# 11. Запуск - ИСПРАВЛЕННАЯ ВЕРСИЯ
# -----------------------------------------------------------------

def main():
    """Главная функция для запуска бота"""

    # Асинхронная функция для задач, которые нужно запустить после инициализации
    async def post_init(application: Application):
        asyncio.create_task(cleanup_task())

    # Создаем приложение с указанием post_init
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)  # <-- ВОТ ИЗМЕНЕНИЕ
        .build()
    )

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("models", models_cmd))
    app.add_handler(CommandHandler("model", set_model_cmd))
    app.add_handler(CommandHandler("context", context_stats_cmd))
    app.add_handler(CommandHandler("clear", clear_context))
    app.add_handler(CommandHandler("cancel", cancel_request_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("cache", cache_stats_cmd))

    # Inline кнопки
    app.add_handler(CallbackQueryHandler(button_callback))

    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Изображения
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))

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

    return app

def run_tests():
    """Запускает тесты"""
    try:
        TestBot.test_cache()
        TestBot.test_request_manager()
        logging.info("✅ Все тесты пройдены")
        return True
    except Exception as exc:
        logging.error(f"❌ Тесты не прошли: {exc}")
        return False

async def start_cleanup_task():
    """Запускает фоновую задачу очистки"""
    return asyncio.create_task(cleanup_task())

if __name__ == "__main__":
    # 1. Запускаем синхронные тесты
    run_tests()

    # 2. Создаем приложение
    app = main()

    # 3. Выводим информацию
    logging.info("🚀 Бот запущен и готов к работе!")
    logging.info(f"📊 Максимум одновременных запросов: {MAX_CONCURRENT_REQUESTS}")
    logging.info(f"💾 TTL кеша: {CACHE_TTL} секунд")
    logging.info(f"💬 Максимум сообщений в контексте: {MAX_CONTEXT_MESSAGES}")

    # 4. Запускаем бота. Этот метод сам создаст и будет управлять asyncio циклом.
    app.run_polling()