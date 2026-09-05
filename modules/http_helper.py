"""
HTTP Helper module for common HTTP request handling
Provides a centralized utility for making HTTP requests with error handling
"""

import asyncio
import inspect
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import anyio
import httpx

logger = logging.getLogger(__name__)

# GitHub URL patterns for proxy detection
# Note: GitHub proxy services like ghfast.top only work for file downloads,
# NOT for API requests. API requests should go directly to api.github.com
GITHUB_API_PREFIX = "https://api.github.com/"
GITHUB_PREFIX = "https://github.com/"
GITHUB_DOWNLOAD_PATTERN = "/releases/download/"  # Pattern for release downloads

# Download chunk size for streaming downloads (8KB)
DOWNLOAD_CHUNK_SIZE = 8192

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # Initial delay in seconds


async def _emit_transfer_progress(
    callback,
    *,
    started_at: float,
    last_event_at: float,
    bytes_transferred: int,
    total_bytes: int,
    retry_count: int,
    force: bool = False,
) -> float:
    """Throttle structured transfer events while preserving terminal updates."""
    if callback is None:
        return last_event_at
    now = time.monotonic()
    if not force and now - last_event_at < 1.0:
        return last_event_at
    payload = {
        "phase": "download",
        "bytes_transferred": bytes_transferred,
        "total_bytes": total_bytes or None,
        "percent": (round(bytes_transferred * 100 / total_bytes, 1) if total_bytes > 0 else None),
        "elapsed_seconds": round(now - started_at, 1),
        "retry_count": retry_count,
    }
    result = callback(payload)
    if inspect.isawaitable(result):
        await result
    return now


async def _emit_retry_progress(
    callback,
    *,
    started_at: float,
    last_event_at: float,
    retry_count: int,
) -> float:
    return await _emit_transfer_progress(
        callback,
        started_at=started_at,
        last_event_at=last_event_at,
        bytes_transferred=0,
        total_bytes=0,
        retry_count=retry_count,
        force=True,
    )


class HTTPHelper:
    """Helper class for making HTTP requests with common error handling"""

    def __init__(self):
        """Initialize HTTP helper with connection pooling"""
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx client with connection pooling"""
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._client_lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0),
                    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
                    follow_redirects=True,
                )
            return self._client

    async def close(self):
        """Close the HTTP client"""
        async with self._client_lock:
            client, self._client = self._client, None
        if client is not None and not client.is_closed:
            await client.aclose()

    async def make_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: int = 10,
        proxy: Optional[str] = None,
        github_token: Optional[str] = None,
        retries: Optional[int] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Make an HTTP request with error handling, retry logic, and connection pooling

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            headers: Optional HTTP headers
            params: Optional query parameters
            data: Optional form data
            json: Optional JSON data
            timeout: Request timeout in seconds (default: 10)
            proxy: Optional proxy URL to use for this request
            github_token: Optional GitHub personal access token for authentication
            retries: Attempt count. Defaults to MAX_RETRIES. Use 1 on interactive paths.

        Returns:
            Tuple[bool, Optional[Dict], Optional[str]]:
                - success: Whether the request was successful
                - response_data: Response JSON data if successful
                - error_message: Error message if failed
        """
        last_error = None
        attempts = MAX_RETRIES if retries is None else max(1, int(retries))

        for attempt in range(attempts):
            try:
                if attempt > 0:
                    delay = RETRY_DELAY * (2 ** (attempt - 1))  # Exponential backoff
                    logger.info(f"Retry attempt {attempt + 1}/{attempts} after {delay}s delay...")
                    await asyncio.sleep(delay)

                # Add GitHub token to headers if provided and URL is a GitHub API request
                request_headers = headers.copy() if headers else {}
                if github_token and github_token.strip() and url.startswith(GITHUB_API_PREFIX):
                    request_headers["Authorization"] = f"Bearer {github_token.strip()}"
                    logger.debug("Added GitHub token to request headers for authentication")

                # Apply proxy to URL if provided
                # IMPORTANT: GitHub proxy services like ghfast.top only work for file downloads,
                # NOT for API requests (api.github.com). Only proxy actual file downloads.
                request_url = url
                if proxy and proxy.strip():
                    proxy_base = proxy.strip().rstrip("/")
                    # Only proxy GitHub file downloads, not API requests
                    # Proxy services don't support API endpoints
                    if url.startswith(GITHUB_PREFIX) and GITHUB_DOWNLOAD_PATTERN in url:
                        request_url = f"{proxy_base}/{url}"
                        logger.debug(f"Using GitHub proxy for download: {proxy_base}")
                    elif url.startswith(GITHUB_API_PREFIX):
                        logger.debug(
                            "Skipping proxy for GitHub API request (proxy only works for downloads)"
                        )

                logger.debug(
                    f"Making {method} request to {request_url} (attempt {attempt + 1}/{attempts})"
                )

                client = await self._get_client()
                response = await client.request(
                    method=method,
                    url=request_url,
                    headers=request_headers,
                    params=params,
                    data=data,
                    json=json,
                    timeout=timeout,
                    follow_redirects=True,  # Enable redirect following
                )

                # Check if response is successful
                if response.status_code >= 200 and response.status_code < 300:
                    try:
                        response_data = response.json()
                        logger.debug(f"Request successful: {response.status_code}")
                        return True, response_data, None
                    except Exception as e:
                        # If JSON parsing fails, return the text response
                        logger.warning(f"Failed to parse JSON response: {e}")
                        return True, {"text": response.text}, None
                else:
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                    logger.error(f"Request failed: {error_msg}")
                    last_error = error_msg
                    # Don't retry on 4xx errors (client errors)
                    if 400 <= response.status_code < 500:
                        return False, None, error_msg
                    # Retry on 5xx errors (server errors)
                    continue

            except httpx.TimeoutException as e:
                error_msg = f"Request timeout: {str(e)}"
                logger.error(error_msg)
                last_error = error_msg
                # Retry on timeout
                continue

            except httpx.RequestError as e:
                error_msg = f"Request error: {str(e)}"
                logger.error(error_msg)
                last_error = error_msg
                # Retry on network errors
                continue

            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                logger.error(error_msg)
                last_error = error_msg
                # Retry on unexpected errors
                continue

        # All retries failed
        final_error = f"Request failed after {attempts} attempts. Last error: {last_error}"
        logger.error(final_error)
        return False, None, final_error

    async def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 10,
        proxy: Optional[str] = None,
        github_token: Optional[str] = None,
        retries: Optional[int] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Make a GET request

        Args:
            url: Request URL
            headers: Optional HTTP headers
            params: Optional query parameters
            timeout: Request timeout in seconds
            proxy: Optional proxy URL to use for this request
            github_token: Optional GitHub personal access token for authentication
            retries: Attempt count. Defaults to MAX_RETRIES.

        Returns:
            Tuple[bool, Optional[Dict], Optional[str]]: (success, response_data, error_message)
        """
        return await self.make_request(
            "GET",
            url,
            headers=headers,
            params=params,
            timeout=timeout,
            proxy=proxy,
            github_token=github_token,
            retries=retries,
        )

    async def post(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: int = 10,
        proxy: Optional[str] = None,
        github_token: Optional[str] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Make a POST request

        Args:
            url: Request URL
            headers: Optional HTTP headers
            params: Optional query parameters
            data: Optional form data
            json: Optional JSON data
            timeout: Request timeout in seconds
            proxy: Optional proxy URL to use for this request
            github_token: Optional GitHub personal access token for authentication

        Returns:
            Tuple[bool, Optional[Dict], Optional[str]]: (success, response_data, error_message)
        """
        return await self.make_request(
            "POST",
            url,
            headers=headers,
            params=params,
            data=data,
            json=json,
            timeout=timeout,
            proxy=proxy,
            github_token=github_token,
        )

    async def download_file(
        self,
        url: str,
        local_path: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 300,
        progress_callback=None,
        progress_event_callback=None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Download a file with progress tracking and retry logic

        Args:
            url: Download URL
            local_path: Local file path to save to
            headers: Optional HTTP headers
            timeout: Request timeout in seconds (default: 300 for large files)
            progress_callback: Optional async callback function for progress updates
                             Called with (bytes_downloaded, total_bytes)
            progress_event_callback: Optional callback receiving structured
                                     download progress metadata

        Returns:
            Tuple[bool, Optional[str]]: (success, error_message)
        """
        last_error = None
        progress_event_callback = progress_event_callback or getattr(
            progress_callback, "progress_event_callback", None
        )
        started_at = time.monotonic()
        deadline = started_at + max(1, timeout)
        last_event_at = 0.0

        for attempt in range(MAX_RETRIES):
            try:
                if attempt > 0:
                    delay = RETRY_DELAY * (2 ** (attempt - 1))  # Exponential backoff
                    logger.info(
                        f"Retry attempt {attempt + 1}/{MAX_RETRIES} after {delay}s delay..."
                    )
                    await asyncio.sleep(delay)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    last_error = "Download timeout: download deadline exceeded"
                    last_event_at = await _emit_retry_progress(
                        progress_event_callback,
                        started_at=started_at,
                        last_event_at=last_event_at,
                        retry_count=attempt,
                    )
                    break

                last_event_at = await _emit_transfer_progress(
                    progress_event_callback,
                    started_at=started_at,
                    last_event_at=last_event_at,
                    bytes_transferred=0,
                    total_bytes=0,
                    retry_count=attempt,
                    force=True,
                )

                logger.debug(
                    f"Downloading file from {url} to {local_path} (attempt {attempt + 1}/{MAX_RETRIES})"
                )

                client = await self._get_client()

                request_timeout = httpx.Timeout(
                    min(30.0, remaining),
                    connect=min(15.0, remaining),
                    read=min(30.0, remaining),
                )
                async with asyncio.timeout(remaining):
                    async with client.stream(
                        "GET", url, headers=headers, timeout=request_timeout, follow_redirects=True
                    ) as response:
                        if response.status_code >= 200 and response.status_code < 300:
                            try:
                                total_bytes = int(response.headers.get("Content-Length", 0))
                            except TypeError, ValueError:
                                total_bytes = 0
                            bytes_downloaded = 0
                            parent_directory = os.path.dirname(local_path)
                            if parent_directory:
                                await asyncio.to_thread(
                                    os.makedirs, parent_directory, exist_ok=True
                                )
                            async with await anyio.open_file(local_path, "wb") as f:
                                async for chunk in response.aiter_bytes(
                                    chunk_size=DOWNLOAD_CHUNK_SIZE
                                ):
                                    await f.write(chunk)
                                    bytes_downloaded += len(chunk)
                                    if progress_callback:
                                        if inspect.iscoroutinefunction(progress_callback):
                                            await progress_callback(bytes_downloaded, total_bytes)
                                        else:
                                            progress_callback(bytes_downloaded, total_bytes)
                                    last_event_at = await _emit_transfer_progress(
                                        progress_event_callback,
                                        started_at=started_at,
                                        last_event_at=last_event_at,
                                        bytes_transferred=bytes_downloaded,
                                        total_bytes=total_bytes,
                                        retry_count=attempt,
                                    )
                            logger.debug(f"Download successful: {bytes_downloaded} bytes")
                            last_event_at = await _emit_transfer_progress(
                                progress_event_callback,
                                started_at=started_at,
                                last_event_at=last_event_at,
                                bytes_transferred=bytes_downloaded,
                                total_bytes=total_bytes,
                                retry_count=attempt,
                                force=True,
                            )
                            return True, None
                        error_body = await response.aread()
                        error_text = error_body.decode("utf-8", errors="ignore")[:500]
                        error_msg = f"HTTP {response.status_code}: {error_text}"
                        logger.error(f"Download failed: {error_msg}")
                        last_error = error_msg
                        if 400 <= response.status_code < 500:
                            return False, error_msg
                        last_event_at = await _emit_retry_progress(
                            progress_event_callback,
                            started_at=started_at,
                            last_event_at=last_event_at,
                            retry_count=attempt + 1,
                        )
                        continue

            except (httpx.TimeoutException, TimeoutError, asyncio.TimeoutError) as e:
                error_msg = f"Download timeout: {str(e)}"
                logger.error(error_msg)
                last_error = error_msg
                last_event_at = await _emit_retry_progress(
                    progress_event_callback,
                    started_at=started_at,
                    last_event_at=last_event_at,
                    retry_count=attempt + 1,
                )
                # Retry on timeout
                continue

            except httpx.RequestError as e:
                error_msg = f"Download error: {str(e)}"
                logger.error(error_msg)
                last_error = error_msg
                last_event_at = await _emit_retry_progress(
                    progress_event_callback,
                    started_at=started_at,
                    last_event_at=last_event_at,
                    retry_count=attempt + 1,
                )
                # Retry on network errors
                continue

            except Exception as e:
                error_msg = f"Unexpected download error: {str(e)}"
                logger.error(error_msg)
                last_error = error_msg
                last_event_at = await _emit_retry_progress(
                    progress_event_callback,
                    started_at=started_at,
                    last_event_at=last_event_at,
                    retry_count=attempt + 1,
                )
                # Retry on unexpected errors
                continue

        # All retries failed
        final_error = f"Download failed after {MAX_RETRIES} attempts. Last error: {last_error}"
        logger.error(final_error)
        return False, final_error


# Global instance
http_helper = HTTPHelper()
