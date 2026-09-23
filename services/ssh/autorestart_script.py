"""Keep the remote CS2 auto-restart wrapper aligned with the panel copy."""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path

LOCAL_AUTORESTART_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cs2_autorestart.sh"

CommandRunner = Callable[..., Awaitable[tuple[bool, str, str]]]


def local_autorestart_script_text() -> str:
    """Return the repo wrapper, always ending in a newline so the remote file matches."""
    text = LOCAL_AUTORESTART_SCRIPT.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    return text


def script_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _remote_digest(stdout: str) -> str:
    token = stdout.split()
    if not token or len(token[0]) != 64:
        return ""
    return token[0]


async def ensure_autorestart_script(
    execute_command: CommandRunner,
    remote_path: str,
) -> tuple[bool, str]:
    """Upload the repo wrapper when the remote file is missing or different.

    ``ready`` is true when a script is on the host, including an older copy that
    could not be refreshed. ``status`` is ``current``, ``updated``, ``deployed``,
    or an error message.
    """
    quoted = shlex.quote(remote_path)
    body = local_autorestart_script_text()
    expected = script_digest(body)
    success, stdout, _stderr = await execute_command(f"sha256sum {quoted}", timeout=10)
    remote = _remote_digest(stdout) if success else ""
    if remote == expected:
        return True, "current"

    command = f"cat > {quoted} << 'EOFSCRIPT'\n{body}EOFSCRIPT\nchmod +x {quoted}"
    wrote, _stdout, stderr = await execute_command(command, timeout=10)
    if wrote:
        return True, "updated" if remote else "deployed"
    if remote:
        detail = stderr.strip() or "remote script was left unchanged"
        return True, f"refresh failed: {detail}"
    return False, stderr.strip() or "could not deploy autorestart script"
