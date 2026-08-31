"""Token-level upsert for CS2 additional startup parameters."""

from __future__ import annotations

import re
import shlex

from modules.server_startup import normalize_additional_parameters

_OPTION_NAME = re.compile(r"[+-][A-Za-z_][A-Za-z0-9_]*")


def upsert_additional_parameters(existing: str | None, upserts: dict[str, str]) -> str | None:
    """Replace or append named +/− options, then normalize the result."""
    if not upserts:
        return normalize_additional_parameters(existing)

    tokens = shlex.split(existing, posix=True) if existing and existing.strip() else []
    folded = {key.casefold(): (key, str(value)) for key, value in upserts.items()}
    seen: set[str] = set()
    rewritten: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _OPTION_NAME.fullmatch(token):
            key = token.casefold()
            value: str | None = None
            if index + 1 < len(tokens) and _OPTION_NAME.fullmatch(tokens[index + 1]) is None:
                value = tokens[index + 1]
                index += 2
            else:
                index += 1
            if key in folded:
                canonical, replacement = folded[key]
                rewritten.append(canonical)
                rewritten.append(replacement)
                seen.add(key)
            else:
                rewritten.append(token)
                if value is not None:
                    rewritten.append(value)
            continue
        rewritten.append(token)
        index += 1

    for key, value in upserts.items():
        if key.casefold() not in seen:
            rewritten.append(key)
            rewritten.append(str(value))

    if not rewritten:
        return None
    return normalize_additional_parameters(shlex.join(rewritten))
