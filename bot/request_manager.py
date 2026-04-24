#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Limits concurrent Ollama requests and supports per-user cancellation."""

import asyncio
from typing import Callable, Dict

from bot.config import MAX_CONCURRENT_REQUESTS


class RequestManager:
    """Semaphore-based concurrency limiter with per-user task tracking."""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_REQUESTS) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active_requests: Dict[int, asyncio.Task] = {}

    async def execute_request(self, user_id: int, func: Callable, *args, **kwargs):
        """Run *func* under the semaphore; track task for cancellation."""
        async with self.semaphore:
            task = asyncio.current_task()
            self.active_requests[user_id] = task
            try:
                return await func(*args, **kwargs)
            finally:
                self.active_requests.pop(user_id, None)

    def cancel_user_request(self, user_id: int) -> bool:
        """Cancel the active request for *user_id*. Returns True if cancelled."""
        task = self.active_requests.get(user_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def active_count(self) -> int:
        """Number of in-flight requests."""
        return sum(1 for t in self.active_requests.values() if not t.done())
