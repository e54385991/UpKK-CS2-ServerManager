"""Restart-loop protection window and remote wrapper refresh."""

from __future__ import annotations

import pytest

from services.restart_protection import restart_protection_hours, restart_protection_window
from services.ssh.autorestart_script import (
    ensure_autorestart_script,
    local_autorestart_script_text,
    script_digest,
)


def test_protection_hours_default_and_bounds():
    assert restart_protection_hours(None) == 2
    assert restart_protection_hours("nope") == 2
    assert restart_protection_hours(True) == 2
    assert restart_protection_hours(0) == 1
    assert restart_protection_hours(900) == 720
    assert restart_protection_hours("6") == 6
    server = type("Server", (), {"restart_protection_hours": 12})()
    assert restart_protection_window(server).total_seconds() == 12 * 3600
    assert restart_protection_window(None).total_seconds() == 2 * 3600


def test_wrapper_defaults_to_two_hours():
    script = local_autorestart_script_text()
    assert 'TIME_WINDOW="${TIME_WINDOW:-7200}"' in script


@pytest.mark.asyncio
async def test_ensure_skips_upload_when_the_remote_script_matches():
    body = local_autorestart_script_text()
    digest = script_digest(body)
    calls: list[str] = []

    async def execute(command, **_kwargs):
        calls.append(command)
        return True, f"{digest}  /srv/cs2/cs2_autorestart.sh\n", ""

    ready, status = await ensure_autorestart_script(execute, "/srv/cs2/cs2_autorestart.sh")
    assert (ready, status) == (True, "current")
    assert len(calls) == 1
    assert calls[0].startswith("sha256sum ")


@pytest.mark.asyncio
async def test_ensure_uploads_when_the_remote_script_differs():
    calls: list[str] = []

    async def execute(command, **_kwargs):
        calls.append(command)
        if command.startswith("sha256sum"):
            return False, "", "missing"
        return True, "", ""

    ready, status = await ensure_autorestart_script(execute, "/srv/cs2/cs2_autorestart.sh")
    assert (ready, status) == (True, "deployed")
    assert calls[1].startswith("cat > ")
    assert "chmod +x " in calls[1]
    assert "EOFSCRIPT" in calls[1]
