#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Configuration: constants, model registry, system prompts, i18n."""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram & Ollama ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
OLLAMA_SERVER_URL: str = os.getenv("OLLAMA_SERVER_URL", "http://localhost:11434")

# ── Limits ────────────────────────────────────────────────────────
MAX_CONCURRENT_REQUESTS: int = 3
REQUEST_TIMEOUT: int = 120
CACHE_TTL: int = 3600          # seconds
MAX_CONTEXT_MESSAGES: int = 10
CLEANUP_INTERVAL: int = 600   # seconds between cache cleanups

# ── Model registry ────────────────────────────────────────────────
AVAILABLE_MODELS: dict = {
    "qwen2.5vl:7b":      {"vision": True,  "description": "Fast model with image support"},
    "gpt-oss:20b":       {"vision": False, "description": "Powerful text model"},
    "qwen3:32b":         {"vision": False, "description": "Large text model"},
    "qwen2.5vl:32b":     {"vision": True,  "description": "Large model with image support"},
    "qwen2.5-coder:7b":  {"vision": False, "description": "Coding specialist model"},
    "qwen2.5-coder:1.5b":{"vision": False, "description": "Fast coding model"},
}

DEFAULT_MODEL: str = "qwen2.5vl:7b"

# ── System prompts ────────────────────────────────────────────────
SYSTEM_PROMPTS: dict = {
    "general": (
        "You are a helpful AI assistant. Answer clearly, informatively and to the point. "
        "If you don't know the answer, say so honestly. Always try to be as useful as possible."
    ),
    "vision": (
        "You are an AI assistant capable of analysing images. "
        "Examine images carefully and give detailed, accurate descriptions. "
        "Point out important details, colours, objects, people, and any text in the image. "
        "If the image contains text, read it and include it in your answer."
    ),
    "code": (
        "You are a specialised AI assistant for programming. "
        "Write clean, readable code with comments. "
        "Explain complex concepts in simple language. "
        "Always follow best practices."
    ),
}

# ── i18n ─────────────────────────────────────────────────────────
LANGUAGES: dict = {
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
        "name": "Русский",
        "system_prompt_suffix": "Отвечай на русском языке.",
        "processing": "🔍 Обрабатываю запрос...",
        "error": "❌ Произошла ошибка",
        "cancelled": "🛑 Запрос отменён",
    },
}

DEFAULT_LANGUAGE: str = "ru"
