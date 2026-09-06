"""Keep CounterStrikeSharp's ``core.json`` on the panel's managed defaults.

CounterStrikeSharp ships ``FollowCS2ServerGuidelines: true``, which makes it
refuse to load plugins that touch APIs Valve's server guidelines disallow.
Community servers managed here install those plugins deliberately, so every
CounterStrikeSharp install and upgrade — the framework action *and* a
marketplace/GitHub install of the framework itself — lands with the flag off.

The rewrite happens on the panel: the remote host only has to hand over the
file and take it back, so no `sed` heuristics run against operator config.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import posixpath
import re
import shlex
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CORE_CONFIG_PATH = "addons/counterstrikesharp/configs/core.json"
CORE_EXAMPLE_PATH = "addons/counterstrikesharp/configs/core.example.json"
GUIDELINES_KEY = "FollowCS2ServerGuidelines"
MAX_CONFIG_BYTES = 256 * 1024
ABSENT_MARKER = "__upkk_absent__"


class ConfigUnavailable(Exception):
    """core.json is present on the host but cannot be read back safely."""


# Each of these ships with CounterStrikeSharp itself and never with a plugin
# that merely drops files into ``addons/counterstrikesharp/plugins``.
RUNTIME_MARKERS = (
    "addons/counterstrikesharp/bin",
    "addons/counterstrikesharp/api",
    "addons/counterstrikesharp/dotnet",
    CORE_EXAMPLE_PATH,
    "addons/metamod/counterstrikesharp.vdf",
)

_REPO_RE = re.compile(
    r"github\.com[/:]roflmuffin/counterstrikesharp(?:\.git)?(?:$|[/?#])",
    re.IGNORECASE,
)
_GUIDELINES_TRUE_RE = re.compile(
    rf'("{GUIDELINES_KEY}"\s*:\s*)true',
    re.IGNORECASE,
)
_GUIDELINES_FALSE_RE = re.compile(
    rf'"{GUIDELINES_KEY}"\s*:\s*false',
    re.IGNORECASE,
)

# ``(command, timeout=...) -> (success, stdout, stderr)``
ExecuteCommand = Callable[..., Awaitable[tuple[bool, str, str]]]
Report = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class CoreConfigResult:
    """Outcome of one ``core.json`` reconciliation."""

    applied: bool
    message: str


def is_counterstrikesharp_repository(url: str | None) -> bool:
    """True when the URL points at the CounterStrikeSharp repository itself."""
    return bool(url) and _REPO_RE.search(str(url)) is not None


async def archive_is_counterstrikesharp(
    execute_command: ExecuteCommand,
    source_dir: str,
) -> bool:
    """True when the extracted archive carries the CounterStrikeSharp runtime."""
    tests = " || ".join(
        f"test -e {shlex.quote(posixpath.join(source_dir, marker))}" for marker in RUNTIME_MARKERS
    )
    success, stdout, _ = await execute_command(
        f"if {tests}; then printf yes; else printf no; fi", timeout=20
    )
    return success and stdout.strip() == "yes"


def guidelines_disabled(text: str) -> bool:
    """True when the given ``core.json`` already turns the flag off."""
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return (
            _GUIDELINES_FALSE_RE.search(text) is not None
            and _GUIDELINES_TRUE_RE.search(text) is None
        )
    return isinstance(data, dict) and data.get(GUIDELINES_KEY) is False


def disable_guidelines(text: str) -> tuple[str | None, str]:
    """Return ``core.json`` content with the guidelines flag turned off."""
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        # Operators sometimes leave comments or trailing commas in core.json.
        # Patch just the boolean rather than reformatting a file we cannot parse.
        patched, count = _GUIDELINES_TRUE_RE.subn(r"\1false", text)
        if count == 0:
            return None, f"core.json is not valid JSON and does not set {GUIDELINES_KEY}"
        return patched, "patched in place because core.json is not valid JSON"
    if not isinstance(data, dict):
        return None, "core.json does not contain a JSON object"
    data[GUIDELINES_KEY] = False
    return json.dumps(data, indent=4, ensure_ascii=False) + "\n", "rewritten"


async def _read_remote_text(execute_command: ExecuteCommand, path: str) -> str | None:
    """Return the file's text, or ``None`` when it does not exist.

    A file that exists but cannot be read whole raises instead of reporting
    "absent" — reporting absence would let the caller overwrite it.
    """
    quoted = shlex.quote(path)
    success, stdout, stderr = await execute_command(
        f"if [ -f {quoted} ]; then head -c {MAX_CONFIG_BYTES + 1} {quoted} "
        f"| base64 | tr -d '\\n'; else printf {ABSENT_MARKER}; fi",
        timeout=30,
    )
    if not success:
        raise ConfigUnavailable((stderr or stdout or f"could not read {path}").strip())
    payload = stdout.strip()
    if payload == ABSENT_MARKER:
        return None
    if not payload:
        return ""
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigUnavailable(f"{path} could not be transferred") from exc
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigUnavailable(f"{path} is larger than {MAX_CONFIG_BYTES} bytes")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigUnavailable(f"{path} is not UTF-8 text") from exc


async def _write_remote_text(
    execute_command: ExecuteCommand,
    path: str,
    content: str,
) -> tuple[bool, str]:
    payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
    quoted = shlex.quote(path)
    temporary = shlex.quote(f"{path}.upkk-{uuid.uuid4().hex[:8]}.tmp")
    command = (
        f"mkdir -p {shlex.quote(posixpath.dirname(path))} && "
        f"printf %s {shlex.quote(payload)} | base64 -d > {temporary} && "
        f"{{ chmod --reference={quoted} {temporary} 2>/dev/null || true; }} && "
        f"mv -f {temporary} {quoted}"
    )
    success, stdout, stderr = await execute_command(command, timeout=30)
    if success:
        return True, ""
    await execute_command(f"rm -f {temporary}", timeout=10)
    return False, (stderr or stdout or "write failed").strip()


async def apply_counterstrikesharp_core_defaults(
    execute_command: ExecuteCommand,
    csgo_dir: str,
    report: Report | None = None,
) -> CoreConfigResult:
    """Turn ``FollowCS2ServerGuidelines`` off, seeding core.json when absent.

    Never raises: a config that cannot be reconciled is reported as a warning
    because the install it follows already succeeded.
    """

    async def announce(message: str) -> None:
        if report is None:
            return
        try:
            await report(message)
        except Exception:  # pragma: no cover - progress must not break installs
            logger.debug("CounterStrikeSharp core.json progress callback failed")

    config_path = posixpath.join(csgo_dir, CORE_CONFIG_PATH)
    try:
        existing = await _read_remote_text(execute_command, config_path)
        if existing is not None and guidelines_disabled(existing):
            await announce(f"✓ {GUIDELINES_KEY} is already false in core.json")
            return CoreConfigResult(False, "already false")

        # A fresh install has no core.json yet; seed it from the shipped example
        # so the rest of CounterStrikeSharp's defaults are preserved.
        source = existing
        if not source:
            source = await _read_remote_text(
                execute_command, posixpath.join(csgo_dir, CORE_EXAMPLE_PATH)
            )
        updated, note = disable_guidelines(source or "{}")
        if updated is None:
            await announce(f"⚠ Left {GUIDELINES_KEY} unchanged: {note}")
            return CoreConfigResult(False, note)

        written, error = await _write_remote_text(execute_command, config_path, updated)
        if not written:
            await announce(f"⚠ Could not write core.json: {error}")
            return CoreConfigResult(False, error)

        created = " (core.json created)" if existing is None else ""
        await announce(f"✓ Set {GUIDELINES_KEY} to false in core.json{created}")
        return CoreConfigResult(True, note)
    except Exception as exc:
        logger.warning("CounterStrikeSharp core.json reconciliation failed: %s", exc)
        await announce(f"⚠ Could not set {GUIDELINES_KEY}: {exc}")
        return CoreConfigResult(False, str(exc))


async def maybe_apply_counterstrikesharp_core_defaults(
    execute_command: ExecuteCommand,
    *,
    csgo_dir: str,
    source_dir: str,
    repo_url: str | None = None,
    download_url: str | None = None,
    report: Report | None = None,
) -> CoreConfigResult:
    """Apply the defaults only when the install *was* CounterStrikeSharp."""
    recognized = is_counterstrikesharp_repository(repo_url) or is_counterstrikesharp_repository(
        download_url
    )
    if not recognized:
        try:
            recognized = await archive_is_counterstrikesharp(execute_command, source_dir)
        except Exception as exc:  # pragma: no cover - detection must not break installs
            logger.warning("CounterStrikeSharp archive detection failed: %s", exc)
            return CoreConfigResult(False, str(exc))
    if not recognized:
        return CoreConfigResult(False, "not a CounterStrikeSharp archive")
    return await apply_counterstrikesharp_core_defaults(execute_command, csgo_dir, report)
