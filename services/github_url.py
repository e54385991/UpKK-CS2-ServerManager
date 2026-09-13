"""Canonical GitHub repository URL parsing shared by market, GitHub, and catalog flows."""

from __future__ import annotations

import re

GITHUB_REPO_PATTERN = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+?)(?:\.git)?(?:/.*)?$"
)


def parse_github_url(url: str) -> tuple[str, str]:
    """Extract ``(owner, repo)`` from https or ``git@`` GitHub repository URLs."""
    match = GITHUB_REPO_PATTERN.match(url)
    if not match:
        raise ValueError("Invalid GitHub repository URL format")
    return match.group(1), match.group(2)
