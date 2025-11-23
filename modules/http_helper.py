"""
HTTP Helper module for common HTTP request handling
Provides a centralized utility for making HTTP requests with error handling
"""
import httpx
import logging
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)


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
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
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
        timeout: int = 10
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Make an HTTP request with error handling using connection pooling
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            headers: Optional HTTP headers
            params: Optional query parameters
            data: Optional form data
            json: Optional JSON data
            timeout: Request timeout in seconds (default: 10)
            
        Returns:
            Tuple[bool, Optional[Dict], Optional[str]]:
                - success: Whether the request was successful
                - response_data: Response JSON data if successful
                - error_message: Error message if failed
        """
        try:
            logger.debug(f"Making {method} request to {url}")
            
            client = await self._get_client()
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                data=data,
                json=json,
                timeout=timeout
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
                return False, None, error_msg
                
        except httpx.TimeoutException as e:
            error_msg = f"Request timeout: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg
            
        except httpx.RequestError as e:
            error_msg = f"Request error: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg
            
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg
    
    async def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 10
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Make a GET request
        
        Args:
            url: Request URL
            headers: Optional HTTP headers
            params: Optional query parameters
            timeout: Request timeout in seconds
            
        Returns:
            Tuple[bool, Optional[Dict], Optional[str]]: (success, response_data, error_message)
        """
        return await self.make_request("GET", url, headers=headers, params=params, timeout=timeout)
    
    async def post(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: int = 10
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
            
        Returns:
            Tuple[bool, Optional[Dict], Optional[str]]: (success, response_data, error_message)
        """
        return await self.make_request("POST", url, headers=headers, params=params, data=data, json=json, timeout=timeout)


# Global instance
http_helper = HTTPHelper()
