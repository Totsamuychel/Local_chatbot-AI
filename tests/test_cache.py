"""Tests for ResponseCache."""

import time
import pytest
from bot.cache import ResponseCache


def test_set_and_get():
    cache = ResponseCache(ttl=60)
    cache.set("hello", "model_x", 0.7, "world")
    assert cache.get("hello", "model_x", 0.7) == "world"


def test_miss_returns_none():
    cache = ResponseCache(ttl=60)
    assert cache.get("missing", "model_x", 0.7) is None


def test_expiry(monkeypatch):
    cache = ResponseCache(ttl=1)
    cache.set("q", "m", 0.5, "r")
    # fast-forward time
    monkeypatch.setattr(time, "time", lambda: time.time.__wrapped__() + 2)
    assert cache.get("q", "m", 0.5) is None


def test_clear_expired():
    cache = ResponseCache(ttl=0)  # everything expires immediately
    cache.set("a", "m", 0.7, "v")
    removed = cache.clear_expired()
    assert removed == 1
    assert len(cache.cache) == 0


def test_stats():
    cache = ResponseCache(ttl=60)
    cache.set("p", "m", 0.7, "r")
    cache.get("p", "m", 0.7)   # hit
    cache.get("x", "m", 0.7)   # miss
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["requests"] == 2
    assert stats["hit_rate"] == pytest.approx(50.0)
