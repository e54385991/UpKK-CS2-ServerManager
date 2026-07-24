"""Public response contracts for S3-backed server maintenance."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class S3RestoreResponse(BaseModel):
    """Result of restoring one validated backup object to a game server."""

    success: bool
    message: str
    restored_from: str
    remote_archive_path: str
    safety_backup: dict[str, Any] | None = None
