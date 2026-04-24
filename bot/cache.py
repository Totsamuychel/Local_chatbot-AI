#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""In-memory response cache with TTL-based expiry."""

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from bot.config import CACHE_TTL


@dataclass
class CacheEntry:
    """Single cached response."""
    response: str
    timestamp: float
    model: str


class ResponseCache:
    """Thread-safe in-memory cache for Ollama responses."""

    def __init__(self, ttl: int = CACHE_TTL) -> None:
        self.cache: Dict[str, CacheEntry] = {}
        self.ttl = ttl
        self._hits: int = 0
        self._requests: int = 0

    # ── private ──────────────────────────────────────────────────
    def _key(self, prompt: str, model: str, temperature: float) -> str:
        content = f"{prompt}|{model}|{temperature}"
        return hashlib.md5(content.encode()).hexdigest()

    # ── public ───────────────────────────────────────────────────
    def get(self, prompt: str, model: str, temperature: float) -> Optional[str]:
        """Return cached response or None if missing / expired."""
        self._requests += 1
        key = self._key(prompt, model, temperature)
        entry = self.cache.get(key)
        if entry is None:
            return None
        if time.time() - entry.timestamp >= self.ttl:
            del self.cache[key]
            return None
        self._hits += 1
        return entry.response

    def set(self, prompt: str, model: str, temperature: float, response: str) -> None:
        """Store a response in the cache."""
        key = self._key(prompt, model, temperature)
        self.cache[key] = CacheEntry(response=response, timestamp=time.time(), model=model)

    def clear_expired(self) -> int:
        """Remove stale entries; return how many were removed."""
        cutoff = time.time() - self.ttl
        stale = [k for k, v in self.cache.items() if v.timestamp < cutoff]
        for k in stale:
            del self.cache[k]
        return len(stale)

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics dict."""
        self.clear_expired()
        hit_rate = (self._hits / self._requests * 100) if self._requests else 0.0
        return {
            "total_entries": len(self.cache),
            "memory_usage_kb": len(str(self.cache)) / 1024,
            "hit_rate": hit_rate,
            "hits": self._hits,
            "requests": self._requests,
        }
