"""Memory-bounded capture for long-running streamed command output."""

from __future__ import annotations

from collections import deque


def truncate_utf8_tail(
    text: str,
    max_bytes: int,
    *,
    marker: str = "[... earlier output truncated ...]",
) -> str:
    """Return text unchanged or a marked UTF-8 tail within ``max_bytes``."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")

    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text

    marker_bytes = marker.encode("utf-8")
    if len(marker_bytes) >= max_bytes:
        return marker_bytes[:max_bytes].decode("utf-8", errors="ignore")

    tail_budget = max_bytes - len(marker_bytes) - 1
    tail = encoded[-tail_budget:].decode("utf-8", errors="ignore") if tail_budget else ""
    return f"{marker}\n{tail}"


class BoundedLineBuffer:
    """Retain the newest complete output lines within a byte budget."""

    TRUNCATION_MARKER = "[... earlier output truncated ...]"

    def __init__(self, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")
        self.max_bytes = max_bytes
        self._lines: deque[tuple[str, int]] = deque()
        self._retained_bytes = 0
        self.truncated = False

    def append(self, line: str) -> None:
        encoded = line.encode("utf-8", errors="replace")
        if len(encoded) + 1 > self.max_bytes:
            # Keep a valid UTF-8 tail even when one tool emits a pathological
            # line with no newline for longer than the entire capture budget.
            tail_budget = self.max_bytes - 1
            tail = encoded[-tail_budget:] if tail_budget else b""
            line = tail.decode("utf-8", errors="ignore")
            encoded = line.encode("utf-8")
            self._lines.clear()
            self._retained_bytes = 0
            self.truncated = True

        line_bytes = len(encoded) + 1
        while self._lines and self._retained_bytes + line_bytes > self.max_bytes:
            _, removed_bytes = self._lines.popleft()
            self._retained_bytes -= removed_bytes
            self.truncated = True

        self._lines.append((line, line_bytes))
        self._retained_bytes += line_bytes

    def clear(self) -> None:
        self._lines.clear()
        self._retained_bytes = 0
        self.truncated = False

    def text(self) -> str:
        retained = "\n".join(line for line, _ in self._lines)
        if not self.truncated:
            return retained
        if not retained:
            return self.TRUNCATION_MARKER
        return f"{self.TRUNCATION_MARKER}\n{retained}"
