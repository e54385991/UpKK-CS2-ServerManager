"""Security and planning coverage for the enhanced CS2 agent tools."""

from __future__ import annotations

from types import SimpleNamespace

from services.server_startup_service import (
    STARTUP_REVISION_FIELDS,
)


def _startup_server():
    values = {field: None for field in STARTUP_REVISION_FIELDS}
    values.update(
        {
            "id": 32,
            "user_id": 8,
            "name": "KZ Server",
            "host": "203.0.113.10",
            "ssh_port": 22,
            "game_port": 27015,
            "game_directory": "/srv/cs2",
            "server_name": "KZ Server",
            "default_map": "de_dust2",
            "max_players": 32,
            "game_mode": "competitive",
            "game_type": "0",
            "additional_parameters": None,
            "tv_enable": False,
            "session_manager": "tmux",
            "a2s_query_host": None,
            "a2s_query_port": None,
            "status": SimpleNamespace(value="running"),
        }
    )
    return SimpleNamespace(**values)


class _StartupLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return None


class _StartupDB:
    def __init__(self, *, fail_first_commit=False):
        self.commits = 0
        self.rollbacks = 0
        self.fail_first_commit = fail_first_commit

    async def commit(self):
        self.commits += 1
        if self.fail_first_commit and self.commits == 1:
            raise RuntimeError("database unavailable")

    async def rollback(self):
        self.rollbacks += 1
