"""
GitHub Plugin Installation routes
Provides endpoints for fetching GitHub releases and installing plugins from them
"""

import asyncio
import logging
import os
import posixpath
import re
import shlex
from typing import Optional
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import (
    ActiveUser,
    DatabaseSession,
    LockedServerOperation,
)
from modules import (
    ArchiveAnalysisResponse,
    ArchiveContentItem,
    GitHubInstallRecipeCreate,
    GitHubPluginInspectRequest,
    GitHubPluginInspectResponse,
    GitHubPluginInstallExecuteRequest,
    GitHubPluginInstallPlanRequest,
    GitHubPluginInstallPlanResponse,
    GitHubPluginInstallRequest,
    GitHubPluginInstallResponse,
    GitHubPluginSearchResponse,
    GitHubRelease,
    GitHubReleaseAsset,
    GitHubReleasesResponse,
    PluginUninstallRequest,
    PluginUninstallResponse,
    Server,
    User,
)
from modules.http_helper import http_helper
from services import SSHManager
from services.ai_access import AgentAccessDenied, enforce_agent_rate_limit
from services.github_credentials import get_effective_github_token
from services.github_plugin_plan_service import (
    GitHubPlanError,
    _archive_entries,
    _download_release_asset,
    _validate_download_url,
    _validate_release_contents,
    build_github_install_plan,
    create_install_recipe,
    execute_github_install_plan,
)
from services.github_plugin_plan_service import (
    inspect_github_plugin as inspect_github_plugin_service,
)
from services.github_plugin_plan_service import (
    search_github_plugins as search_github_plugins_service,
)

router = APIRouter(prefix="/api/github-plugins", tags=["github-plugins"])

logger = logging.getLogger(__name__)

# Regex to validate GitHub repository URL
GITHUB_REPO_PATTERN = re.compile(
    r"^https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)(?:/.*)?$"
)

# Progress update interval (percent) for panel proxy downloads/uploads
PROGRESS_UPDATE_INTERVAL = 10  # Update progress every 10%


async def _secure_archive_analysis(
    db: AsyncSession,
    user: User,
    server_id: int,
    download_url: str,
) -> ArchiveAnalysisResponse:
    """Inspect an official release asset locally with the plan-service limits."""
    from services.ai_access import authorized_server

    try:
        await authorized_server(db, user, server_id)
        await enforce_agent_rate_limit(user.id, "github_archive_analysis", limit=5)
        _validate_download_url(download_url)
        archive_path, _digest, compressed_size = await _download_release_asset(download_url)
        try:
            asset_name = unquote(posixpath.basename(urlsplit(download_url).path))
            try:
                entries = await asyncio.to_thread(
                    _archive_entries,
                    archive_path,
                    asset_name,
                    compressed_size,
                )
            except GitHubPlanError:
                raise
            except Exception as exc:
                raise GitHubPlanError("Release archive could not be safely inspected") from exc
            _validate_release_contents(entries)
        finally:
            try:
                os.unlink(archive_path)
            except OSError:
                pass
    except (AgentAccessDenied, GitHubPlanError) as exc:
        return ArchiveAnalysisResponse(success=False, error=str(exc))

    directories: set[str] = set()
    files: list[ArchiveContentItem] = []
    top_level: dict[str, bool] = {}
    for item in entries:
        path = item["path"]
        parts = path.split("/")
        top_level[parts[0]] = top_level.get(parts[0], False) or len(parts) > 1 or item["is_dir"]
        for index in range(1, len(parts)):
            directories.add("/".join(parts[:index]))
        if item["is_dir"]:
            directories.add(path)
        else:
            files.append(ArchiveContentItem(path=path, is_dir=False, size=item["size"]))
    root_dirs = sorted({path.split("/", 1)[0] for path in directories})
    lowered = asset_name.casefold()
    archive_type = next(
        (
            extension.lstrip(".")
            for extension in (".tar.gz", ".tgz", ".tar", ".zip", ".7z")
            if lowered.endswith(extension)
        ),
        None,
    )
    return ArchiveAnalysisResponse(
        success=True,
        has_addons_dir=any(
            item["path"] == "addons" or item["path"].startswith("addons/") for item in entries
        ),
        root_dirs=root_dirs,
        all_dirs=sorted(directories),
        all_files=files,
        top_level_items=[
            ArchiveContentItem(path=path, is_dir=is_dir)
            for path, is_dir in sorted(top_level.items())
        ],
        archive_type=archive_type,
    )


def _safe_github_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentAccessDenied):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/search", response_model=GitHubPluginSearchResponse)
async def search_github_cs2_plugins(
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=3, ge=1, le=3),
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    try:
        await enforce_agent_rate_limit(current_user.id, "github_search", limit=10)
        return await search_github_plugins_service(db, current_user, q, limit=limit)
    except (AgentAccessDenied, GitHubPlanError) as exc:
        raise _safe_github_error(exc) from exc


@router.post("/inspect", response_model=GitHubPluginInspectResponse)
async def inspect_github_plugin(
    request: GitHubPluginInspectRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    try:
        await enforce_agent_rate_limit(current_user.id, "github_inspect", limit=15)
        return await inspect_github_plugin_service(db, current_user, request.repo_url, request.mode)
    except (AgentAccessDenied, GitHubPlanError) as exc:
        raise _safe_github_error(exc) from exc


@router.post("/servers/{server_id}/install-plan", response_model=GitHubPluginInstallPlanResponse)
async def plan_github_plugin_install(
    server_id: int,
    request: GitHubPluginInstallPlanRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    try:
        await enforce_agent_rate_limit(current_user.id, "github_plan", limit=5)
        return await build_github_install_plan(db, current_user, server_id, request)
    except (AgentAccessDenied, GitHubPlanError) as exc:
        raise _safe_github_error(exc) from exc


@router.post("/servers/{server_id}/install-plan/execute")
async def apply_github_plugin_install(
    server_id: int,
    request: GitHubPluginInstallExecuteRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    try:
        await enforce_agent_rate_limit(
            current_user.id, "github_install", limit=2, window_seconds=300
        )
        plan_request = GitHubPluginInstallPlanRequest.model_validate(
            request.model_dump(
                exclude={
                    "expected_plan_hash",
                    "acknowledge_warning_rule_ids",
                    "acknowledge_unknown_compatibility",
                }
            )
        )
        return await execute_github_install_plan(
            db,
            current_user,
            server_id,
            plan_request,
            request.expected_plan_hash,
            set(request.acknowledge_warning_rule_ids),
            request.acknowledge_unknown_compatibility,
        )
    except (AgentAccessDenied, GitHubPlanError) as exc:
        raise _safe_github_error(exc) from exc


@router.post("/recipes")
async def create_github_install_recipe(
    request: GitHubInstallRecipeCreate,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> dict:
    try:
        recipe = await create_install_recipe(db, current_user, request.model_dump())
        return {
            "id": recipe.id,
            "repo_url": recipe.repo_url,
            "revision": recipe.revision,
            "source_prefix": recipe.source_prefix,
            "target_prefix": recipe.target_prefix,
        }
    except (PermissionError, GitHubPlanError) as exc:
        raise _safe_github_error(exc) from exc


def _build_plugin_copy_command(
    source_dir: str,
    target_dir: str,
    exclude_patterns: list[str],
    *,
    use_rsync: bool,
) -> str:
    """Build a plugin copy command while always refreshing gamedata files.

    Upgrade exclusions intentionally preserve user configuration files by
    extension.  Framework gamedata often uses those same extensions (for
    example CounterStrikeSharp's ``gamedata.json`` and CS2Fixes'
    ``cs2fixes.jsonc``), so a final, unconditional copy of every file below a
    ``gamedata`` directory is required after the filtered copy.
    """
    safe_source = shlex.quote(source_dir)
    safe_target = shlex.quote(target_dir)

    if use_rsync:
        exclusions = "".join(f" --exclude={shlex.quote(pattern)}" for pattern in exclude_patterns)
        primary_copy = f"rsync -av{exclusions} {safe_source}/ {safe_target}/"
    elif exclude_patterns:
        exclusions = " ".join(f"--exclude={shlex.quote(pattern)}" for pattern in exclude_patterns)
        primary_copy = f"cd {safe_source} && tar {exclusions} -cf - . | tar -xf - -C {safe_target}"
    else:
        primary_copy = f"cp -r {safe_source}/. {safe_target}/"

    # GNU find's batched -exec form propagates a non-zero copy status.  Rebuild
    # each archive-relative parent path below the destination, then force the
    # file copy so gamedata is immune to every upgrade/manual exclusion rule.
    copy_script = (
        'target="$1"; shift; '
        "for source do "
        'relative=${source#./}; destination="$target/$relative"; '
        'parent=${destination%/*}; mkdir -p "$parent" '
        "&& cp -a --no-dereference --remove-destination -- "
        '"$source" "$destination" || exit 1; '
        "done"
    )
    gamedata_copy = (
        f"cd {safe_source} && "
        "find . -path '*/gamedata/*' -type f "
        f"-exec sh -c {shlex.quote(copy_script)} sh {safe_target} {{}} +"
    )
    return f"{primary_copy} && {gamedata_copy}"


def parse_github_url(url: str) -> tuple[str, str]:
    """
    Parse GitHub repository URL to extract owner and repo name.

    Args:
        url: GitHub repository URL (e.g., https://github.com/Source2ZE/CS2Fixes)

    Returns:
        Tuple of (owner, repo_name)

    Raises:
        ValueError: If URL is invalid
    """
    match = GITHUB_REPO_PATTERN.match(url)
    if not match:
        raise ValueError("Invalid GitHub repository URL format")
    return match.group(1), match.group(2)


async def get_server_and_verify_ownership(db: AsyncSession, server_id: int, user: User) -> Server:
    """
    Get server by ID and verify user ownership.
    Admins can access any server, regular users can only access their own.
    Raises HTTPException if server not found or user doesn't have access.
    """
    if user.is_admin:
        server = await Server.get_by_id(db, server_id)
    else:
        server = await Server.get_by_id_and_user(db, server_id, user.id)

    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    await db.commit()
    return server


@router.get("/releases")
async def get_github_releases(
    repo_url: str,
    count: int = 5,
    server_id: Optional[int] = None,
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> GitHubReleasesResponse:
    """
    Fetch recent releases from a GitHub repository.

    Args:
        repo_url: GitHub repository URL (e.g., https://github.com/Source2ZE/CS2Fixes)
        count: Number of releases to fetch (default: 5, max: 10)
        server_id: Optional server ID to use server's GitHub proxy configuration

    Returns:
        List of releases with their assets
    """
    try:
        owner, repo = parse_github_url(repo_url)
    except ValueError as e:
        return GitHubReleasesResponse(success=False, error=str(e), releases=[])

    # A server context is authorization-only. GitHub credentials are never sent
    # through a user-configured proxy.
    github_proxy = None
    server = None
    if server_id:
        from services.ai_access import authorized_server

        server = await authorized_server(db, current_user, server_id)

    linux_runtime_profile: dict[str, object] | None = None
    if server is not None:
        from services.linux_runtime_service import detect_linux_runtime_profile

        linux_runtime_profile = await detect_linux_runtime_profile(server)

    # Limit count to prevent abuse
    count = min(count, 10)

    # Fetch releases from GitHub API
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"}

    # Prefer the user's token and use the system credential only as a fallback.
    github_token = await get_effective_github_token(db, current_user)

    success, data, error = await http_helper.get(
        api_url,
        headers=headers,
        params={"per_page": count},
        timeout=30,
        proxy=github_proxy,
        github_token=github_token,
    )

    if not success:
        return GitHubReleasesResponse(
            success=False,
            error=f"Failed to fetch releases: {error}",
            releases=[],
            repo_owner=owner,
            repo_name=repo,
        )

    if not isinstance(data, list):
        return GitHubReleasesResponse(
            success=False,
            error="Unexpected response format from GitHub API",
            releases=[],
            repo_owner=owner,
            repo_name=repo,
        )

    # Parse releases
    releases: list[GitHubRelease] = []
    for raw_release in data:
        if not isinstance(raw_release, dict):
            continue
        release_data: dict[str, object] = raw_release
        if release_data.get("draft") or release_data.get("prerelease"):
            continue
        asset_payloads = []
        raw_assets = release_data.get("assets", [])
        assets_data = raw_assets if isinstance(raw_assets, list) else []
        for raw_asset in assets_data:
            if not isinstance(raw_asset, dict):
                continue
            asset_data: dict[str, object] = raw_asset
            asset_name = str(asset_data.get("name") or "")
            asset_name_lower = asset_name.lower()

            # Skip Windows-specific archives (filename contains 'windows' or 'win')
            if (
                "windows" in asset_name_lower
                or "-win-" in asset_name_lower
                or "_win_" in asset_name_lower
                or asset_name_lower.endswith("-win.zip")
            ):
                continue

            # Only include archive files that could be plugins (including 7z)
            if any(
                asset_name_lower.endswith(ext) for ext in [".zip", ".tar.gz", ".tgz", ".tar", ".7z"]
            ):
                asset_payloads.append(
                    {
                        "name": asset_name,
                        "browser_download_url": asset_data.get("browser_download_url", ""),
                        "size": asset_data.get("size", 0),
                        "content_type": asset_data.get("content_type"),
                    }
                )

        from services.linux_runtime_service import annotate_runtime_assets

        assets = [
            GitHubReleaseAsset.model_validate(item)
            for item in annotate_runtime_assets(asset_payloads, linux_runtime_profile)
        ]

        # Only include releases that have downloadable assets
        if assets:
            releases.append(
                GitHubRelease(
                    id=str(release_data.get("id") or ""),
                    tag_name=release_data.get("tag_name", ""),
                    name=release_data.get("name"),
                    published_at=release_data.get("published_at"),
                    prerelease=release_data.get("prerelease", False),
                    assets=assets,
                )
            )
            if len(releases) >= count:
                break

    from modules import LinuxRuntimeProfile

    return GitHubReleasesResponse(
        success=True,
        releases=releases,
        repo_owner=owner,
        repo_name=repo,
        linux_runtime_profile=(
            LinuxRuntimeProfile.model_validate(linux_runtime_profile)
            if linux_runtime_profile is not None
            else None
        ),
    )


@router.get("/servers/{server_id}/analyze-archive")
async def analyze_archive(
    server_id: int,
    download_url: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ArchiveAnalysisResponse:
    """
    Download and analyze archive contents to detect structure.

    This helps determine:
    - If archive has an addons/ directory (proper CS2 plugin structure)
    - What directories are at the root level
    - Archive type (zip, tar.gz, etc.)

    Args:
        server_id: Server ID for SSH connection
        download_url: Direct download URL for the archive

    Returns:
        Analysis of archive contents
    """
    return await _secure_archive_analysis(db, current_user, server_id, download_url)


@router.post("/servers/{server_id}/install")
async def install_github_plugin(
    server_id: int,
    request: GitHubPluginInstallRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> GitHubPluginInstallResponse:
    """
    Install a plugin from a GitHub release asset with WebSocket progress updates.

    This endpoint:
    1. Downloads the archive from GitHub
    2. Analyzes the archive structure (must contain addons/ directory)
    3. Extracts files to the CS2 game directory
    4. Optionally excludes specified directories (for updates)

    Args:
        server_id: Server ID
        request: Installation request with download URL and options

    Returns:
        Installation result
    """
    if not request.repo_url or not request.installation_plan_hash:
        return GitHubPluginInstallResponse(
            success=False,
            message=(
                "A current GitHub install plan and plan hash are required; use the "
                "install-plan endpoint before confirming"
            ),
        )
    plan_request = GitHubPluginInstallPlanRequest(
        repo_url=request.repo_url,
        mode=request.install_mode,
        asset_name=request.asset_name,
        config_policy=request.config_policy,
    )
    try:
        result = await execute_github_install_plan(
            db,
            current_user,
            server_id,
            plan_request,
            request.installation_plan_hash,
            set(request.acknowledge_warning_rule_ids),
            request.acknowledge_unknown_compatibility,
        )
    except (AgentAccessDenied, GitHubPlanError) as exc:
        return GitHubPluginInstallResponse(success=False, message=str(exc))
    return GitHubPluginInstallResponse(
        success=bool(result.get("success")),
        message=str(result.get("message") or "GitHub installation finished"),
        installed_files=int(result.get("installed_files") or 0),
    )


@router.get("/servers/{server_id}/analyze-installed-plugins")
async def analyze_installed_plugins(
    server_id: int,
    directory: str = "addons",
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """
    Analyze installed plugin files to help users select which files to uninstall.

    Args:
        server_id: Server ID
        directory: Directory to analyze (default: addons, relative to csgo directory)

    Returns:
        List of installed files and directories
    """
    from modules import InstalledPluginAnalysisResponse, InstalledPluginFile

    server = await get_server_and_verify_ownership(db, server_id, current_user)

    ssh_manager = SSHManager()
    success, msg = await ssh_manager.connect(server)
    if not success:
        return InstalledPluginAnalysisResponse(success=False, error=f"SSH connection failed: {msg}")

    try:
        # Sanitize directory input
        safe_dir = directory.strip().strip("/")
        if ".." in safe_dir or safe_dir.startswith("/"):
            return InstalledPluginAnalysisResponse(success=False, error="Invalid directory path")

        csgo_dir = f"{server.game_directory}/cs2/game/csgo"
        target_dir = f"{csgo_dir}/{safe_dir}"

        # Check if directory exists
        check_cmd = f"test -d {target_dir} && echo 'exists'"
        success, output, _ = await ssh_manager.execute_command(check_cmd)

        if "exists" not in output:
            return InstalledPluginAnalysisResponse(
                success=False, error=f"Directory {safe_dir} does not exist"
            )

        # List all files and directories with sizes
        # Use find to get all files and directories recursively
        list_cmd = f"cd {target_dir} && find . -type f -exec ls -l {{}} \\; 2>/dev/null | awk '{{print $5 \" \" $9}}' || find . -type f 2>/dev/null"
        success, output, stderr = await ssh_manager.execute_command(list_cmd, timeout=30)

        if not success:
            return InstalledPluginAnalysisResponse(
                success=False, error=f"Failed to list files: {stderr}"
            )

        files = []
        total_size = 0

        if output.strip():
            for line in output.strip().split("\n"):
                line = line.strip()
                if not line or line == ".":
                    continue

                # Try to parse size and path
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    size = int(parts[0])
                    path = parts[1].strip().lstrip("./")
                else:
                    # Fallback if no size info
                    size = 0
                    path = line.lstrip("./")

                if path:
                    # Make path relative to csgo directory
                    full_path = f"{safe_dir}/{path}"
                    files.append(InstalledPluginFile(path=full_path, size=size, is_dir=False))
                    total_size += size

        # Also get directories
        dir_cmd = f"cd {target_dir} && find . -type d 2>/dev/null | grep -v '^\\.\\?$' || echo ''"
        success, dir_output, _ = await ssh_manager.execute_command(dir_cmd, timeout=30)

        if success and dir_output.strip():
            for line in dir_output.strip().split("\n"):
                path = line.strip().lstrip("./")
                if path:
                    full_path = f"{safe_dir}/{path}"
                    # Only add if not already in files list
                    if not any(f.path == full_path for f in files):
                        files.append(InstalledPluginFile(path=full_path, size=0, is_dir=True))

        return InstalledPluginAnalysisResponse(success=True, files=files, total_size=total_size)

    except Exception as e:
        logger.error(f"Error analyzing installed plugins: {e}")
        return InstalledPluginAnalysisResponse(
            success=False, error=f"Error analyzing plugins: {str(e)}"
        )
    finally:
        await ssh_manager.disconnect()


@router.post("/servers/{server_id}/uninstall", response_model=PluginUninstallResponse)
async def uninstall_plugin(
    server_id: int,
    request: PluginUninstallRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
    _operation_server: LockedServerOperation,
) -> PluginUninstallResponse:
    """
    Uninstall a plugin by deleting selected files.

    Args:
        server_id: Server ID
        request: Uninstall request with list of files to delete

    Returns:
        Uninstallation result
    """
    from services.deployment_progress import send_deployment_update

    server = await get_server_and_verify_ownership(db, server_id, current_user)

    async def progress(msg: str, msg_type: str = "status"):
        """Send progress update via WebSocket"""
        await send_deployment_update(server_id, msg_type, msg)

    ssh_manager = SSHManager()
    success, msg = await ssh_manager.connect(server)
    if not success:
        await progress(f"SSH connection failed: {msg}", "error")
        return PluginUninstallResponse(success=False, message=f"SSH connection failed: {msg}")

    try:
        await progress("Starting plugin uninstallation...")

        csgo_dir = f"{server.game_directory}/cs2/game/csgo"
        deleted_count = 0
        failed_files = []

        for file_path in request.files_to_delete:
            # Build absolute path
            full_path = f"{csgo_dir}/{file_path}"

            # Delete file or directory
            delete_cmd = f"rm -rf '{full_path}'"
            success, _, stderr = await ssh_manager.execute_command(delete_cmd)

            if success:
                deleted_count += 1
                await progress(f"Deleted: {file_path}")
            else:
                failed_files.append(file_path)
                await progress(f"Failed to delete: {file_path} - {stderr}", "warning")

        if failed_files:
            message = f"Uninstallation completed with errors. Deleted {deleted_count} files, failed {len(failed_files)} files."
            await progress(message, "warning")
        else:
            message = f"Successfully uninstalled plugin. Deleted {deleted_count} files."
            await progress(message, "complete")

        return PluginUninstallResponse(
            success=len(failed_files) == 0,
            message=message,
            deleted_files=deleted_count,
            failed_files=failed_files,
        )

    except Exception as e:
        logger.error(f"Error uninstalling plugin: {e}")
        error_msg = f"Uninstallation error: {str(e)}"
        await progress(error_msg, "error")
        return PluginUninstallResponse(success=False, message=error_msg)
    finally:
        await ssh_manager.disconnect()
