"""Fetch the non-secret GitHub metadata used to fill a marketplace listing."""

from __future__ import annotations

import logging
from typing import Optional

from modules.http_helper import http_helper
from modules.schemas.plugins import GitHubRepoInfo
from services.github_service import parse_github_url
from services.plugins.github_readme import decode_readme, readme_excerpt

logger = logging.getLogger(__name__)


async def fetch_github_repo_info(
    github_url: str, github_proxy: Optional[str] = None, github_token: Optional[str] = None
) -> GitHubRepoInfo:
    """
    Fetch repository information from GitHub API.

    Args:
        github_url: GitHub repository URL
        github_proxy: Optional GitHub proxy URL
        github_token: Optional GitHub personal access token for authentication

    Returns:
        GitHubRepoInfo with parsed data
    """
    try:
        owner, repo = parse_github_url(github_url)
    except ValueError as e:
        return GitHubRepoInfo(success=False, error=str(e))

    # Fetch repo info from GitHub API
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"}

    success, data, error = await http_helper.get(
        api_url, headers=headers, timeout=30, proxy=github_proxy, github_token=github_token
    )

    if not success or not isinstance(data, dict):
        return GitHubRepoInfo(success=False, error=f"Failed to fetch repository info: {error}")

    # Extract repo name and description
    repo_name = data.get("name", repo)
    description = data.get("description", "")

    # Fetch the README so the console can offer the full long-form Markdown.
    readme_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    readme_success, readme_data, _ = await http_helper.get(
        readme_url, headers=headers, timeout=30, proxy=github_proxy, github_token=github_token
    )

    readme: Optional[str] = None
    if readme_success and isinstance(readme_data, dict):
        readme = decode_readme(readme_data.get("content", ""))

    if not description and readme:
        # No repository description: fall back to a short README excerpt so the
        # legacy form still lands something usable in its single-line field.
        description = readme_excerpt(readme)

    return GitHubRepoInfo(
        success=True,
        repo_name=repo_name,
        description=description if description else None,
        readme=readme,
        author=owner,
    )
