#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Async client for the Ollama REST API."""

import asyncio
import json
import logging
from typing import List, Optional

import httpx

from bot.cache import ResponseCache
from bot.config import DEFAULT_MODEL, OLLAMA_SERVER_URL, REQUEST_TIMEOUT

log = logging.getLogger(__name__)

# Module-level shared cache instance
response_cache = ResponseCache()


async def query_ollama(
    prompt: str,
    model_name: str = DEFAULT_MODEL,
    system_prompt: Optional[str] = None,
    images: Optional[List[str]] = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    use_cache: bool = True,
) -> str:
    """
    Send a prompt to Ollama and return the text response.

    Parameters
    ----------
    prompt       : user message (may include conversation context prefix)
    model_name   : Ollama model tag
    system_prompt: optional system instruction prepended to the prompt
    images       : list of base64-encoded images for vision models
    temperature  : sampling temperature (0.0-1.0)
    max_tokens   : max tokens to generate
    use_cache    : whether to check/write the response cache
    """
    # Cache lookup (text-only requests)
    if use_cache and not images:
        cached = response_cache.get(prompt, model_name, temperature)
        if cached:
            log.debug("Cache hit for model=%s", model_name)
            return f"🔄 {cached}"

    full_prompt = f"SYSTEM: {system_prompt}\n\nUSER: {prompt}" if system_prompt else prompt

    payload = {
        "model": model_name,
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if images:
        payload["images"] = images

    url = f"{OLLAMA_SERVER_URL}/api/generate"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        answer = data.get("response", "").strip()
        if not answer:
            return "🤖 Could not generate a response — try rephrasing your question."

        if use_cache and not images:
            response_cache.set(prompt, model_name, temperature, answer)

        return answer

    except asyncio.CancelledError:
        return "🛑 Request was cancelled."
    except httpx.TimeoutException:
        return "⏱️ Request timed out. Try a simpler question."
    except httpx.RequestError as exc:
        log.error("Ollama connection error: %s", exc)
        return f"❌ Connection error: {exc}"
    except json.JSONDecodeError:
        return "❌ Could not parse Ollama response."
