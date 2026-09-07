"""Lifecycle-owned HTTP transport for AI provider requests."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx


class AIProviderTransport:
    """Reuse connections while allowing deterministic client injection in tests."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._client_factory: Callable[..., httpx.AsyncClient] | None = None
        self._lock = asyncio.Lock()
        self._rpm_windows: dict[str, deque[float]] = {}
        self._last_rpm_cleanup = 0.0
        self._rpm_lock = asyncio.Lock()

    async def acquire_rpm(self, limit: int, base_url: str, api_key: str | None) -> None:
        """Share a rolling minute per origin/credential in this API process."""
        if not 1 <= limit <= 10_000:
            raise ValueError("AI RPM must be between 1 and 10000")
        origin = urlsplit(base_url)
        port = origin.port or (443 if origin.scheme == "https" else 80)
        identity = f"{origin.scheme}://{origin.hostname}:{port}\n{api_key or ''}"
        key = hashlib.sha256(identity.encode()).hexdigest()
        while True:
            async with self._rpm_lock:
                now = time.monotonic()
                if now - self._last_rpm_cleanup >= 60:
                    self._rpm_windows = {
                        key: window
                        for key, window in self._rpm_windows.items()
                        if window and now - window[-1] < 60
                    }
                    self._last_rpm_cleanup = now
                window = self._rpm_windows.setdefault(key, deque())
                while window and now - window[0] >= 60:
                    window.popleft()
                if len(window) < limit:
                    window.append(now)
                    return
                wait = max(0.01, 60 - (now - window[0]))
            await asyncio.sleep(wait)

    async def _get_client(self) -> httpx.AsyncClient:
        factory = httpx.AsyncClient
        if (
            self._client is not None
            and not self._client.is_closed
            and self._client_factory is factory
        ):
            return self._client
        async with self._lock:
            if (
                self._client is not None
                and not self._client.is_closed
                and self._client_factory is factory
            ):
                return self._client
            previous = self._client
            self._client = factory(
                timeout=httpx.Timeout(60),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
                follow_redirects=False,
            )
            self._client_factory = factory
            if previous is not None and not previous.is_closed:
                await previous.aclose()
            return self._client

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> AsyncIterator[httpx.Response]:
        client = await self._get_client()
        async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
            yield response

    async def close(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
            self._client_factory = None
        if client is not None and not client.is_closed:
            await client.aclose()


ai_provider_transport = AIProviderTransport()
