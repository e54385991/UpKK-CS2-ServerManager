"""
GitHub Plugin Installation routes
Provides endpoints for fetching GitHub releases and installing plugins from them
"""

import logging
import re
import shlex
from typing import Any, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_ssh_manager, locked_server_operation
from api.http_resource import (
    ApplicationHTTP,
    as_application_http,
    resolve_application_http,
)
from cs2_manager.core import ErrorResponse
from modules import (
    ArchiveAnalysisResponse,
    ArchiveContentItem,
    GitHubPluginInstallRequest,
    GitHubPluginInstallResponse,
    GitHubRelease,
    GitHubReleaseAsset,
    GitHubReleasesResponse,
    PluginUninstallRequest,
    PluginUninstallResponse,
    Server,
    User,
    get_current_active_user,
    get_db,
)
from services import SSHManager
from services.github_credentials import get_effective_github_token
from services.plugin_installation import install_github_plugin as install_github_plugin_service

router = APIRouter(prefix="/api/github-plugins", tags=["github-plugins"])

logger = logging.getLogger(__name__)

OUTBOUND_HTTP_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}

# Regex to validate GitHub repository URL
GITHUB_REPO_PATTERN = re.compile(
    r"^https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)(?:/.*)?$"
)

# Progress update interval (percent) for panel proxy downloads/uploads
PROGRESS_UPDATE_INTERVAL = 10  # Update progress every 10%


def _coerce_ssh_manager(candidate: object) -> SSHManager:
    """Preserve direct-call compatibility while ASGI requests inject a manager."""
    if callable(getattr(candidate, "disconnect", None)):
        return candidate  # type: ignore[return-value]
    return SSHManager()


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
    snapshot = Server.model_validate(server, from_attributes=True)
    await db.commit()
    return snapshot


@router.get(
    "/releases",
    response_model=GitHubReleasesResponse,
    status_code=status.HTTP_200_OK,
    responses=OUTBOUND_HTTP_ERROR_RESPONSES,
)
async def get_github_releases(
    repo_url: str,
    count: int = 5,
    server_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    http_resource: ApplicationHTTP | object = Depends(resolve_application_http),
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

    # Get server's GitHub proxy if server_id is provided
    github_proxy = None
    if server_id:
        server = await get_server_and_verify_ownership(db, server_id, current_user)
        github_proxy = server.github_proxy

    # Limit count to prevent abuse
    count = min(count, 10)

    # Fetch releases from GitHub API
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"}

    # Prefer the user's token and use the system credential only as a fallback.
    github_token = await get_effective_github_token(db, current_user)
    # Release the transaction before the potentially slow GitHub request.
    await db.commit()
    outbound_http = cast(ApplicationHTTP, http_resource)

    success, data, error = await outbound_http.get(
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
    releases = []
    for release_data in data:
        if release_data.get("draft") or release_data.get("prerelease"):
            continue
        assets = []
        for asset_data in release_data.get("assets", []):
            asset_name = asset_data.get("name", "")
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
                assets.append(
                    GitHubReleaseAsset(
                        name=asset_name,
                        browser_download_url=asset_data.get("browser_download_url", ""),
                        size=asset_data.get("size", 0),
                        content_type=asset_data.get("content_type"),
                    )
                )

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

    return GitHubReleasesResponse(success=True, releases=releases, repo_owner=owner, repo_name=repo)


@router.get("/servers/{server_id}/analyze-archive")
async def analyze_archive(
    server_id: int,
    download_url: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
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
    # Validate URL
    if (
        not download_url.startswith("https://github.com/")
        or "/releases/download/" not in download_url
    ):
        return ArchiveAnalysisResponse(success=False, error="Invalid GitHub releases download URL")

    # Get server
    server = await get_server_and_verify_ownership(db, server_id, current_user)

    ssh_manager = _coerce_ssh_manager(ssh_manager)
    success, msg = await ssh_manager.connect(server)
    if not success:
        await ssh_manager.disconnect()
        return ArchiveAnalysisResponse(success=False, error=f"SSH connection failed: {msg}")

    try:
        # Create temp directory
        temp_dir = f"/tmp/archive_analysis_{server_id}"
        await ssh_manager.execute_command(f"rm -rf {temp_dir} && mkdir -p {temp_dir}")

        # Detect archive type from URL (including 7z)
        url_lower = download_url.lower()
        if url_lower.endswith(".zip"):
            archive_type = "zip"
            archive_file = f"{temp_dir}/archive.zip"
        elif url_lower.endswith(".tar.gz") or url_lower.endswith(".tgz"):
            archive_type = "tar.gz"
            archive_file = f"{temp_dir}/archive.tar.gz"
        elif url_lower.endswith(".tar"):
            archive_type = "tar"
            archive_file = f"{temp_dir}/archive.tar"
        elif url_lower.endswith(".7z"):
            archive_type = "7z"
            archive_file = f"{temp_dir}/archive.7z"
        else:
            # Try to detect from content-type after download
            archive_type = "unknown"
            archive_file = f"{temp_dir}/archive"

        # Download archive (use GitHub proxy if configured)
        actual_download_url = download_url
        if server.github_proxy and server.github_proxy.strip():
            # Apply GitHub proxy
            proxy_base = server.github_proxy.strip().rstrip("/")
            actual_download_url = f"{proxy_base}/{download_url}"

        download_cmd = f"curl -fsSL -o {archive_file} '{actual_download_url}'"
        success, _, stderr = await ssh_manager.execute_command(download_cmd, timeout=120)

        if not success:
            await ssh_manager.execute_command(f"rm -rf {temp_dir}")
            return ArchiveAnalysisResponse(
                success=False, error=f"Failed to download archive: {stderr}"
            )

        # List archive contents (including 7z)
        if archive_type == "zip":
            list_cmd = f"unzip -l {archive_file} | tail -n +4 | head -n -2"
        elif archive_type in ["tar.gz", "tar"]:
            list_cmd = f"tar -tzf {archive_file} 2>/dev/null || tar -tf {archive_file}"
        elif archive_type == "7z":
            list_cmd = f"7z l {archive_file} | grep -E '^[0-9]{{4}}-' | awk '{{print $NF}}' 2>/dev/null || 7za l {archive_file} | grep -E '^[0-9]{{4}}-' | awk '{{print $NF}}'"
        else:
            # Try to detect type
            type_cmd = f"file {archive_file}"
            _, type_output, _ = await ssh_manager.execute_command(type_cmd)
            if "Zip" in type_output:
                archive_type = "zip"
                list_cmd = f"unzip -l {archive_file} | tail -n +4 | head -n -2"
            elif "gzip" in type_output.lower() or "tar" in type_output.lower():
                archive_type = "tar.gz"
                list_cmd = f"tar -tzf {archive_file} 2>/dev/null || tar -tf {archive_file}"
            elif "7-zip" in type_output.lower():
                archive_type = "7z"
                list_cmd = f"7z l {archive_file} | grep -E '^[0-9]{{4}}-' | awk '{{print $NF}}'"
            else:
                await ssh_manager.execute_command(f"rm -rf {temp_dir}")
                return ArchiveAnalysisResponse(
                    success=False, error=f"Unsupported archive type: {type_output}"
                )

        success, list_output, stderr = await ssh_manager.execute_command(list_cmd, timeout=30)

        # Cleanup
        await ssh_manager.execute_command(f"rm -rf {temp_dir}")

        if not success:
            return ArchiveAnalysisResponse(
                success=False,
                error=f"Failed to list archive contents: {stderr}",
                archive_type=archive_type,
            )

        # Parse archive contents
        has_addons_dir = False
        root_dirs = set()
        top_level_items = []

        lines = list_output.strip().split("\n") if list_output.strip() else []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse based on archive type
            if archive_type == "zip":
                # unzip -l output format: "size  date time  path"
                parts = line.split()
                if len(parts) >= 4:
                    path = " ".join(parts[3:])
                else:
                    continue
            else:
                # tar output is just paths
                path = line

            # Normalize path
            path = path.strip("/")
            if not path:
                continue

            # Get top-level component
            top_level = path.split("/")[0]

            # Check for addons directory
            if path == "addons" or path.startswith("addons/"):
                has_addons_dir = True

            # Track root directories
            if "/" in path:
                root_dirs.add(top_level)

            # Add to top-level items (only first level)
            if "/" not in path or path.endswith("/"):
                is_dir = path.endswith("/") or any(
                    other_path.startswith(path.rstrip("/") + "/")
                    for other_path in [
                        line.strip().split()[-1] if archive_type == "zip" else line
                        for line in lines
                    ]
                    if other_path != path
                )
                if not any(item.path == path.rstrip("/") for item in top_level_items):
                    top_level_items.append(ArchiveContentItem(path=path.rstrip("/"), is_dir=is_dir))

        # Collect all directories and files from the archive for exclusion selection
        all_dirs = set()
        all_files = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse path and size based on archive type
            if archive_type == "zip":
                parts = line.split()
                if len(parts) >= 4:
                    path = " ".join(parts[3:])
                    # Try to extract size (first column)
                    try:
                        size = int(parts[0])
                    except ValueError, IndexError:
                        size = 0
                else:
                    continue
            else:
                # tar output is just paths, no size info
                path = line
                size = 0

            path = path.strip("/")
            if not path:
                continue

            # Determine if this is a file or directory
            is_dir = False

            # If path ends with / it's definitely a directory
            if path.endswith("/"):
                all_dirs.add(path.rstrip("/"))
                is_dir = True
            else:
                # Check if it's a directory by seeing if any other path starts with it
                for other_line in lines:
                    other_line = other_line.strip()
                    if archive_type == "zip":
                        parts = other_line.split()
                        if len(parts) >= 4:
                            other_path = " ".join(parts[3:]).strip("/")
                        else:
                            continue
                    else:
                        other_path = other_line.strip("/")

                    if other_path.startswith(path + "/"):
                        all_dirs.add(path)
                        is_dir = True
                        break

                # If not a directory, it's a file
                if not is_dir:
                    all_files.append(ArchiveContentItem(path=path, is_dir=False, size=size))

            # Also add parent directories
            parts = path.split("/")
            for i in range(1, len(parts)):
                parent = "/".join(parts[:i])
                if parent:
                    all_dirs.add(parent)

        return ArchiveAnalysisResponse(
            success=True,
            has_addons_dir=has_addons_dir,
            root_dirs=sorted(list(root_dirs)),
            all_dirs=sorted(list(all_dirs)),
            all_files=all_files,
            top_level_items=top_level_items,
            archive_type=archive_type,
        )

    except Exception as e:
        logger.error(f"Error analyzing archive: {e}")
        return ArchiveAnalysisResponse(success=False, error=f"Error analyzing archive: {str(e)}")
    finally:
        await ssh_manager.disconnect()


@router.post("/servers/{server_id}/install")
async def install_github_plugin(
    server_id: int,
    request: GitHubPluginInstallRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _operation_server: Server = Depends(locked_server_operation),
    http_resource: ApplicationHTTP | object = Depends(resolve_application_http),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
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
    return await install_github_plugin_service(
        server_id,
        request,
        db,
        current_user,
        ssh_manager=_coerce_ssh_manager(ssh_manager),
        http_resource=as_application_http(http_resource),
    )


@router.get("/servers/{server_id}/analyze-installed-plugins")
async def analyze_installed_plugins(
    server_id: int,
    directory: str = "addons",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
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

    ssh_manager = _coerce_ssh_manager(ssh_manager)
    success, msg = await ssh_manager.connect(server)
    if not success:
        await ssh_manager.disconnect()
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _operation_server: Server = Depends(locked_server_operation),
    ssh_manager: SSHManager = Depends(get_ssh_manager),
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

    ssh_manager = _coerce_ssh_manager(ssh_manager)
    success, msg = await ssh_manager.connect(server)
    if not success:
        await ssh_manager.disconnect()
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
