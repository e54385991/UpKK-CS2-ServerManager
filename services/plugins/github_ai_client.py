"""Bounded, serial GitHub API reads with immediate rate-limit termination."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from modules.plugin_ai import DocumentationSource, GitHubVerification, ImportOptions, repository_url

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
NETWORK_RETRIES = 3
NETWORK_RETRY_DELAYS = (0.5, 1.0)


class GitHubImportError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class GitHubRateLimitError(GitHubImportError):
    def __init__(self, message: str, *, reset_at: int | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class GitHubAuthenticationError(GitHubImportError):
    pass


def _integer(value: object) -> int | None:
    try:
        return int(str(value))
    except ValueError, TypeError:
        return None


def response_error(response: httpx.Response) -> GitHubImportError | None:
    code = response.status_code
    body = response.text[:4000].casefold()
    limited = code == 429 or (
        code == 403
        and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or "rate limit" in body
            or "abuse detection" in body
        )
    )
    if limited:
        reset = _integer(response.headers.get("x-ratelimit-reset"))
        retry = _integer(response.headers.get("retry-after"))
        if retry is not None:
            reset = max(reset or 0, int(time.time()) + max(0, retry))
        return GitHubRateLimitError("GitHub rate limit reached; import stopped", reset_at=reset)
    if code == 401:
        return GitHubAuthenticationError("Global GitHub token is invalid or expired", status=code)
    if code == 403:
        return GitHubImportError(
            "GitHub denied access (HTTP 403); check token permissions", status=code
        )
    if code >= 300:
        return GitHubImportError(f"GitHub API returned HTTP {code}", status=code)
    return None


class GitHubAIClient:
    def __init__(
        self,
        token: str,
        *,
        before_request: Callable[[], Awaitable[None]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        interval: float = 2.0,
    ) -> None:
        if not token.strip():
            raise GitHubAuthenticationError("Configure the global GitHub token in Settings")
        self._check = before_request
        self._interval = interval
        self._last_request = 0.0
        self._last_search = 0.0
        self._lock = asyncio.Lock()
        self._blocked: GitHubRateLimitError | None = None
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=30,
            follow_redirects=False,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                # Some upstream proxies advertise gzip while returning an
                # uncompressed body. Avoid client-side decompression failures.
                "Accept-Encoding": "identity",
                "Authorization": f"Bearer {token.strip()}",
                "User-Agent": "UpKK-CS2-ServerManager",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def request(self, path: str, *, params: dict[str, str | int] | None = None) -> Any:
        parsed = urlsplit(path)
        if not path.startswith("/") or path.startswith("//") or parsed.netloc or parsed.scheme:
            raise ValueError("Only relative GitHub API paths are allowed")
        async with self._lock:
            if self._blocked:
                raise self._blocked
            search = path.startswith("/search/")
            delay = self._interval - (time.monotonic() - self._last_request)
            if search and self._interval:
                delay = max(delay, 6.0 - (time.monotonic() - self._last_search))
            if delay > 0:
                await asyncio.sleep(delay)
            if self._check:
                await self._check()
            self._last_request = time.monotonic()
            if search:
                self._last_search = self._last_request
            response = await self._request_with_network_retries(path, params)
            error = response_error(response)
            if isinstance(error, GitHubRateLimitError):
                self._blocked = error
            if error:
                raise error
            try:
                return response.json()
            except ValueError as exc:
                raise GitHubImportError("GitHub returned invalid JSON") from exc

    async def _request_with_network_retries(
        self, path: str, params: dict[str, str | int] | None
    ) -> httpx.Response:
        """Retry transient transport failures while keeping API errors final."""
        for attempt in range(NETWORK_RETRIES):
            try:
                return await self._read_response(path, params)
            except httpx.TimeoutException as exc:
                if attempt + 1 == NETWORK_RETRIES:
                    raise GitHubImportError(
                        f"GitHub API timeout while requesting {path}; check outbound network or proxy"
                    ) from exc
            except httpx.ProxyError as exc:
                raise GitHubImportError(
                    f"GitHub proxy connection failed while requesting {path}; check proxy settings"
                ) from exc
            except httpx.ConnectError as exc:
                if attempt + 1 == NETWORK_RETRIES:
                    raise GitHubImportError(
                        f"Unable to connect to GitHub API while requesting {path}; check DNS, firewall, or proxy"
                    ) from exc
            except httpx.NetworkError as exc:
                if attempt + 1 == NETWORK_RETRIES:
                    raise GitHubImportError(
                        f"GitHub network error while requesting {path}; check outbound network or proxy"
                    ) from exc
            except httpx.DecodingError as exc:
                raise GitHubImportError(
                    f"GitHub response decompression failed while requesting {path}; check proxy configuration"
                ) from exc
            if attempt < len(NETWORK_RETRY_DELAYS):
                await asyncio.sleep(NETWORK_RETRY_DELAYS[attempt])
        raise AssertionError("network retry loop exhausted")

    async def _read_response(
        self, path: str, params: dict[str, str | int] | None
    ) -> httpx.Response:
        async with self._client.stream("GET", path, params=params) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if response.status_code >= 300 and len(data) > 4000:
                    data = data[:4000]
                    break
                if len(data) > MAX_RESPONSE_BYTES:
                    raise GitHubImportError("GitHub response exceeds the size limit")
            return httpx.Response(
                response.status_code, headers=response.headers, content=bytes(data)
            )

    async def optional(self, path: str, *, params: dict[str, str | int] | None = None) -> Any:
        try:
            return await self.request(path, params=params)
        except GitHubImportError as exc:
            if exc.status == 404:
                return None
            raise

    async def verify(self) -> GitHubVerification:
        user = await self.request("/user")
        limits = await self.request("/rate_limit")
        resources = limits.get("resources") or {}
        core, search = resources.get("core") or {}, resources.get("search") or {}
        return GitHubVerification(
            valid=bool(user.get("login"))
            and _integer(core.get("remaining")) is not None
            and _integer(search.get("remaining")) is not None,
            account=str(user.get("login") or ""),
            checked_at=datetime.now(timezone.utc).isoformat(),
            core_remaining=_integer(core.get("remaining")),
            core_reset=_integer(core.get("reset")),
            search_remaining=_integer(search.get("remaining")),
            search_reset=_integer(search.get("reset")),
            message="GitHub token verified",
        )

    async def search(
        self, options: ImportOptions, term: str, page: int = 1
    ) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(days=options.updated_within_days)).date()
        query = f"{term} is:public fork:false archived:false stars:>={options.min_stars} forks:>={options.min_forks} pushed:>={since}"
        data = await self.request(
            "/search/repositories",
            params={
                "q": query,
                "sort": options.sort,
                "order": "desc",
                "per_page": 50,
                "page": page,
            },
        )
        return list(data.get("items") or [])

    async def repository(self, url: str) -> dict[str, Any]:
        owner_repo = repository_url(url).removeprefix("https://github.com/")
        return await self.request(f"/repos/{owner_repo}")

    async def documents(
        self, repo: dict[str, Any]
    ) -> tuple[list[dict[str, str]], list[DocumentationSource]]:
        owner_repo = repository_url(str(repo["html_url"])).removeprefix("https://github.com/")
        prefix = f"/repos/{owner_repo}"
        commit = await self.request(
            f"{prefix}/commits/{quote(str(repo['default_branch']), safe='')}"
        )
        sha = str(commit["sha"])
        readme = await self.optional(f"{prefix}/readme", params={"ref": sha})
        files = [readme] if readme else []
        tree = await self.optional(f"{prefix}/git/trees/{sha}", params={"recursive": 1})
        paths = [
            str(item.get("path") or "")
            for item in (tree or {}).get("tree", [])
            if item.get("type") == "blob" and int(item.get("size") or 0) <= 100_000
        ]
        selected = sorted(
            path
            for path in paths
            if path.lower().endswith(".md")
            and any(word in path.lower() for word in ("install", "setup", "dependenc"))
        )[:3]
        for path in selected:
            file = await self.optional(
                f"{prefix}/contents/{quote(path, safe='/')}", params={"ref": sha}
            )
            if file:
                files.append(file)
        docs, sources = [], []
        for file in files:
            if file.get("encoding") != "base64":
                continue
            try:
                content = base64.b64decode(str(file.get("content") or "")).decode(
                    "utf-8", errors="replace"
                )
            except ValueError:
                continue
            path = str(file.get("path") or "README.md")
            docs.append({"path": path, "text": content[:16000]})
            sources.append(DocumentationSource(path=path, commit=sha))
        return docs, sources

    async def release(self, url: str) -> dict[str, Any] | None:
        owner_repo = repository_url(url).removeprefix("https://github.com/")
        return await self.optional(f"/repos/{owner_repo}/releases/latest")
