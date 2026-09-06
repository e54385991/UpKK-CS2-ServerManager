"""Callback invocation and propagation contracts across Python versions."""

from __future__ import annotations

import asyncio
from functools import partial

import pytest

from services.ssh.game_steamcmd import GameSteamcmdMixin


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_progress_callback_runs_once_and_preserves_exceptions(asynchronous, wrapped, outcome):
    messages = []
    error = {
        "success": None,
        "error": ValueError("callback failed"),
        "cancel": asyncio.CancelledError(),
    }[outcome]

    def sync_callback(message):
        messages.append(message)
        if error is not None:
            raise error

    async def async_callback(message):
        sync_callback(message)

    callback = async_callback if asynchronous else sync_callback
    if wrapped:
        callback = partial(callback)
    mixin = GameSteamcmdMixin.__new__(GameSteamcmdMixin)
    if error is None:
        await mixin._send_progress_if_callback(callback, "progress")
    else:
        with pytest.raises(type(error)) as caught:
            await mixin._send_progress_if_callback(callback, "progress")
        assert caught.value is error
    assert messages == ["progress"]
    await mixin._send_progress_if_callback(None, "unused")
