"""
Steam API Service for CS2 Version Checking and Game Server Account Management
Implements version checking against Steam API for automatic updates
and game server login token (GSLT) generation
"""
import logging
from typing import Optional, Tuple, Dict
from datetime import datetime, timedelta
from modules.utils import get_current_time
from modules.http_helper import http_helper

logger = logging.getLogger(__name__)


class SteamAPIService:
    """Service for checking CS2 version against Steam API and managing game server accounts"""
    
    # CS2 App ID on Steam
    CS2_APP_ID = 730
    
    # Steam API endpoint for version checking
    VERSION_CHECK_URL = "https://api.steampowered.com/ISteamApps/UpToDateCheck/v0001/"
    
    # Steam API endpoint for creating game server account
    CREATE_ACCOUNT_URL = "https://api.steampowered.com/IGameServersService/CreateAccount/v1/"
    
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
            
            # Make async HTTP request using http_helper
            success, data, error_msg = await http_helper.get(
                url=SteamAPIService.VERSION_CHECK_URL,
                params=params,
                timeout=10
            )
            
            if not success:
                logger.error(f"Steam API request failed: {error_msg}")
                return False, {
                    'success': False,
                    'error': error_msg or 'Failed to connect to Steam API'
                }
            
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
            
            # Validate data is a dictionary and has expected structure
            if not isinstance(data, dict) or 'response' not in data:
                logger.error(f"Unexpected Steam API response format: {data}")
                return False, {
                    'success': False,
                    'error': 'Unexpected API response format'
                }
            
            api_response = data['response']
            if not isinstance(api_response, dict):
                logger.error(f"Invalid response structure: {api_response}")
                return False, {
                    'success': False,
                    'error': 'Invalid API response structure'
                }
            
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
        now = get_current_time()
        
        # Make last_check timezone-aware if it's naive
        # Note: This assumes naive timestamps from the database were recorded in local timezone
        # If database contains timestamps from different environments, they should be migrated
        # to timezone-aware format
        if last_check.tzinfo is None:
            # If last_check is naive, assume it was in the local timezone
            # and convert it to timezone-aware
            last_check = last_check.astimezone()
        
        time_since_check = (now - last_check).total_seconds()
        
        # Check based on configured interval
        interval_seconds = interval_hours * 3600
        return time_since_check >= interval_seconds
    
    @staticmethod
    async def create_game_server_account(steam_api_key: str, memo: str = "") -> Tuple[bool, Optional[Dict]]:
        """
        Create a new game server account (GSLT) using Steam API
        
        Args:
            steam_api_key: User's Steam Web API key
            memo: Optional memo/description for the server account
            
        Returns:
            Tuple[bool, Optional[Dict]]: (success, result_dict or None)
            result_dict contains:
                - success: bool (API call successful)
                - login_token: str (the generated GSLT token)
                - steamid: str (Steam ID of the game server account)
                - error: str (error message if failed)
        """
        try:
            logger.debug(f"Creating game server account for CS2 (appid={SteamAPIService.CS2_APP_ID})")
            
            # Prepare request parameters
            params = {
                'key': steam_api_key,
                'appid': SteamAPIService.CS2_APP_ID,
                'memo': memo or 'CS2 Server'
            }
            
            # Make HTTP request using the helper
            success, response_data, error_msg = await http_helper.post(
                url=SteamAPIService.CREATE_ACCOUNT_URL,
                params=params,
                timeout=15
            )
            
            if not success:
                logger.error(f"Failed to create game server account: {error_msg}")
                return False, {
                    'success': False,
                    'error': error_msg or 'Failed to create game server account'
                }
            
            # Parse response
            # Expected format:
            # {
            #   "response": {
            #     "steamid": "...",
            #     "login_token": "..."
            #   }
            # }
            
            if 'response' not in response_data:
                logger.error(f"Unexpected Steam API response format: {response_data}")
                return False, {
                    'success': False,
                    'error': 'Unexpected API response format'
                }
            
            api_response = response_data['response']
            
            # Check if login_token exists in response
            if 'login_token' not in api_response:
                error_detail = api_response.get('error', 'Unknown error')
                logger.error(f"Steam API did not return login_token: {error_detail}")
                return False, {
                    'success': False,
                    'error': f'Failed to generate token: {error_detail}'
                }
            
            result = {
                'success': True,
                'login_token': api_response['login_token'],
                'steamid': api_response.get('steamid', ''),
                'raw_response': api_response
            }
            
            logger.info(f"Successfully created game server account")
            
            return True, result
            
        except Exception as e:
            logger.error(f"Unexpected error creating game server account: {str(e)}")
            return False, {
                'success': False,
                'error': f'Unexpected error: {str(e)}'
            }


# Global instance
steam_api_service = SteamAPIService()
