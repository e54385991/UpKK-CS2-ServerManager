"""SQLAlchemy listeners that record execute timing without SQL text."""

from __future__ import annotations

from time import perf_counter

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import Pool

from .control import is_enabled
from .counters import record_db_execute, record_db_invalidate
from .events import record_error

_INSTALLED = False


def install_database_observers(engine: object | None = None) -> None:
    """Attach process-wide listeners once. Safe to call repeatedly."""
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Engine, "before_cursor_execute", _before_cursor_execute)
    event.listen(Engine, "after_cursor_execute", _after_cursor_execute)
    event.listen(Engine, "handle_error", _handle_error)
    event.listen(Pool, "invalidate", _invalidate)
    _INSTALLED = True
    if engine is not None:
        return


def _before_cursor_execute(
    _conn: object,
    _cursor: object,
    _statement: object,
    _parameters: object,
    context: object,
    _executemany: bool,
) -> None:
    if not is_enabled():
        return
    setattr(context, "_panel_obs_start", perf_counter())  # noqa: B010


def _after_cursor_execute(
    _conn: object,
    _cursor: object,
    _statement: object,
    _parameters: object,
    context: object,
    _executemany: bool,
) -> None:
    if not is_enabled():
        return
    started = getattr(context, "_panel_obs_start", None)
    if not isinstance(started, float):
        return
    record_db_execute((perf_counter() - started) * 1000)


def _handle_error(exception_context: object) -> None:
    if not is_enabled():
        return
    started = getattr(
        getattr(exception_context, "execution_context", None), "_panel_obs_start", None
    )
    duration = (perf_counter() - started) * 1000 if isinstance(started, float) else 0.0
    record_db_execute(duration, error=True)
    original = getattr(exception_context, "original_exception", None)
    summary = str(original or getattr(exception_context, "sqlalchemy_exception", "database error"))
    record_error(
        source="database",
        summary=summary,
        error_code="db_error",
        exception=original if isinstance(original, BaseException) else None,
        exception_type=type(original).__name__ if original is not None else "DatabaseError",
    )


def _invalidate(_dbapi_connection: object, _connection_record: object, _exception: object) -> None:
    if not is_enabled():
        return
    record_db_invalidate()
