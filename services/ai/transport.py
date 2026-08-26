"""Lifecycle-owned HTTP transport for AI provider requests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx


class AIProviderTransport:
    """Reuse connections while allowing deterministic client injection in tests."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._client_factory: Callable[..., httpx.AsyncClient] | None = None
        self._lock = asyncio.Lock()

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
