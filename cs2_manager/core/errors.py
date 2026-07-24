"""Stable wire-level error schemas."""

from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Compatibility error body used by existing JSON endpoints."""

    # Most legacy endpoints return a human-readable string, while a handful of
    # conflict responses include structured recovery metadata inside the same
    # top-level ``detail`` envelope.  Keep that established wire contract while
    # still giving OpenAPI consumers an explicit response model.
    detail: str | dict[str, Any]
