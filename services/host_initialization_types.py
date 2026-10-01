"""Host initialization command protocol and immutable result."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Privilege = Literal["root", "sudo", "none"]


class HostCommandRunner(Protocol):
    async def run(self, command: str, *, timeout: float = 60) -> tuple[int, str, str]: ...

    async def run_privileged(
        self, command: str, *, timeout: float = 600
    ) -> tuple[int, str, str]: ...

    async def resolve_privilege(self) -> Privilege: ...


@dataclass(frozen=True)
class HostDependencyResult:
    success: bool
    architecture_supported: bool
    architecture: str
    missing_before: tuple[str, ...]
    missing_after: tuple[str, ...]
    installed: bool
    privilege: Privilege
    message: str
    manual_install_command: str | None
    logs: tuple[str, ...]
    apt_mirror: str | None = None
    failed_mirrors: tuple[str, ...] = ()
    os_id: str | None = None
    os_version: str | None = None

    @staticmethod
    def ready(
        *,
        architecture: str,
        logs: Sequence[str] = (),
        apt_mirror: str | None = None,
    ) -> HostDependencyResult:
        return HostDependencyResult(
            success=True,
            architecture_supported=True,
            architecture=architecture,
            missing_before=(),
            missing_after=(),
            installed=False,
            privilege="root",
            message="SteamCMD host dependencies are installed and verified.",
            manual_install_command=None,
            logs=tuple(logs),
            apt_mirror=apt_mirror,
        )
