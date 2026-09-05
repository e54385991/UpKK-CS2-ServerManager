"""Execstack target validation shared by request schemas and SSH services.

The newer-glibc patchelf fix is applied by :mod:`services.server_compatibility`,
but the list of plugin libraries it may touch is also validated when an
administrator saves a server. Keeping that pure policy here lets the schema
layer reuse it without importing a service, which the layer contract forbids.
"""

from __future__ import annotations

from typing import Any

DEFAULT_EXECSTACK_TARGETS = ("counterstrikesharp/bin/linuxsteamrt64/counterstrikesharp.so",)

MAX_EXECSTACK_TARGETS = 64


def normalize_execstack_targets(values: Any) -> tuple[str, ...]:
    """Validate relative ELF paths stored below the addons directory."""
    if values is None:
        return DEFAULT_EXECSTACK_TARGETS
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise ValueError("execstack targets must be a list of relative .so paths")
    cleaned: list[str] = []
    for value in values:
        target = str(value or "").strip()
        if (
            not target
            or target.startswith("/")
            or not target.endswith(".so")
            or any(part in {"", ".", ".."} for part in target.split("/"))
        ):
            raise ValueError("execstack targets must be relative .so paths")
        if target not in cleaned:
            cleaned.append(target)
    if not cleaned or len(cleaned) > MAX_EXECSTACK_TARGETS:
        raise ValueError("execstack targets must contain between 1 and 64 paths")
    return tuple(cleaned)
