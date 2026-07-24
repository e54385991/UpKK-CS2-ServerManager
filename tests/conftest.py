"""Provide non-secret application settings while collecting the test suite."""

from pathlib import Path

import pytest
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env.example", override=False)


@pytest.fixture(autouse=True)
def _unit_coordination_store(request, monkeypatch):
    """Keep unit tests deterministic while production locks remain fail-closed.

    Redis integration tests opt out with ``@pytest.mark.redis_integration``.
    Individual security tests can still replace these methods after this
    fixture is installed to exercise unavailable/busy states.
    """
    if request.node.get_closest_marker("redis_integration"):
        return

    from services.redis_manager import redis_manager

    async def acquire(*_args, **_kwargs):
        return True

    async def release(*_args, **_kwargs):
        return True

    async def refresh(*_args, **_kwargs):
        return True

    async def is_held(*_args, **_kwargs):
        return False

    monkeypatch.setattr(redis_manager, "acquire_lock", acquire)
    monkeypatch.setattr(redis_manager, "release_lock", release)
    monkeypatch.setattr(redis_manager, "refresh_lock", refresh)
    monkeypatch.setattr(redis_manager, "is_lock_held", is_held)
