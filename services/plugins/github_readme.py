"""Decode a GitHub README payload into marketplace description copy."""

from __future__ import annotations

import base64
import binascii
import logging

logger = logging.getLogger(__name__)

# Matches MarketPluginCreateRequest.description, so an auto-filled README can be
# submitted back unchanged.
README_MAX_CHARS = 10000
README_EXCERPT_CHARS = 200


def decode_readme(content: str) -> str | None:
    """Decode the base64 README the GitHub contents API returns.

    Returns the full Markdown, bounded by the description column, so the
    console can render it. Returns ``None`` when there is nothing usable.
    """
    if not content:
        return None
    try:
        decoded = base64.b64decode(content).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        logger.warning(f"Failed to decode README: {exc}")
        return None
    text = decoded.replace("\r\n", "\n").strip()
    if not text:
        return None
    return text[:README_MAX_CHARS]


def readme_excerpt(readme: str) -> str:
    """Flatten a README into one line for callers that cannot render Markdown."""
    lines = [
        line.strip()
        for line in readme.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    return " ".join(lines)[:README_EXCERPT_CHARS]
