"""Detect, auto-install, and verify SteamCMD host packages over SSH."""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from services.apt_mirrors import (
    OS_RELEASE_COMMAND,
    apply_apt_mirror_command,
    is_apt_source_failure,
    mirror_label,
    mirror_order,
    normalize_apt_mirror,
    parse_os_release,
    switch_hint,
)
from services.system_dependencies import (
    APT_RETRY_ATTEMPTS,
    APT_RETRY_DELAYS_SECONDS,
    STEAMCMD_REQUIRED_PACKAGES,
    apt_get_command,
    installed_packages_verification_command,
    manual_install_command,
    normalize_debian_architecture,
    parse_missing_packages,
    steamcmd_architecture_supported,
)

Privilege = Literal["root", "sudo", "none"]
ProgressCallback = Callable[[str], Awaitable[None] | None]


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


class AsyncsshHostRunner:
    """Drive host commands through an already-open asyncssh connection."""

    def __init__(
        self,
        conn,
        *,
        sudo_password: str | None = None,
    ) -> None:
        self._conn = conn
        self._sudo_password = sudo_password
        self._privilege: Privilege = "none"

    async def run(self, command: str, *, timeout: float = 60) -> tuple[int, str, str]:
        result = await self._conn.run(command, check=False, timeout=timeout)
        return int(result.exit_status or 0), result.stdout or "", result.stderr or ""

    async def run_privileged(self, command: str, *, timeout: float = 600) -> tuple[int, str, str]:
        if self._privilege == "root":
            return await self.run(command, timeout=timeout)

        privileged = f"sudo -n -- sh -c {shlex.quote(command)}"
        if self._sudo_password:
            privileged = (
                f"printf '%s\\n' {shlex.quote(self._sudo_password)} | "
                f"sudo -S -- sh -c {shlex.quote(command)}"
            )
        result = await self._conn.run(privileged, check=False, timeout=timeout)
        return int(result.exit_status or 0), result.stdout or "", result.stderr or ""

    async def resolve_privilege(self) -> Privilege:
        code, stdout, _ = await self.run("whoami")
        if code == 0 and stdout.strip() == "root":
            self._privilege = "root"
            return self._privilege
        nopass_code, _, _ = await self.run("sudo -n true")
        if nopass_code == 0:
            self._sudo_password = None
            self._privilege = "sudo"
            return self._privilege
        if self._sudo_password:
            code, _, _ = await self.run_privileged("true")
            if code == 0:
                self._privilege = "sudo"
                return self._privilege
        self._privilege = "none"
        return self._privilege


class SshManagerHostRunner:
    """Drive host commands through an already-connected SSHManager."""

    def __init__(self, manager, server) -> None:
        self._manager = manager
        self._sudo_password = getattr(server, "sudo_password", None) or getattr(
            server, "ssh_password", None
        )
        self._privilege: Privilege = "none"

    async def run(self, command: str, *, timeout: float = 60) -> tuple[int, str, str]:
        success, stdout, stderr = await self._manager.execute_command(command, timeout=int(timeout))
        return (0 if success else 1), stdout or "", stderr or ""

    async def run_privileged(self, command: str, *, timeout: float = 600) -> tuple[int, str, str]:
        if self._privilege == "root":
            return await self.run(command, timeout=timeout)
        success, stdout, stderr = await self._manager.execute_sudo_command(
            command, self._sudo_password, timeout=int(timeout)
        )
        if success:
            return 0, stdout or "", stderr or ""
        if self._sudo_password:
            retry_ok, retry_out, retry_err = await self._manager.execute_sudo_command(
                command, None, timeout=int(timeout)
            )
            if retry_ok:
                self._sudo_password = None
                return 0, retry_out or "", retry_err or ""
        return 1, stdout or "", stderr or ""

    async def resolve_privilege(self) -> Privilege:
        code, stdout, _ = await self.run("whoami")
        if code == 0 and stdout.strip() == "root":
            self._privilege = "root"
            return self._privilege
        nopass_code, _, _ = await self.run("sudo -n true")
        if nopass_code == 0:
            self._sudo_password = None
            self._privilege = "sudo"
            return self._privilege
        if self._sudo_password:
            code, _, _ = await self.run_privileged("true")
            if code == 0:
                self._privilege = "sudo"
                return self._privilege
        self._privilege = "none"
        return self._privilege


def _manual_message(
    missing: Iterable[str],
    *,
    reason: str,
    failed_mirrors: Sequence[str] = (),
    apt_mirror: str | None = None,
) -> str:
    packages = tuple(missing)
    command = manual_install_command(packages or STEAMCMD_REQUIRED_PACKAGES)
    listed = ", ".join(packages) if packages else ", ".join(STEAMCMD_REQUIRED_PACKAGES)
    hint = switch_hint(failed_mirrors, apt_mirror)
    return (
        f"{reason} Missing packages: {listed}. {hint} "
        f"If automatic install still fails, run this on the host: {command}"
    )


def _failed(
    *,
    architecture: str,
    architecture_supported: bool,
    missing_before: Sequence[str],
    missing_after: Sequence[str],
    installed: bool,
    privilege: Privilege,
    reason: str,
    logs: Sequence[str],
    apt_mirror: str | None = None,
    failed_mirrors: Sequence[str] = (),
) -> HostDependencyResult:
    leftover = tuple(missing_after or missing_before)
    return HostDependencyResult(
        success=False,
        architecture_supported=architecture_supported,
        architecture=architecture,
        missing_before=tuple(missing_before),
        missing_after=tuple(missing_after),
        installed=installed,
        privilege=privilege,
        message=_manual_message(
            leftover,
            reason=reason,
            failed_mirrors=failed_mirrors,
            apt_mirror=apt_mirror,
        ),
        manual_install_command=manual_install_command(leftover or STEAMCMD_REQUIRED_PACKAGES),
        logs=tuple(logs),
        apt_mirror=apt_mirror,
        failed_mirrors=tuple(failed_mirrors),
    )


async def _emit(progress: ProgressCallback | None, logs: list[str], message: str) -> None:
    logs.append(message)
    if progress is None:
        return
    result = progress(message)
    if asyncio.iscoroutine(result):
        await result


async def _probe_missing(runner: HostCommandRunner, packages: Sequence[str]) -> list[str]:
    code, stdout, stderr = await runner.run(
        installed_packages_verification_command(packages), timeout=60
    )
    if code == 0:
        return []
    parsed = parse_missing_packages(stdout, stderr)
    return parsed or list(packages)


async def _run_privileged_with_retry(
    runner: HostCommandRunner,
    command: str,
    *,
    description: str,
    progress: ProgressCallback | None,
    logs: list[str],
) -> tuple[int, str, str]:
    last = (1, "", f"{description} has not run")
    for attempt in range(1, APT_RETRY_ATTEMPTS + 1):
        if attempt > 1:
            await _emit(
                progress, logs, f"Retrying {description} ({attempt}/{APT_RETRY_ATTEMPTS})..."
            )
        try:
            last = await runner.run_privileged(command, timeout=600)
        except TimeoutError:
            last = (124, "", f"{description} timed out after 600 seconds")
        if last[0] == 0:
            return last
        detail = (last[2] or last[1] or "no error output").strip()
        await _emit(
            progress,
            logs,
            f"⚠ {description} failed ({attempt}/{APT_RETRY_ATTEMPTS}): {detail[-500:]}",
        )
        if is_apt_source_failure(last[1], last[2]):
            await _emit(
                progress,
                logs,
                "Apt source or network error detected. "
                "The same mirror will not be retried; a different catalog entry can be applied.",
            )
            return last
        if attempt < APT_RETRY_ATTEMPTS:
            delay = APT_RETRY_DELAYS_SECONDS[min(attempt - 1, len(APT_RETRY_DELAYS_SECONDS) - 1)]
            await _emit(progress, logs, f"Retrying in {delay} seconds...")
            await asyncio.sleep(delay)
    return last


async def _detect_os_release(runner: HostCommandRunner):
    code, stdout, _ = await runner.run(OS_RELEASE_COMMAND, timeout=30)
    if code != 0:
        return None
    return parse_os_release(stdout)


async def _apply_apt_mirror(
    runner: HostCommandRunner,
    os_release,
    mirror_id: str,
    *,
    progress: ProgressCallback | None,
    logs: list[str],
) -> tuple[bool, str]:
    label = mirror_label(mirror_id)
    await _emit(
        progress,
        logs,
        f"Switching apt sources to {label}. Previous files are backed up under "
        "/etc/apt/upkk-sources-backup/previous so Official can be restored.",
    )
    command = apply_apt_mirror_command(os_release, mirror_id)
    code, stdout, stderr = await runner.run_privileged(command, timeout=120)
    if code == 0:
        await _emit(progress, logs, f"✓ Apt sources now use {label}")
        return True, stdout
    detail = (stderr or stdout or "no error output").strip()
    await _emit(progress, logs, f"⚠ Could not apply {label}: {detail[-400:]}")
    return False, detail


async def _update_and_install(
    runner: HostCommandRunner,
    missing: Sequence[str],
    *,
    progress: ProgressCallback | None,
    logs: list[str],
) -> tuple[int, str, str, str]:
    update_code, update_out, update_err = await _run_privileged_with_retry(
        runner,
        apt_get_command("update"),
        description="apt-get update",
        progress=progress,
        logs=logs,
    )
    if update_code != 0:
        return update_code, update_out, update_err, "update"
    await _emit(progress, logs, f"Installing missing packages: {' '.join(missing)}")
    install_code, install_out, install_err = await _run_privileged_with_retry(
        runner,
        apt_get_command("install", missing),
        description="apt-get install",
        progress=progress,
        logs=logs,
    )
    return install_code, install_out, install_err, "install"


async def ensure_steamcmd_packages(
    runner: HostCommandRunner,
    packages: Sequence[str] = STEAMCMD_REQUIRED_PACKAGES,
    *,
    progress: ProgressCallback | None = None,
    preferred_mirror: str | None = None,
    apply_preferred_first: bool = False,
) -> HostDependencyResult:
    """Probe the host and install any missing SteamCMD/CS2 runtime packages."""
    logs: list[str] = []
    preferred = normalize_apt_mirror(preferred_mirror)
    failed_mirrors: list[str] = []
    active_mirror = preferred
    code, stdout, stderr = await runner.run("dpkg --print-architecture 2>/dev/null || uname -m")
    architecture = normalize_debian_architecture(stdout)
    if code != 0 or not steamcmd_architecture_supported(architecture):
        detected = architecture or stderr.strip() or "unknown"
        message = (
            f"Unsupported server architecture: {detected}. "
            "SteamCMD/CS2 requires amd64 (x86_64); arm64/aarch64 cannot run it natively."
        )
        await _emit(progress, logs, message)
        return HostDependencyResult(
            success=False,
            architecture_supported=False,
            architecture=detected,
            missing_before=(),
            missing_after=(),
            installed=False,
            privilege="none",
            message=message,
            manual_install_command=None,
            logs=tuple(logs),
            apt_mirror=preferred,
        )
    await _emit(progress, logs, f"Detected architecture: {architecture}")

    apt_code, _, _ = await runner.run("command -v apt-get")
    if apt_code != 0:
        return _failed(
            architecture=architecture,
            architecture_supported=True,
            missing_before=tuple(packages),
            missing_after=tuple(packages),
            installed=False,
            privilege="none",
            reason="apt-get is not installed; automatic setup only supports Ubuntu/Debian.",
            logs=logs,
            apt_mirror=preferred,
        )

    missing = await _probe_missing(runner, packages)
    if not missing:
        await _emit(progress, logs, "✓ SteamCMD host dependencies are already installed")
        return HostDependencyResult.ready(
            architecture=architecture,
            logs=logs,
            apt_mirror=preferred,
        )

    await _emit(progress, logs, f"Missing required packages: {' '.join(missing)}")
    privilege = await runner.resolve_privilege()
    if privilege == "none":
        return _failed(
            architecture=architecture,
            architecture_supported=True,
            missing_before=missing,
            missing_after=missing,
            installed=False,
            privilege=privilege,
            reason=(
                "Could not elevate to root to install packages automatically. "
                "Record a sudo/root password on the server (or SSH as root) so apt, "
                "mirror switch, and package install can run."
            ),
            logs=logs,
            apt_mirror=preferred,
        )
    await _emit(progress, logs, f"Installing as {privilege}...")

    foreign_code, foreign_out, _ = await runner.run("dpkg --print-foreign-architectures")
    if foreign_code != 0 or "i386" not in foreign_out.split():
        await _emit(progress, logs, "Enabling i386 multiarch for the 32-bit SteamCMD runtime...")
        add_code, add_out, add_err = await runner.run_privileged("dpkg --add-architecture i386")
        if add_code != 0:
            await _emit(
                progress,
                logs,
                "⚠ Could not enable i386 multiarch; continuing with package install. "
                f"{(add_err or add_out).strip()[-300:]}",
            )

    os_release = await _detect_os_release(runner)
    if os_release is None:
        await _emit(
            progress,
            logs,
            "Could not read a Ubuntu/Debian /etc/os-release; apt mirror switching is skipped.",
        )
    elif preferred:
        await _emit(
            progress,
            logs,
            f"Preferred apt mirror: {mirror_label(preferred)}. "
            "Official / USTC (中科大) / Tsinghua (清华) can be switched from the operations center.",
        )

    async def finish_success(*, installed: bool) -> HostDependencyResult:
        leftover = await _probe_missing(runner, packages)
        if leftover:
            return _failed(
                architecture=architecture,
                architecture_supported=True,
                missing_before=missing,
                missing_after=leftover,
                installed=installed,
                privilege=privilege,
                reason="Automatic package installation failed.",
                logs=logs,
                apt_mirror=active_mirror,
                failed_mirrors=failed_mirrors,
            )
        await _emit(progress, logs, f"✓ Installed and verified: {', '.join(missing)}")
        if active_mirror:
            await _emit(progress, logs, f"Active apt mirror: {mirror_label(active_mirror)}")
        return HostDependencyResult(
            success=True,
            architecture_supported=True,
            architecture=architecture,
            missing_before=tuple(missing),
            missing_after=(),
            installed=True,
            privilege=privilege,
            message=(
                "SteamCMD host dependencies were missing; they are now installed and verified."
                + (f" Apt mirror: {mirror_label(active_mirror)}." if active_mirror else "")
            ),
            manual_install_command=None,
            logs=tuple(logs),
            apt_mirror=active_mirror,
            failed_mirrors=tuple(failed_mirrors),
        )

    tried: list[str] = []
    if preferred and (apply_preferred_first or preferred) and os_release is not None:
        applied, _ = await _apply_apt_mirror(
            runner, os_release, preferred, progress=progress, logs=logs
        )
        tried.append(preferred)
        active_mirror = preferred
        if not applied:
            failed_mirrors.append(preferred)

    action_code, action_out, action_err, action = await _update_and_install(
        runner, missing, progress=progress, logs=logs
    )
    if action_code == 0:
        return await finish_success(installed=True)

    leftover = await _probe_missing(runner, packages)
    source_failed = is_apt_source_failure(action_out, action_err)
    if not source_failed or os_release is None:
        reason = (
            "Automatic apt-get update failed."
            if action == "update"
            else "Automatic package installation failed."
        )
        if source_failed:
            reason = f"{reason} {switch_hint(failed_mirrors, active_mirror)}"
        return _failed(
            architecture=architecture,
            architecture_supported=True,
            missing_before=missing,
            missing_after=leftover or missing,
            installed=action == "install" and action_code == 0,
            privilege=privilege,
            reason=reason,
            logs=logs,
            apt_mirror=active_mirror,
            failed_mirrors=failed_mirrors,
        )

    if active_mirror and active_mirror not in failed_mirrors:
        failed_mirrors.append(active_mirror)
    await _emit(
        progress,
        logs,
        f"Apt {action} failed on "
        f"{mirror_label(active_mirror) if active_mirror else 'the current sources'} "
        "because of a software-source or network error. "
        f"{switch_hint(failed_mirrors, active_mirror)}",
    )

    for candidate in mirror_order(preferred):
        if candidate in tried:
            continue
        applied, _ = await _apply_apt_mirror(
            runner, os_release, candidate, progress=progress, logs=logs
        )
        tried.append(candidate)
        active_mirror = candidate
        if not applied:
            failed_mirrors.append(candidate)
            continue
        action_code, action_out, action_err, action = await _update_and_install(
            runner, missing, progress=progress, logs=logs
        )
        if action_code == 0:
            return await finish_success(installed=True)
        leftover = await _probe_missing(runner, packages)
        if not is_apt_source_failure(action_out, action_err):
            reason = (
                "Automatic apt-get update failed."
                if action == "update"
                else "Automatic package installation failed."
            )
            return _failed(
                architecture=architecture,
                architecture_supported=True,
                missing_before=missing,
                missing_after=leftover or missing,
                installed=action == "install" and action_code == 0,
                privilege=privilege,
                reason=reason,
                logs=logs,
                apt_mirror=active_mirror,
                failed_mirrors=failed_mirrors,
            )
        failed_mirrors.append(candidate)
        await _emit(
            progress,
            logs,
            f"⚠ {mirror_label(candidate)} also failed apt {action}. Trying the next catalog entry...",
        )

    leftover = await _probe_missing(runner, packages)
    return _failed(
        architecture=architecture,
        architecture_supported=True,
        missing_before=missing,
        missing_after=leftover or missing,
        installed=False,
        privilege=privilege,
        reason="Automatic apt-get update/install failed after trying Official, USTC, and Tsinghua.",
        logs=logs,
        apt_mirror=active_mirror,
        failed_mirrors=failed_mirrors,
    )


def attach_host_initialization(server, result: HostDependencyResult) -> None:
    object.__setattr__(server, "_host_initialization", result)


def host_initialization_of(server) -> HostDependencyResult | None:
    return getattr(server, "_host_initialization", None)
