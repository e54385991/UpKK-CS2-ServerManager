"""SSH host-key discovery and strict fingerprint validation."""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass
from typing import Any

import asyncssh


class HostKeyConfirmationRequired(ValueError):
    """Raised when a managed server has no confirmed SSH host key."""


@dataclass(frozen=True, slots=True)
class SSHHostKeyIdentity:
    """Stable public identity presented by one SSH endpoint."""

    algorithm: str
    fingerprint: str


async def scan_ssh_host_key(
    host: str,
    port: int,
    *,
    timeout: float = 10.0,
) -> SSHHostKeyIdentity:
    """Perform only the SSH handshake and return the advertised host key."""
    if not host or not 1 <= port <= 65535:
        raise ValueError("A valid SSH host and port are required")

    async with asyncio.timeout(timeout):
        key = await asyncssh.get_server_host_key(host, port, config=None)
    if key is None:
        raise ValueError("SSH endpoint did not present a host key")
    return SSHHostKeyIdentity(
        algorithm=key.get_algorithm(),
        fingerprint=key.get_fingerprint("sha256"),
    )


class _PinnedSSHClient(asyncssh.SSHClient):
    """Validate the negotiated key before AsyncSSH starts authentication."""

    def __init__(self, identity: SSHHostKeyIdentity) -> None:
        self._identity = identity

    def validate_host_public_key(self, _host, _addr, _port, key) -> bool:
        algorithm_matches = hmac.compare_digest(
            key.get_algorithm().encode("utf-8"),
            self._identity.algorithm.encode("utf-8"),
        )
        fingerprint_matches = hmac.compare_digest(
            key.get_fingerprint("sha256").encode("utf-8"),
            self._identity.fingerprint.encode("utf-8"),
        )
        return algorithm_matches and fingerprint_matches


def pinned_host_key_options(
    algorithm: str | None,
    fingerprint: str | None,
) -> dict[str, Any]:
    """Build AsyncSSH options which fail closed unless the pin matches."""
    if not algorithm or not fingerprint:
        raise HostKeyConfirmationRequired(
            "SSH host key confirmation is required before connecting to this server"
        )

    identity = SSHHostKeyIdentity(algorithm=algorithm, fingerprint=fingerprint)
    return {
        # Empty known-hosts enables the application callback. ``None`` would
        # disable host-key validation entirely in AsyncSSH.
        "known_hosts": b"",
        "server_host_key_algs": [algorithm],
        "client_factory": lambda: _PinnedSSHClient(identity),
    }


def server_pinned_host_key_options(server) -> dict[str, Any]:
    """Build strict connection options from a Server-compatible object."""
    return pinned_host_key_options(
        getattr(server, "ssh_host_key_algorithm", None),
        getattr(server, "ssh_host_key_fingerprint", None),
    )


__all__ = [
    "HostKeyConfirmationRequired",
    "SSHHostKeyIdentity",
    "pinned_host_key_options",
    "scan_ssh_host_key",
    "server_pinned_host_key_options",
]
