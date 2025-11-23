"""
Disk Space Cache Service
Provides cached disk space information for server directories
"""
import logging
import re
import shlex
from typing import Optional, Dict, Tuple

from services.redis_manager import redis_manager
from services.ssh_manager import SSHManager
from modules.models import Server

logger = logging.getLogger(__name__)


class DiskSpaceService:
    """Service to read and cache disk space for server directories"""
    
    # Cache TTL: 6 hours
    CACHE_TTL_SECONDS = 6 * 60 * 60
    
    def __init__(self):
        pass
        
    async def get_disk_space(self, server: Server, force_refresh: bool = False) -> Tuple[bool, Optional[Dict]]:
        """
        Get disk space information for server directory
        
        Args:
            server: Server instance
            force_refresh: If True, bypass cache and read from system
            
        Returns:
            Tuple[bool, Optional[Dict]]: (success, disk_info)
            disk_info format: {
                "used_gb": float,      # GB used by server directory
                "total_gb": float,     # Total GB on filesystem
                "available_gb": float, # GB available
                "used_percent": float  # Percentage used (0-100)
            }
        """
        cache_key = f"disk_space:{server.id}"
        
        # Try cache first unless force_refresh
        if not force_refresh:
            cached_info = await redis_manager.get(cache_key)
            if cached_info and isinstance(cached_info, dict):
                logger.debug(f"Using cached disk space for server {server.id}")
                return True, cached_info
        
        # Read from system
        success, disk_info = await self._read_disk_space(server)
        
        if success and disk_info:
            # Cache the info
            await redis_manager.set(cache_key, disk_info, expire=self.CACHE_TTL_SECONDS)
            logger.info(f"Cached disk space for server {server.id}: {disk_info.get('used_gb', 0):.2f}GB used of {disk_info.get('total_gb', 0):.2f}GB")
            return True, disk_info
        
        return False, None
    
    async def _read_disk_space(self, server: Server) -> Tuple[bool, Optional[Dict]]:
        """
        Read disk space from system via SSH
        
        Args:
            server: Server instance
            
        Returns:
            Tuple[bool, Optional[Dict]]: (success, disk_info)
        """
        ssh_manager = SSHManager()
        
        try:
            # Connect to server
            success, msg = await ssh_manager.connect(server)
            if not success:
                logger.warning(f"Failed to connect to server {server.id} for disk space read: {msg}")
                return False, None
            
            # Get size of server directory
            # Use du -s (summary) for faster performance on large directories
            # Properly escape the path to prevent command injection
            escaped_path = shlex.quote(server.game_directory)
            du_cmd = f"du -sb {escaped_path} 2>/dev/null | awk '{{print $1}}' || echo '0'"
            success, stdout, stderr = await ssh_manager.execute_command(du_cmd, timeout=60)
            
            if not success:
                logger.warning(f"Failed to get directory size for server {server.id}")
                return False, None
            
            try:
                used_bytes = int(stdout.strip() or '0')
                used_gb = used_bytes / (1024 ** 3)  # Convert bytes to GB
            except (ValueError, TypeError):
                logger.warning(f"Invalid directory size output for server {server.id}: {stdout}")
                return False, None
            
            # Get filesystem info using df
            # Use -BG to get sizes in GB
            df_cmd = f"df -BG {escaped_path} | tail -1"
            success, stdout, stderr = await ssh_manager.execute_command(df_cmd)
            
            if not success or not stdout:
                logger.warning(f"Failed to get filesystem info for server {server.id}")
                return False, None
            
            # Parse df output
            # Format: Filesystem 1G-blocks Used Available Use% Mounted
            # Example: /dev/sda1      100G   50G      50G  50% /home
            disk_info = self._parse_df_output(stdout, used_gb)
            
            if disk_info:
                return True, disk_info
            else:
                logger.warning(f"Could not parse df output for server {server.id}: {stdout}")
                return False, None
                
        except Exception as e:
            logger.error(f"Error reading disk space for server {server.id}: {e}")
            return False, None
        finally:
            await ssh_manager.disconnect()
    
    def _parse_df_output(self, output: str, used_gb: float) -> Optional[Dict]:
        """
        Parse df command output
        
        Args:
            output: Output from df command
            used_gb: Used space in GB from du command
            
        Returns:
            Dict with disk space info or None
        """
        try:
            # Split by whitespace
            parts = output.split()
            
            if len(parts) < 5:
                return None
            
            # Extract total and available (removing 'G' suffix)
            total_str = parts[1].rstrip('G')
            available_str = parts[3].rstrip('G')
            
            total_gb = float(total_str)
            available_gb = float(available_str)
            
            # Calculate percentage
            if total_gb > 0:
                used_percent = (used_gb / total_gb) * 100
            else:
                used_percent = 0.0
            
            return {
                "used_gb": round(used_gb, 2),
                "total_gb": round(total_gb, 2),
                "available_gb": round(available_gb, 2),
                "used_percent": round(used_percent, 2)
            }
            
        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing df output: {e}")
            return None
    
    async def clear_disk_space_cache(self, server_id: int):
        """
        Clear cached disk space for a server
        
        Args:
            server_id: Server ID
        """
        cache_key = f"disk_space:{server_id}"
        await redis_manager.delete(cache_key)
        logger.debug(f"Cleared disk space cache for server {server_id}")


# Global instance
disk_space_service = DiskSpaceService()
