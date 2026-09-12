"""Outbound HTTP classification helpers. No I/O."""

from __future__ import annotations

from .counters import record_outbound

GITHUB_API = "api.github.com"
GITHUB_HOST = "github.com"
DOWNLOAD_MARKERS = ("/releases/download/", "/archive/", "githubusercontent.com")


def classify_url(url: str) -> str:
    lower = url.lower()
    if GITHUB_API in lower:
        return "github"
    if any(marker in lower for marker in DOWNLOAD_MARKERS):
        return "download"
    if GITHUB_HOST in lower:
        return "github"
    return "other"


def record_outbound_http(
    url: str,
    duration_ms: float,
    *,
    group: str | None = None,
    status: int | None = None,
    timeout: bool = False,
    network: bool = False,
    retry: bool = False,
) -> None:
    record_outbound(
        group or classify_url(url),
        duration_ms,
        status=status,
        timeout=timeout,
        network=network,
        retry=retry,
    )
