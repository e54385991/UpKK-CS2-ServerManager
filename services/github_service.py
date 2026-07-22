"""
GitHub API Service
Provides functionality to fetch repository metadata from GitHub API
"""

import logging
import re
from typing import Tuple

from modules.http_helper import http_helper

logger = logging.getLogger(__name__)

# Regex to parse GitHub repository URL
GITHUB_REPO_PATTERN = re.compile(
    r"^https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)(?:/.*)?$"
)


def parse_github_url(url: str) -> Tuple[str, str]:
    """
    Parse GitHub repository URL to extract owner and repo name.

    Args:
        url: GitHub repository URL (e.g., https://github.com/owner/repo)

    Returns:
        Tuple of (owner, repo_name)

    Raises:
        ValueError: If URL is invalid
    """
    match = GITHUB_REPO_PATTERN.match(url)
    if not match:
        raise ValueError("Invalid GitHub repository URL format")
    return match.group(1), match.group(2)


async def fetch_github_repo_info(github_url: str) -> dict:
    """
    Fetch repository information from GitHub API.

    Args:
        github_url: GitHub repository URL (e.g., https://github.com/owner/repo)

    Returns:
        Dictionary containing:
            - name: Repository name (slug format)
            - display_name: Repository full name
            - description: Repository description
            - author: Repository owner/organization name
            - topics: List of repository topics (tags)
            - html_url: Repository URL

    Raises:
        ValueError: If URL is invalid or API request fails
    """
    try:
        # Parse the GitHub URL
        owner, repo = parse_github_url(github_url)

        # Build GitHub API URL
        api_url = f"https://api.github.com/repos/{owner}/{repo}"

        logger.info(f"Fetching repository info from GitHub API: {api_url}")

        # Make request to GitHub API
        success, data, error = await http_helper.get(
            url=api_url,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            timeout=30,
        )

        if not success:
            error_msg = f"Failed to fetch repository info: {error}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if not data:
            raise ValueError("Empty response from GitHub API")

        # Extract relevant information
        # Use repo name as the unique identifier (slug format)
        repo_name = data.get("name", "").lower()

        # Get full name for display (e.g., "CounterStrikeSharp")
        display_name = data.get("name", repo_name)

        # Get description
        description = data.get("description", "")
        if not description:
            description = f"{display_name} - GitHub repository"

        # Get owner/author
        author = data.get("owner", {}).get("login", owner)

        # Get topics (tags)
        topics = data.get("topics", [])

        # Get HTML URL (should be same as input, but normalized)
        html_url = data.get("html_url", github_url)

        # Get language
        language = data.get("language", "")

        # Get stars count (could be useful for popularity)
        stars = data.get("stargazers_count", 0)

        logger.info(f"Successfully fetched info for {owner}/{repo}: {display_name}")

        return {
            "name": f"{owner.lower()}-{repo_name}",  # Unique identifier: owner-repo
            "display_name": display_name,
            "description": description,
            "short_description": description[:500] if len(description) > 500 else description,
            "author": author,
            "topics": topics,
            "html_url": html_url,
            "language": language,
            "stars": stars,
        }

    except ValueError:
        raise
    except Exception as e:
        error_msg = f"Unexpected error fetching repository info: {str(e)}"
        logger.error(error_msg)
        raise ValueError(error_msg) from e


def determine_category(repo_info: dict) -> str:
    """
    Determine plugin category based on repository information.

    Args:
        repo_info: Repository information from fetch_github_repo_info

    Returns:
        Category string (功能, 依赖, or 娱乐)
    """
    # Default category
    default_category = "功能"

    # Check topics and description for keywords
    topics = [t.lower() for t in repo_info.get("topics", [])]
    description = repo_info.get("description", "").lower()
    name = repo_info.get("name", "").lower()

    # Keywords for different categories
    dependency_keywords = [
        "metamod",
        "sourcemod",
        "counterstrikesharp",
        "cs2fixes",
        "library",
        "framework",
        "api",
        "core",
        "base",
        "sdk",
    ]

    functionality_keywords = [
        "admin",
        "management",
        "manager",
        "server",
        "tool",
        "utility",
        "monitor",
        "stats",
        "ranking",
        "rank",
    ]

    entertainment_keywords = [
        "fun",
        "game",
        "mini",
        "event",
        "bhop",
        "surf",
        "kz",
        "climb",
        "jump",
        "race",
        "arena",
        "deathmatch",
        "entertainment",
        "娱乐",
    ]

    # Check if it's a dependency (highest priority)
    for keyword in dependency_keywords:
        if keyword in topics or keyword in description or keyword in name:
            return "依赖"

    # Check if it's functionality (admin/utility tools - check before entertainment)
    for keyword in functionality_keywords:
        if keyword in topics or keyword in description or keyword in name:
            return "功能"

    # Check if it's entertainment (lowest priority for automatic detection)
    for keyword in entertainment_keywords:
        if keyword in topics or keyword in description or keyword in name:
            return "娱乐"

    # Default to functionality
    return default_category
