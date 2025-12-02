"""
HTTP Helper module for common HTTP request handling
Provides a centralized utility for making HTTP requests with error handling
"""
import httpx
import logging
import os
import asyncio
from typing import Optional, Dict, Any, Tuple

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


class HTTPHelper:
    """Helper class for making HTTP requests with common error handling"""
    
    def __init__(self):
        """Initialize HTTP helper with connection pooling"""
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx client with connection pooling"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                follow_redirects=True  # Enable automatic redirect following
            )
        return self._client
    
    async def close(self):
        """Close the HTTP client"""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def make_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: int = 10,
        proxy: Optional[str] = None
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
            
        Returns:
            Tuple[bool, Optional[Dict], Optional[str]]:
                - success: Whether the request was successful
                - response_data: Response JSON data if successful
                - error_message: Error message if failed
        """
        last_error = None
        
        for attempt in range(MAX_RETRIES):
            try:
                if attempt > 0:
                    delay = RETRY_DELAY * (2 ** (attempt - 1))  # Exponential backoff
                    logger.info(f"Retry attempt {attempt + 1}/{MAX_RETRIES} after {delay}s delay...")
                    await asyncio.sleep(delay)
                
                # Apply proxy to URL if provided
                # IMPORTANT: GitHub proxy services like ghfast.top only work for file downloads,
                # NOT for API requests (api.github.com). Only proxy actual file downloads.
                request_url = url
                if proxy and proxy.strip():
                    proxy_base = proxy.strip().rstrip('/')
                    # Only proxy GitHub file downloads, not API requests
                    # Proxy services don't support API endpoints
                    if url.startswith(GITHUB_PREFIX) and GITHUB_DOWNLOAD_PATTERN in url:
                        request_url = f"{proxy_base}/{url}"
                        logger.debug(f"Using GitHub proxy for download: {proxy_base}")
                    elif url.startswith(GITHUB_API_PREFIX):
                        logger.debug(f"Skipping proxy for GitHub API request (proxy only works for downloads)")
                
                logger.debug(f"Making {method} request to {request_url} (attempt {attempt + 1}/{MAX_RETRIES})")
                
                client = await self._get_client()
                response = await client.request(
                    method=method,
                    url=request_url,
                    headers=headers,
                    params=params,
                    data=data,
                    json=json,
                    timeout=timeout,
                    follow_redirects=True  # Enable redirect following
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
        final_error = f"Request failed after {MAX_RETRIES} attempts. Last error: {last_error}"
        logger.error(final_error)
        return False, None, final_error
    
    async def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 10,
        proxy: Optional[str] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Make a GET request
        
        Args:
            url: Request URL
            headers: Optional HTTP headers
            params: Optional query parameters
            timeout: Request timeout in seconds
            proxy: Optional proxy URL to use for this request
            
        Returns:
            Tuple[bool, Optional[Dict], Optional[str]]: (success, response_data, error_message)
        """
        return await self.make_request("GET", url, headers=headers, params=params, timeout=timeout, proxy=proxy)
    
    async def post(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: int = 10,
        proxy: Optional[str] = None
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
            
        Returns:
            Tuple[bool, Optional[Dict], Optional[str]]: (success, response_data, error_message)
        """
        return await self.make_request("POST", url, headers=headers, params=params, data=data, json=json, timeout=timeout, proxy=proxy)
    
    async def download_file(
        self,
        url: str,
        local_path: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 300,
        progress_callback=None
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
            
        Returns:
            Tuple[bool, Optional[str]]: (success, error_message)
        """
        last_error = None
        
        for attempt in range(MAX_RETRIES):
            try:
                if attempt > 0:
                    delay = RETRY_DELAY * (2 ** (attempt - 1))  # Exponential backoff
                    logger.info(f"Retry attempt {attempt + 1}/{MAX_RETRIES} after {delay}s delay...")
                    await asyncio.sleep(delay)
                
                logger.debug(f"Downloading file from {url} to {local_path} (attempt {attempt + 1}/{MAX_RETRIES})")
                
                client = await self._get_client()
                
                async with client.stream("GET", url, headers=headers, timeout=timeout, follow_redirects=True) as response:
                    if response.status_code >= 200 and response.status_code < 300:
                        # Get total file size if available
                        total_bytes = int(response.headers.get("Content-Length", 0))
                        bytes_downloaded = 0
                        
                        # Ensure parent directory exists
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
                        
                        # Download file in chunks
                        with open(local_path, "wb") as f:
                            async for chunk in response.aiter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
                                f.write(chunk)
                                bytes_downloaded += len(chunk)
                                
                                # Send progress update
                                if progress_callback:
                                    if asyncio.iscoroutinefunction(progress_callback):
                                        await progress_callback(bytes_downloaded, total_bytes)
                                    else:
                                        progress_callback(bytes_downloaded, total_bytes)
                        
                        logger.debug(f"Download successful: {bytes_downloaded} bytes")
                        return True, None
                    else:
                        # Read error response body for streaming response
                        error_body = await response.aread()
                        error_text = error_body.decode('utf-8', errors='ignore')[:500]  # Limit to 500 chars
                        error_msg = f"HTTP {response.status_code}: {error_text}"
                        logger.error(f"Download failed: {error_msg}")
                        last_error = error_msg
                        # Don't retry on 4xx errors (client errors)
                        if 400 <= response.status_code < 500:
                            return False, error_msg
                        # Retry on 5xx errors (server errors)
                        continue
                        
            except httpx.TimeoutException as e:
                error_msg = f"Download timeout: {str(e)}"
                logger.error(error_msg)
                last_error = error_msg
                # Retry on timeout
                continue
                
            except httpx.RequestError as e:
                error_msg = f"Download error: {str(e)}"
                logger.error(error_msg)
                last_error = error_msg
                # Retry on network errors
                continue
                
            except Exception as e:
                error_msg = f"Unexpected download error: {str(e)}"
                logger.error(error_msg)
                last_error = error_msg
                # Retry on unexpected errors
                continue
        
        # All retries failed
        final_error = f"Download failed after {MAX_RETRIES} attempts. Last error: {last_error}"
        logger.error(final_error)
        return False, final_error


# Global instance
http_helper = HTTPHelper()
