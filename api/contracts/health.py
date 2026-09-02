"""Public health response contract, including safe build metadata."""

from typing import Literal

from api.contracts.base import ApiResponse


class HealthResponse(ApiResponse):
    """Liveness payload used by Docker and the console runtime footer."""

    status: Literal["healthy"]
    redis: Literal["connected", "disconnected"]
    version: str
    python: str
    fastapi: str
    git_sha: str
    build_time: str


__all__ = ["HealthResponse"]
