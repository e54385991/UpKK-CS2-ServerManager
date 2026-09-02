"""Value objects used by the SSH connection pool."""

import time
from typing import List

import asyncssh

from modules.models import AuthType


class ConnectionKey:
    """Unique key for identifying SSH connections"""

    def __init__(self, host: str, port: int, user: str, auth_type: AuthType):
        self.host = host
        self.port = port
        self.user = user
        self.auth_type = auth_type

    def __hash__(self):
        return hash((self.host, self.port, self.user, self.auth_type))

    def __eq__(self, other):
        if not isinstance(other, ConnectionKey):
            return False
        return (
            self.host == other.host
            and self.port == other.port
            and self.user == other.user
            and self.auth_type == other.auth_type
        )

    def __repr__(self):
        return f"ConnectionKey({self.user}@{self.host}:{self.port}, {self.auth_type})"


class PooledConnection:
    """Wrapper for a pooled SSH connection"""

    def __init__(self, conn: asyncssh.SSHClientConnection, key: ConnectionKey):
        self.conn = conn
        self.key = key
        self.created_at = time.time()
        self.last_used = time.time()
        self.in_use_count = 0
        self.draining = False
        self.reconnection_attempts: List[float] = []  # Timestamps of reconnection attempts

    def is_alive(self) -> bool:
        """Check if connection is still alive"""
        return self.conn is not None and not self.conn.is_closed()

    def mark_used(self):
        """Mark connection as used"""
        self.last_used = time.time()

    def acquire(self):
        """
        Mark connection as in-use

        Note: This is a synchronous method (not async) because it only updates
        simple counter/timestamp fields. It's called while already holding the
        pool's async lock, so it doesn't need its own async operations.
        """
        self.in_use_count += 1
        self.mark_used()

    def release(self):
        """
        Mark connection as released

        Note: This is a synchronous method (not async) because it only updates
        a simple counter field. It's called while already holding the pool's
        async lock, so it doesn't need its own async operations.
        """
        self.in_use_count = max(0, self.in_use_count - 1)

    async def close(self):
        """Close the connection"""
        if self.conn and not self.conn.is_closed():
            self.conn.close()
            await self.conn.wait_closed()
        self.conn = None
