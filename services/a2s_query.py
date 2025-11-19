"""
A2S Query Service for CS2 Servers
Implements Source Engine A2S query protocol with CS2 challenge support
Reference: https://developer.valvesoftware.com/wiki/Server_queries
"""

import a2s
import asyncio
from typing import Optional, Dict, Tuple
import logging

logger = logging.getLogger(__name__)


class A2SQueryService:
    """Service for querying CS2 servers using A2S protocol"""
    
    @staticmethod
    async def query_server_info(host: str, port: int, timeout: float = 5.0) -> Tuple[bool, Optional[Dict]]:
        """
        Query server information using A2S_INFO
        
        Args:
            host: Server host/IP address
            port: Server query port (usually game port)
            timeout: Query timeout in seconds
            
        Returns:
            Tuple[bool, Optional[Dict]]: (success, server_info_dict or None)
        """
        try:
            # Run the synchronous a2s query in a thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            address = (host, port)
            
            # Query server info
            info = await loop.run_in_executor(
                None,
                lambda: a2s.info(address, timeout=timeout)
            )
            
            if info:
                # Convert the info object to a dictionary
                server_info = {
                    'server_name': info.server_name,
                    'map_name': info.map_name,
                    'folder': info.folder,
                    'game': info.game,
                    'player_count': info.player_count,
                    'max_players': info.max_players,
                    'bot_count': info.bot_count,
                    'server_type': info.server_type,
                    'platform': info.platform,
                    'password_protected': info.password_protected,
                    'vac_enabled': info.vac_enabled,
                    'version': info.version,
                    'ping': getattr(info, 'ping', None),
                }
                
                # Add optional fields if they exist
                if hasattr(info, 'keywords'):
                    server_info['keywords'] = info.keywords
                if hasattr(info, 'game_id'):
                    server_info['game_id'] = info.game_id
                    
                logger.debug(f"A2S query successful for {host}:{port} - {info.server_name}")
                return True, server_info
            else:
                logger.warning(f"A2S query returned no info for {host}:{port}")
                return False, None
                
        except asyncio.TimeoutError:
            logger.warning(f"A2S query timeout for {host}:{port}")
            return False, None
        except Exception as e:
            logger.error(f"A2S query error for {host}:{port}: {str(e)}")
            return False, None
    
    @staticmethod
    async def query_players(host: str, port: int, timeout: float = 5.0) -> Tuple[bool, Optional[list]]:
        """
        Query player information using A2S_PLAYER
        
        Args:
            host: Server host/IP address
            port: Server query port
            timeout: Query timeout in seconds
            
        Returns:
            Tuple[bool, Optional[list]]: (success, player_list or None)
        """
        try:
            loop = asyncio.get_event_loop()
            address = (host, port)
            
            # Query player info
            players = await loop.run_in_executor(
                None,
                lambda: a2s.players(address, timeout=timeout)
            )
            
            if players is not None:
                player_list = []
                for player in players:
                    player_list.append({
                        'name': player.name,
                        'score': player.score,
                        'duration': player.duration,
                    })
                
                logger.debug(f"A2S player query successful for {host}:{port} - {len(player_list)} players")
                return True, player_list
            else:
                return True, []
                
        except asyncio.TimeoutError:
            logger.warning(f"A2S player query timeout for {host}:{port}")
            return False, None
        except Exception as e:
            logger.error(f"A2S player query error for {host}:{port}: {str(e)}")
            return False, None
    
    @staticmethod
    async def query_rules(host: str, port: int, timeout: float = 5.0) -> Tuple[bool, Optional[Dict]]:
        """
        Query server rules/cvars using A2S_RULES
        
        Args:
            host: Server host/IP address
            port: Server query port
            timeout: Query timeout in seconds
            
        Returns:
            Tuple[bool, Optional[Dict]]: (success, rules_dict or None)
        """
        try:
            loop = asyncio.get_event_loop()
            address = (host, port)
            
            # Query rules
            rules = await loop.run_in_executor(
                None,
                lambda: a2s.rules(address, timeout=timeout)
            )
            
            if rules:
                logger.debug(f"A2S rules query successful for {host}:{port} - {len(rules)} rules")
                return True, dict(rules)
            else:
                return True, {}
                
        except asyncio.TimeoutError:
            logger.warning(f"A2S rules query timeout for {host}:{port}")
            return False, None
        except Exception as e:
            logger.error(f"A2S rules query error for {host}:{port}: {str(e)}")
            return False, None
    
    @staticmethod
    async def check_server_health(host: str, port: int, timeout: float = 5.0) -> bool:
        """
        Simple health check - just verify the server responds to A2S_INFO
        
        Args:
            host: Server host/IP address
            port: Server query port
            timeout: Query timeout in seconds
            
        Returns:
            bool: True if server is responsive, False otherwise
        """
        success, _ = await A2SQueryService.query_server_info(host, port, timeout)
        return success


# Global instance
a2s_service = A2SQueryService()
