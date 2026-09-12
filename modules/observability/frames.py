"""Capture a short, project-relative traceback without locals or source."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKIP_PARTS = {".venv", "site-packages", "dist-packages"}
MAX_FRAMES = 8


def capture_frames(
    exc: BaseException | None = None, *, limit: int = MAX_FRAMES
) -> list[dict[str, Any]]:
    """Return up to ``limit`` project frames: file, function, line."""
    if exc is not None:
        extracted = traceback.extract_tb(exc.__traceback__)
    else:
        extracted = traceback.extract_stack()[:-1]
    frames: list[dict[str, Any]] = []
    for item in extracted:
        relative = _project_path(item.filename)
        if relative is None:
            continue
        frames.append(
            {
                "file": relative,
                "function": item.name,
                "line": int(item.lineno or 0),
            }
        )
        if len(frames) >= limit:
            break
    return frames[-limit:]


def _project_path(filename: str) -> str | None:
    try:
        path = Path(filename).resolve()
        path.relative_to(_REPO_ROOT)
    except OSError, ValueError:
        return None
    if any(part in _SKIP_PARTS for part in path.parts):
        return None
    try:
        return path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return None
