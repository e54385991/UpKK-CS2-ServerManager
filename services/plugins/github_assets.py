"""Validated, bounded transport for immutable GitHub release assets."""

from __future__ import annotations

import hashlib
import os
import tempfile
from urllib.parse import urljoin, urlsplit

import anyio
import httpx

DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


class GitHubPlanError(ValueError):
    """Raised when GitHub discovery or an immutable plan is invalid."""


def validate_download_url(value: str) -> None:
    parsed = urlsplit(value)
    try:
        has_port = parsed.port is not None
    except ValueError as exc:
        raise GitHubPlanError("Invalid GitHub release asset port") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or has_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "/releases/download/" not in parsed.path
    ):
        raise GitHubPlanError("Release asset URL is not a canonical GitHub download URL")


async def download_release_asset(url: str) -> tuple[str, str, int]:
    validate_download_url(url)
    temp = tempfile.NamedTemporaryFile(prefix="upkk-github-plan-", suffix=".archive", delete=False)
    path = temp.name
    temp.close()
    digest = hashlib.sha256()
    total = 0
    current = url
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            for _redirect in range(6):
                parsed = urlsplit(current)
                if (
                    parsed.scheme != "https"
                    or parsed.hostname not in DOWNLOAD_HOSTS
                    or parsed.port is not None
                    or parsed.username is not None
                    or parsed.password is not None
                ):
                    raise GitHubPlanError("GitHub release redirected to an unapproved host")
                async with client.stream(
                    "GET", current, headers={"User-Agent": "UpKK-CS2-ServerManager"}
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise GitHubPlanError(
                                "GitHub release redirect did not include a location"
                            )
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise GitHubPlanError(
                            f"Release asset download failed with HTTP {response.status_code}"
                        )
                    async with await anyio.open_file(path, "wb") as handle:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > MAX_ARCHIVE_BYTES:
                                raise GitHubPlanError("Release archive exceeds the 512 MiB limit")
                            digest.update(chunk)
                            await handle.write(chunk)
                    return path, digest.hexdigest(), total
            raise GitHubPlanError("GitHub release exceeded the redirect limit")
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
