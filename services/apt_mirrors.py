"""Ubuntu/Debian apt mirror catalog and sources rewrite helpers.

SteamCMD download mirrors in user settings are unrelated — this module only
rewrites host apt sources so ``apt-get update`` / package install can recover
from official-archive network failures.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

AptMirrorId = Literal["official", "ustc", "tuna"]

APT_MIRROR_IDS: tuple[AptMirrorId, ...] = ("official", "ustc", "tuna")

# Friendly aliases accepted by APIs and persisted values.
APT_MIRROR_ALIASES = {
    "official": "official",
    "ustc": "ustc",
    "tuna": "tuna",
    "tsinghua": "tuna",
    "thu": "tuna",
}

APT_SOURCES_BACKUP_DIR = "/etc/apt/upkk-sources-backup"
APT_SOURCES_PREVIOUS_DIR = f"{APT_SOURCES_BACKUP_DIR}/previous"
OS_RELEASE_COMMAND = "cat /etc/os-release 2>/dev/null"

# Markers that mean the current apt *source* or network path is broken, not a
# local dpkg lock or a genuinely missing package after a successful update.
APT_SOURCE_FAILURE_MARKERS = (
    "failed to fetch",
    "could not resolve",
    "temporary failure resolving",
    "connection timed out",
    "connection failed",
    "unable to connect",
    "network is unreachable",
    "no address associated",
    "hash sum mismatch",
    "some index files failed to download",
    "does not have a release file",
    "is not signed",
    "clearsigned file isn't valid",
    "release file expired",
    "mirror sync is in progress",
    "404  not found",
    "403  forbidden",
    "503  service",
    "502  bad gateway",
    "the repository",
)


@dataclass(frozen=True)
class AptMirror:
    id: AptMirrorId
    label_en: str
    label_zh: str
    ubuntu_archive: str
    ubuntu_security: str
    debian_archive: str
    debian_security: str


APT_MIRRORS: dict[str, AptMirror] = {
    "official": AptMirror(
        id="official",
        label_en="Official",
        label_zh="官方",
        ubuntu_archive="http://archive.ubuntu.com/ubuntu",
        ubuntu_security="http://security.ubuntu.com/ubuntu",
        debian_archive="http://deb.debian.org/debian",
        debian_security="http://security.debian.org/debian-security",
    ),
    "ustc": AptMirror(
        id="ustc",
        label_en="USTC",
        label_zh="中科大",
        ubuntu_archive="https://mirrors.ustc.edu.cn/ubuntu",
        ubuntu_security="https://mirrors.ustc.edu.cn/ubuntu",
        debian_archive="https://mirrors.ustc.edu.cn/debian",
        debian_security="https://mirrors.ustc.edu.cn/debian-security",
    ),
    "tuna": AptMirror(
        id="tuna",
        label_en="Tsinghua",
        label_zh="清华",
        ubuntu_archive="https://mirrors.tuna.tsinghua.edu.cn/ubuntu",
        ubuntu_security="https://mirrors.tuna.tsinghua.edu.cn/ubuntu",
        debian_archive="https://mirrors.tuna.tsinghua.edu.cn/debian",
        debian_security="https://mirrors.tuna.tsinghua.edu.cn/debian-security",
    ),
}

_MANAGED_SOURCE_FILES = (
    "ubuntu.sources",
    "debian.sources",
    "ubuntu.list",
    "debian.list",
    "official-package-repositories.list",
)


@dataclass(frozen=True)
class OsRelease:
    id: str
    version_codename: str
    version_id: str

    @property
    def is_ubuntu(self) -> bool:
        return self.id == "ubuntu"

    @property
    def is_debian(self) -> bool:
        return self.id == "debian"


def normalize_apt_mirror(value: str | None) -> AptMirrorId | None:
    """Map a user/API value to a catalog id, or None when unset/unknown."""
    if value is None:
        return None
    key = value.strip().lower()
    if not key:
        return None
    mapped = APT_MIRROR_ALIASES.get(key)
    if mapped in APT_MIRRORS:
        return mapped  # type: ignore[return-value]
    return None


def require_apt_mirror(value: str) -> AptMirrorId:
    normalized = normalize_apt_mirror(value)
    if normalized is None:
        allowed = ", ".join(APT_MIRROR_IDS)
        raise ValueError(f"Unknown apt mirror {value!r}. Allowed: {allowed}")
    return normalized


def mirror_label(mirror_id: str) -> str:
    mirror = APT_MIRRORS.get(mirror_id)
    if mirror is None:
        return mirror_id
    return f"{mirror.label_en} ({mirror.label_zh})"


def mirror_order(preferred: str | None = None) -> tuple[AptMirrorId, ...]:
    """Preferred mirror first, then the rest of the catalog."""
    preferred_id = normalize_apt_mirror(preferred)
    if preferred_id is None:
        return APT_MIRROR_IDS
    return (preferred_id, *(item for item in APT_MIRROR_IDS if item != preferred_id))


def parse_os_release(text: str) -> OsRelease | None:
    """Parse ``/etc/os-release`` into Ubuntu/Debian identity + codename."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        values[key.strip()] = raw.strip().strip('"').strip("'")

    distro_id = (values.get("ID") or "").lower()
    like = (values.get("ID_LIKE") or "").lower()
    if distro_id not in {"ubuntu", "debian"}:
        tokens = set(like.split())
        if "ubuntu" in tokens:
            distro_id = "ubuntu"
        elif "debian" in tokens:
            distro_id = "debian"
        else:
            return None
    codename = (values.get("VERSION_CODENAME") or "").lower()
    if not codename:
        return None
    return OsRelease(
        id=distro_id,
        version_codename=codename,
        version_id=values.get("VERSION_ID") or "",
    )


def is_apt_source_failure(*chunks: str) -> bool:
    """True when apt failed because sources or the network path are broken."""
    blob = " ".join(chunk or "" for chunk in chunks).lower()
    return any(marker in blob for marker in APT_SOURCE_FAILURE_MARKERS)


def _archive_urls(os_release: OsRelease, mirror: AptMirror) -> tuple[str, str]:
    if os_release.is_ubuntu:
        return mirror.ubuntu_archive, mirror.ubuntu_security
    return mirror.debian_archive, mirror.debian_security


def render_deb822_sources(os_release: OsRelease, mirror_id: str) -> str:
    """DEB822 body for ``ubuntu.sources`` / ``debian.sources``."""
    mirror = APT_MIRRORS[require_apt_mirror(mirror_id)]
    archive, security = _archive_urls(os_release, mirror)
    codename = os_release.version_codename
    if os_release.is_ubuntu:
        signed_by = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
        components = "main restricted universe multiverse"
        suites = f"{codename} {codename}-updates {codename}-backports"
        security_suite = f"{codename}-security"
    else:
        signed_by = "/usr/share/keyrings/debian-archive-keyring.gpg"
        components = "main contrib non-free non-free-firmware"
        suites = f"{codename} {codename}-updates"
        security_suite = f"{codename}-security"
    return (
        f"Types: deb\n"
        f"URIs: {archive}\n"
        f"Suites: {suites}\n"
        f"Components: {components}\n"
        f"Signed-By: {signed_by}\n"
        f"\n"
        f"Types: deb\n"
        f"URIs: {security}\n"
        f"Suites: {security_suite}\n"
        f"Components: {components}\n"
        f"Signed-By: {signed_by}\n"
    )


def render_classic_sources_list(os_release: OsRelease, mirror_id: str) -> str:
    """Traditional ``sources.list`` lines (Ubuntu/Debian)."""
    mirror = APT_MIRRORS[require_apt_mirror(mirror_id)]
    archive, security = _archive_urls(os_release, mirror)
    codename = os_release.version_codename
    if os_release.is_ubuntu:
        components = "main restricted universe multiverse"
        lines = [
            f"deb {archive} {codename} {components}",
            f"deb {archive} {codename}-updates {components}",
            f"deb {archive} {codename}-backports {components}",
            f"deb {security} {codename}-security {components}",
        ]
    else:
        components = "main contrib non-free non-free-firmware"
        lines = [
            f"deb {archive} {codename} {components}",
            f"deb {archive} {codename}-updates {components}",
            f"deb {security} {codename}-security {components}",
        ]
    return "\n".join(lines) + "\n"


def apply_apt_mirror_command(os_release: OsRelease, mirror_id: str) -> str:
    """Privileged shell that backs up current sources and writes the catalog entry.

    Writes modern DEB822 (``ubuntu.sources`` / ``debian.sources``) and a pointer
    ``/etc/apt/sources.list`` so both Ubuntu 24.04+ and classic layouts work.
    Previous files are copied to ``/etc/apt/upkk-sources-backup/previous``.
    """
    normalized = require_apt_mirror(mirror_id)
    deb822 = render_deb822_sources(os_release, normalized)
    classic = render_classic_sources_list(os_release, normalized)
    deb822_name = "ubuntu.sources" if os_release.is_ubuntu else "debian.sources"
    deb822_path = f"/etc/apt/sources.list.d/{deb822_name}"
    pointer = (
        f"# Managed by UpKK CS2 Server Manager. Active mirror: {normalized}.\n"
        f"# Previous sources were copied to {APT_SOURCES_PREVIOUS_DIR}.\n"
        f"# Canonical entries live in {deb822_path}.\n"
    )
    backup_copies = " ".join(
        f"if [ -e /etc/apt/sources.list.d/{name} ]; then "
        f"cp -a /etc/apt/sources.list.d/{name} {shlex.quote(APT_SOURCES_PREVIOUS_DIR + '/' + name)}; "
        f"rm -f /etc/apt/sources.list.d/{name}; "
        f"fi;"
        for name in _MANAGED_SOURCE_FILES
    )
    return "\n".join(
        (
            "set -eu",
            f"mkdir -p {shlex.quote(APT_SOURCES_PREVIOUS_DIR)} /etc/apt/sources.list.d",
            f"if [ -f /etc/apt/sources.list ]; then "
            f"cp -a /etc/apt/sources.list {shlex.quote(APT_SOURCES_PREVIOUS_DIR + '/sources.list')}; "
            f"fi",
            backup_copies.rstrip(";"),
            f"printf '%s\\n' {shlex.quote(deb822.rstrip(chr(10)))} > {shlex.quote(deb822_path)}",
            f"printf '%s\\n' {shlex.quote(classic.rstrip(chr(10)))} > "
            f"{shlex.quote(APT_SOURCES_PREVIOUS_DIR + '/sources.list.classic')}",
            f"printf '%s\\n' {shlex.quote(pointer.rstrip(chr(10)))} > /etc/apt/sources.list",
            f"printf 'applied:%s\\n' {shlex.quote(normalized)}",
        )
    )


def switch_hint(failed: Iterable[str] = (), current: str | None = None) -> str:
    """Operator-facing sentence for init/deploy logs and the Next console."""
    catalog = "Official, USTC (中科大), or Tsinghua (清华)"
    parts = [
        f"Switch the apt mirror from the operations center ({catalog}) and retry.",
    ]
    if current:
        parts.insert(0, f"Last attempted mirror: {mirror_label(current)}.")
    failed_ids = tuple(dict.fromkeys(item for item in failed if item))
    if failed_ids:
        labels = ", ".join(mirror_label(item) for item in failed_ids)
        parts.insert(0, f"Failed mirrors: {labels}.")
    return " ".join(parts)
