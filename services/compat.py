"""Call-time module lookups so facades stay monkeypatch-compatible after splits."""

from __future__ import annotations

import io
import tokenize
from importlib import import_module
from typing import Any, Iterable


class LateBoundModule:
    """Resolve attributes on another module at call time.

    Split implementations must not capture facade names at import time.
    Tests and callers patch the public module (``services.ai_tools``,
    ``api.routes.plugin_market``, ``api.routes.map_management``, …); looking
    the name up here keeps that contract without duplicating a host class
    in every package.
    """

    def __init__(self, module_name: str) -> None:
        self._module_name = module_name

    def __getattr__(self, name: str) -> Any:
        return getattr(import_module(self._module_name), name)


def rewrite_facade_lookups(source: str, names: Iterable[str]) -> str:
    """Prefix patchable names with ``host.`` while preserving original layout.

    Splitters use this so domain modules keep call-time lookups instead of
    binding ``from facade import name`` at import time. Function and class
    definitions and assignment targets are left alone; already-qualified
    names are not rewritten.
    """

    host_names = set(names)
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    replacements: list[tuple[tuple[int, int], tuple[int, int]]] = []
    ignored = {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.COMMENT,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
    }
    previous = ""
    for index, token in enumerate(tokens):
        if (
            token.type == tokenize.NAME
            and token.string in host_names
            and previous not in {".", "def", "class"}
        ):
            following = next(
                (item for item in tokens[index + 1 :] if item.type not in ignored),
                None,
            )
            if following is not None and following.string == "=":
                previous = token.string
                continue
            replacements.append((token.start, token.end))
        previous = token.string

    lines = source.splitlines(keepends=True)
    for (start_line, start_col), (_end_line, end_col) in reversed(replacements):
        line = lines[start_line - 1]
        lines[start_line - 1] = f"{line[:start_col]}host.{line[start_col:end_col]}{line[end_col:]}"
    return "".join(lines)
