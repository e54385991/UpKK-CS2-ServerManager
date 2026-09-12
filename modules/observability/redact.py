"""Best-effort redaction for monitoring summaries. No I/O."""

from __future__ import annotations

import re

SUMMARY_LIMIT = 512

_SECRET_LINE = re.compile(
    r"(?im)^(\s*(?:rcon_password|sv_password|password|secret|token|api[_-]?key|"
    r"webhook(?:_url)?|authorization)\s*[=:]\s*)([^\r\n]+)$"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_COMMON_TOKEN = re.compile(r"\b(?:gh[poushr]_|github_pat_)[A-Za-z0-9_]{8,}")
_SECRET_INLINE = re.compile(
    r"""(?ix)(["']?(?:rcon_password|sv_password|password|secret|token|api[_-]?key|"""
    r"""webhook(?:_url)?|authorization|ssh[_-]?(?:key|password))["']?\s*[:=]\s*["']?)"""
    r"""([^"'\s,}]+)"""
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_URL_QUERY = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)


def redact_summary(value: str, *, limit: int = SUMMARY_LIMIT) -> tuple[str, bool]:
    """Return a safe summary and whether it was truncated."""
    text = value.replace("\x00", "")
    text = _SECRET_LINE.sub(r"\1[REDACTED]", text)
    text = _SECRET_INLINE.sub(r"\1[REDACTED]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _COMMON_TOKEN.sub("[REDACTED_TOKEN]", text)
    text = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", text)
    text = _strip_query(text)
    truncated = len(text) > limit
    if truncated:
        text = text[:limit]
    return text, truncated


def _strip_query(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        url = match.group(1)
        head, sep, _query = url.partition("?")
        return head + ("?[REDACTED]" if sep else "")

    return _URL_QUERY.sub(replace, text)
