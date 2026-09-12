"""Request-scoped correlation identifiers for monitoring records."""

from __future__ import annotations

from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("panel_monitor_request_id", default=None)
_operation_id: ContextVar[str | None] = ContextVar("panel_monitor_operation_id", default=None)
_generation: ContextVar[int] = ContextVar("panel_monitor_generation", default=0)
_monitor_io: ContextVar[bool] = ContextVar("panel_monitor_storage_io", default=False)


def current_request_id() -> str | None:
    return _request_id.get()


def current_operation_id() -> str | None:
    return _operation_id.get()


def current_generation() -> int:
    return _generation.get()


def is_monitor_io() -> bool:
    return _monitor_io.get()


def bind_request(*, request_id: str, generation: int) -> Token[str | None]:
    _generation.set(generation)
    return _request_id.set(request_id)


def reset_request(token: Token[str | None]) -> None:
    _request_id.reset(token)


def bind_operation(operation_id: str) -> Token[str | None]:
    return _operation_id.set(operation_id)


def reset_operation(token: Token[str | None]) -> None:
    _operation_id.reset(token)


def set_monitor_io(active: bool) -> Token[bool]:
    return _monitor_io.set(active)


def reset_monitor_io(token: Token[bool]) -> None:
    _monitor_io.reset(token)
