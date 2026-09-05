"""Shared Linux dependency contract for setup and SteamCMD deployment."""

from __future__ import annotations

import shlex
from collections.abc import Iterable

APT_RETRY_ATTEMPTS = 3
APT_RETRY_DELAYS_SECONDS = (2, 5)
APT_OPTIONS = (
    "-o",
    "DPkg::Lock::Timeout=120",
    "-o",
    "Acquire::Retries=3",
    "-o",
    "Dpkg::Use-Pty=0",
)

# Valve's Linux SteamCMD bootstrap is a 32-bit x86 executable and CS2 is x86-64.
# The project therefore supports Debian's amd64 architecture, not arm64 or other
# architectures, unless Valve ships native binaries for them in the future.
STEAMCMD_SUPPORTED_ARCHITECTURES = frozenset({"amd64", "x86_64"})

# Keep this list limited to packages which are required to download, unpack and
# execute SteamCMD. Optional panel/plugin conveniences belong below so a missing
# optional repository cannot make the SteamCMD runtime unusable.
STEAMCMD_RUNTIME_PACKAGES = (
    "ca-certificates",
    "libc6-i386",
    "lib32gcc-s1",
    "lib32stdc++6",
    "lib32z1",
)

STEAMCMD_REQUIRED_PACKAGES = (
    *STEAMCMD_RUNTIME_PACKAGES,
    "curl",
    "wget",
    "tar",
    "unzip",
    "screen",
    "tmux",
    "bzip2",
    "patchelf",
)

SETUP_OPTIONAL_PACKAGES = ("libicu-dev",)
SEVEN_ZIP_PACKAGE_ALTERNATIVES = ("7zip", "p7zip-full")


def normalize_debian_architecture(value: str) -> str:
    """Normalize dpkg/uname architecture names used by the remote probes."""
    normalized = value.strip().lower()
    return "amd64" if normalized == "x86_64" else normalized


def steamcmd_architecture_supported(value: str) -> bool:
    return normalize_debian_architecture(value) in STEAMCMD_SUPPORTED_ARCHITECTURES


def apt_get_command(action: str, packages: Iterable[str] = ()) -> str:
    """Build a noninteractive apt command with network and lock resilience."""
    arguments = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", *APT_OPTIONS, action]
    if action == "install":
        arguments.append("-y")
    arguments.extend(packages)
    return shlex.join(arguments)


def installed_packages_verification_command(packages: Iterable[str]) -> str:
    """Return a shell probe which prints missing packages and fails if any are absent."""
    quoted_packages = " ".join(shlex.quote(package) for package in packages)
    return (
        "missing=''; "
        f"for package in {quoted_packages}; do "
        "status=$(dpkg-query -W -f='${Status}' \"$package\" 2>/dev/null || true); "
        'if [ "$status" != \'install ok installed\' ]; then missing="$missing $package"; fi; '
        "done; "
        'if [ -n "$missing" ]; then '
        "printf 'Missing required packages:%s\\n' \"$missing\" >&2; exit 1; "
        "fi"
    )


MISSING_PACKAGES_MARKER = "Missing required packages:"


def parse_missing_packages(*chunks: str) -> list[str]:
    """Extract package names emitted by ``installed_packages_verification_command``."""
    names: list[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        for line in chunk.splitlines():
            if MISSING_PACKAGES_MARKER not in line:
                continue
            _, _, rest = line.partition(MISSING_PACKAGES_MARKER)
            names.extend(part for part in rest.split() if part)
    return list(dict.fromkeys(names))


def manual_install_command(packages: Iterable[str] | None = None) -> str:
    """Exact apt command the operator can paste if automatic install fails."""
    selected = tuple(packages) if packages else STEAMCMD_REQUIRED_PACKAGES
    return "sudo apt-get install -y " + " ".join(selected)
