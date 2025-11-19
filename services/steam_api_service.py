"""
Steam API Service for CS2 Version Checking
Implements version checking against Steam API for automatic updates
"""
import aiohttp
import logging
from typing import Optional, Tuple, Dict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class SteamAPIService:
    """Service for checking CS2 version against Steam API"""
    
    # CS2 App ID on Steam
    CS2_APP_ID = 730
    
    # Steam API endpoint for version checking
    VERSION_CHECK_URL = "https://api.steampowered.com/ISteamApps/UpToDateCheck/v0001/"
    
    @staticmethod
    async def check_version(current_version: Optional[str] = None) -> Tuple[bool, Optional[Dict]]:
        """
        Check if a CS2 version is up-to-date using Steam API
        
        Args:
            current_version: The current installed version (e.g., "1.41.2.5")
                           If None or empty, defaults to "1" to get latest version info
            
        Returns:
            Tuple[bool, Optional[Dict]]: (success, result_dict or None)
            result_dict contains:
                - success: bool (API call successful)
                - up_to_date: bool (version is up-to-date)
                - required_version: str (required/latest version)
                - message: str (API message if any)
                - error: str (error message if failed)
        """
        try:
            # Use "1" as default version if not provided to get the latest version info
            version_to_check = current_version if current_version else "1"
            
            # Prepare request parameters
            params = {
                'appid': SteamAPIService.CS2_APP_ID,
                'version': version_to_check,
                'format': 'json'
            }
            
            logger.debug(f"Checking CS2 version against Steam API: {version_to_check}")
            
            # Make async HTTP request
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    SteamAPIService.VERSION_CHECK_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        logger.error(f"Steam API returned status {response.status}")
                        return False, {
                            'success': False,
                            'error': f'Steam API returned status {response.status}'
                        }
                    
                    data = await response.json()
                    
                    # Parse response
                    # Expected format:
                    # {
                    #   "response": {
                    #     "success": true,
                    #     "up_to_date": false,
                    #     "version_is_listable": false,
                    #     "required_version": 14125,
                    #     "message": "Server version required: 1.41.2.5"
                    #   }
                    # }
                    
                    if 'response' not in data:
                        logger.error(f"Unexpected Steam API response format: {data}")
                        return False, {
                            'success': False,
                            'error': 'Unexpected API response format'
                        }
                    
                    api_response = data['response']
                    
                    # Extract required version from message if available
                    required_version = None
                    message = api_response.get('message', '')
                    
                    # Try to extract version from message like "Server version required: 1.41.2.5"
                    if message and 'required:' in message.lower():
                        parts = message.split(':')
                        if len(parts) >= 2:
                            required_version = parts[-1].strip()
                    
                    # If not found in message, try to use required_version field
                    if not required_version and 'required_version' in api_response:
                        required_version = str(api_response['required_version'])
                    
                    result = {
                        'success': True,
                        'up_to_date': api_response.get('up_to_date', False),
                        'required_version': required_version,
                        'message': message,
                        'raw_response': api_response
                    }
                    
                    logger.info(
                        f"Steam API version check: "
                        f"current={version_to_check}, "
                        f"up_to_date={result['up_to_date']}, "
                        f"required={required_version}"
                    )
                    
                    return True, result
                    
        except aiohttp.ClientError as e:
            logger.error(f"Steam API network error: {str(e)}")
            return False, {
                'success': False,
                'error': f'Network error: {str(e)}'
            }
        except Exception as e:
            logger.error(f"Steam API unexpected error: {str(e)}")
            return False, {
                'success': False,
                'error': f'Unexpected error: {str(e)}'
            }
    
    @staticmethod
    def parse_version_from_a2s(a2s_version: Optional[str]) -> Optional[str]:
        """
        Parse and normalize version string from A2S query
        
        Args:
            a2s_version: Version string from A2S query (e.g., "1.41.2.5/14125")
            
        Returns:
            Normalized version string (e.g., "1.41.2.5") or None
        """
        if not a2s_version:
            return None
        
        # A2S version can be in format "1.41.2.5/14125" or just "1.41.2.5"
        # Extract the dotted version part
        if '/' in a2s_version:
            version = a2s_version.split('/')[0].strip()
        else:
            version = a2s_version.strip()
        
        return version if version else None
    
    @staticmethod
    def should_check_version(last_check: Optional[datetime], interval_hours: int = 1) -> bool:
        """
        Determine if version should be checked based on last check time
        
        Args:
            last_check: Datetime of last version check
            interval_hours: Hours between checks (default: 1)
            
        Returns:
            True if version should be checked, False otherwise
        """
        if not last_check:
            return True
        
        # Calculate time since last check
        now = datetime.now(timezone.utc)
        
        # Make last_check timezone-aware if it's naive
        if last_check.tzinfo is None:
            last_check = last_check.replace(tzinfo=timezone.utc)
        
        time_since_check = (now - last_check).total_seconds()
        
        # Check based on configured interval
        interval_seconds = interval_hours * 3600
        return time_since_check >= interval_seconds


# Global instance
steam_api_service = SteamAPIService()
