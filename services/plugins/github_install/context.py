"""Shared GitHub plugin install session state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from modules import GitHubPluginInstallRequest, Server, User
from services.compat import LateBoundModule

ProgressFn = Callable[..., Awaitable[None]]
NotifyFn = Callable[..., Awaitable[None]]
host = LateBoundModule("services.plugin_installation")


@dataclass
class GithubInstallContext:
    server: Server
    request: GitHubPluginInstallRequest
    current_user: User
    ssh_manager: Any
    remote_temp_dir: str
    progress: ProgressFn
    notify_install_result: NotifyFn
    record_installation: Callable[[], Awaitable[None]]
    csgo_dir: str
