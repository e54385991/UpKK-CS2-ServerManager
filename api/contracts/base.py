"""Pydantic v2 policies for HTTP request and response DTOs."""

from pydantic import BaseModel, ConfigDict


class ApiRequest(BaseModel):
    """Strict input DTO used by the maintained ``/api/v1`` surface."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )


class ApiResponse(BaseModel):
    """Output DTO; response data is always explicitly selected by presenters."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )
