"""Explicit mutable state owned by one SSH facade instance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncssh

from modules.models import Server


@dataclass(slots=True)
class SSHRuntimeState:
    """Connection and operation state that must never be shared across facades."""

    connection: asyncssh.SSHClientConnection | None = None
    server: Server | None = None
    last_plugin_backup: dict[str, Any] | None = None
