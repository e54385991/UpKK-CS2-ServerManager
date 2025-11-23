"""
System Info Helper Service
Provides cached system-level information for servers (disk space, CPU, memory, etc.)
Separate from A2S protocol which is for game server queries
"""
import logging
from typing import Optional, Dict

from services.disk_space_service import disk_space_service
from modules.models import Server

logger = logging.getLogger(__name__)


class SystemInfoHelper:
    """Helper service to get system-level information for servers"""
    
    def __init__(self):
        pass
    
    async def get_system_info(self, server: Server, force_refresh: bool = False) -> Dict:
        """
        Get comprehensive system information for a server
        
        Args:
            server: Server instance
            force_refresh: If True, bypass cache and fetch fresh data
            
        Returns:
            Dict containing system information
        """
        system_info = {
            "server_id": server.id,
            "disk_space": None,
            "success": False
        }
        
        # Get disk space
        disk_success, disk_data = await disk_space_service.get_disk_space(server, force_refresh)
        if disk_success and disk_data:
            system_info["disk_space"] = disk_data
            system_info["success"] = True
        
        return system_info
    
    async def get_disk_space(self, server: Server, force_refresh: bool = False) -> Optional[Dict]:
        """
        Get disk space information for a server
        
        Args:
            server: Server instance
            force_refresh: If True, bypass cache and fetch fresh data
            
        Returns:
            Dict with disk space info or None if failed
        """
        success, disk_data = await disk_space_service.get_disk_space(server, force_refresh)
        return disk_data if success else None
    
    async def get_all_servers_disk_space(self, servers: list, force_refresh: bool = False) -> Dict[int, Optional[Dict]]:
        """
        Get disk space for multiple servers
        
        Args:
            servers: List of Server instances
            force_refresh: If True, bypass cache for all servers
            
        Returns:
            Dict mapping server ID to disk space info
        """
        result = {}
        
        for server in servers:
            disk_data = await self.get_disk_space(server, force_refresh)
            result[server.id] = disk_data
        
        return result


# Global instance
system_info_helper = SystemInfoHelper()
