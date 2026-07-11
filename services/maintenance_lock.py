"""Shared in-process maintenance locks for operations that overwrite server files."""
import asyncio
from typing import Dict


class MaintenanceLockService:
    def __init__(self) -> None:
        self._locks: Dict[int, asyncio.Lock] = {}

    def get(self, server_id: int) -> asyncio.Lock:
        lock = self._locks.get(server_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[server_id] = lock
        return lock


maintenance_lock_service = MaintenanceLockService()
