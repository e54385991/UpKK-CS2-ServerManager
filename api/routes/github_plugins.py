"""
GitHub Plugin Installation routes
Provides endpoints for fetching GitHub releases and installing plugins from them
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import re
import asyncio
import logging

from modules import (
    Server, get_db, User, get_current_active_user,
    GitHubReleasesResponse, GitHubRelease, GitHubReleaseAsset,
    ArchiveAnalysisResponse, ArchiveContentItem,
    GitHubPluginInstallRequest, GitHubPluginInstallResponse,
    ActionResponse
)
from modules.http_helper import http_helper
from services import SSHManager

router = APIRouter(prefix="/api/github-plugins", tags=["github-plugins"])

logger = logging.getLogger(__name__)

# Regex to validate GitHub repository URL
GITHUB_REPO_PATTERN = re.compile(r'^https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)(?:/.*)?$')


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


async def get_server_and_verify_ownership(
    db: AsyncSession, server_id: int, user_id: int
) -> Server:
    """
    Get server by ID and verify user ownership.
    Raises HTTPException if server not found or user doesn't own it.
    """
    server = await Server.get_by_id_and_user(db, server_id, user_id)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found"
        )
    return server


@router.get("/releases")
async def get_github_releases(
    repo_url: str,
    count: int = 5,
    current_user: User = Depends(get_current_active_user)
) -> GitHubReleasesResponse:
    """
    Fetch recent releases from a GitHub repository.
    
    Args:
        repo_url: GitHub repository URL (e.g., https://github.com/Source2ZE/CS2Fixes)
        count: Number of releases to fetch (default: 5, max: 10)
    
    Returns:
        List of releases with their assets
    """
    try:
        owner, repo = parse_github_url(repo_url)
    except ValueError as e:
        return GitHubReleasesResponse(
            success=False,
            error=str(e),
            releases=[]
        )
    
    # Limit count to prevent abuse
    count = min(count, 10)
    
    # Fetch releases from GitHub API
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CS2-ServerManager"
    }
    
    success, data, error = await http_helper.get(
        api_url,
        headers=headers,
        params={"per_page": count},
        timeout=30
    )
    
    if not success:
        return GitHubReleasesResponse(
            success=False,
            error=f"Failed to fetch releases: {error}",
            releases=[],
            repo_owner=owner,
            repo_name=repo
        )
    
    if not isinstance(data, list):
        return GitHubReleasesResponse(
            success=False,
            error="Unexpected response format from GitHub API",
            releases=[],
            repo_owner=owner,
            repo_name=repo
        )
    
    # Parse releases
    releases = []
    for release_data in data[:count]:
        assets = []
        for asset_data in release_data.get("assets", []):
            asset_name = asset_data.get("name", "")
            asset_name_lower = asset_name.lower()
            
            # Skip Windows-specific archives (filename contains 'windows' or 'win')
            if 'windows' in asset_name_lower or '-win-' in asset_name_lower or '_win_' in asset_name_lower or asset_name_lower.endswith('-win.zip'):
                continue
            
            # Only include archive files that could be plugins (including 7z)
            if any(asset_name_lower.endswith(ext) for ext in [".zip", ".tar.gz", ".tgz", ".tar", ".7z"]):
                assets.append(GitHubReleaseAsset(
                    name=asset_name,
                    browser_download_url=asset_data.get("browser_download_url", ""),
                    size=asset_data.get("size", 0),
                    content_type=asset_data.get("content_type")
                ))
        
        # Only include releases that have downloadable assets
        if assets:
            releases.append(GitHubRelease(
                tag_name=release_data.get("tag_name", ""),
                name=release_data.get("name"),
                published_at=release_data.get("published_at"),
                prerelease=release_data.get("prerelease", False),
                assets=assets
            ))
    
    return GitHubReleasesResponse(
        success=True,
        releases=releases,
        repo_owner=owner,
        repo_name=repo
    )


@router.get("/servers/{server_id}/analyze-archive")
async def analyze_archive(
    server_id: int,
    download_url: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
    if not download_url.startswith('https://github.com/') or '/releases/download/' not in download_url:
        return ArchiveAnalysisResponse(
            success=False,
            error="Invalid GitHub releases download URL"
        )
    
    # Get server
    server = await get_server_and_verify_ownership(db, server_id, current_user.id)
    
    ssh_manager = SSHManager()
    success, msg = await ssh_manager.connect(server)
    if not success:
        return ArchiveAnalysisResponse(
            success=False,
            error=f"SSH connection failed: {msg}"
        )
    
    try:
        # Create temp directory
        temp_dir = f"/tmp/archive_analysis_{server_id}"
        await ssh_manager.execute_command(f"rm -rf {temp_dir} && mkdir -p {temp_dir}")
        
        # Detect archive type from URL (including 7z)
        url_lower = download_url.lower()
        if url_lower.endswith('.zip'):
            archive_type = 'zip'
            archive_file = f"{temp_dir}/archive.zip"
        elif url_lower.endswith('.tar.gz') or url_lower.endswith('.tgz'):
            archive_type = 'tar.gz'
            archive_file = f"{temp_dir}/archive.tar.gz"
        elif url_lower.endswith('.tar'):
            archive_type = 'tar'
            archive_file = f"{temp_dir}/archive.tar"
        elif url_lower.endswith('.7z'):
            archive_type = '7z'
            archive_file = f"{temp_dir}/archive.7z"
        else:
            # Try to detect from content-type after download
            archive_type = 'unknown'
            archive_file = f"{temp_dir}/archive"
        
        # Download archive
        download_cmd = f"curl -fsSL -o {archive_file} '{download_url}'"
        success, _, stderr = await ssh_manager.execute_command(download_cmd, timeout=120)
        
        if not success:
            await ssh_manager.execute_command(f"rm -rf {temp_dir}")
            return ArchiveAnalysisResponse(
                success=False,
                error=f"Failed to download archive: {stderr}"
            )
        
        # List archive contents (including 7z)
        if archive_type == 'zip':
            list_cmd = f"unzip -l {archive_file} | tail -n +4 | head -n -2"
        elif archive_type in ['tar.gz', 'tar']:
            list_cmd = f"tar -tzf {archive_file} 2>/dev/null || tar -tf {archive_file}"
        elif archive_type == '7z':
            list_cmd = f"7z l {archive_file} | grep -E '^[0-9]{{4}}-' | awk '{{print $NF}}' 2>/dev/null || 7za l {archive_file} | grep -E '^[0-9]{{4}}-' | awk '{{print $NF}}'"
        else:
            # Try to detect type
            type_cmd = f"file {archive_file}"
            _, type_output, _ = await ssh_manager.execute_command(type_cmd)
            if 'Zip' in type_output:
                archive_type = 'zip'
                list_cmd = f"unzip -l {archive_file} | tail -n +4 | head -n -2"
            elif 'gzip' in type_output.lower() or 'tar' in type_output.lower():
                archive_type = 'tar.gz'
                list_cmd = f"tar -tzf {archive_file} 2>/dev/null || tar -tf {archive_file}"
            elif '7-zip' in type_output.lower():
                archive_type = '7z'
                list_cmd = f"7z l {archive_file} | grep -E '^[0-9]{{4}}-' | awk '{{print $NF}}'"
            else:
                await ssh_manager.execute_command(f"rm -rf {temp_dir}")
                return ArchiveAnalysisResponse(
                    success=False,
                    error=f"Unsupported archive type: {type_output}"
                )
        
        success, list_output, stderr = await ssh_manager.execute_command(list_cmd, timeout=30)
        
        # Cleanup
        await ssh_manager.execute_command(f"rm -rf {temp_dir}")
        
        if not success:
            return ArchiveAnalysisResponse(
                success=False,
                error=f"Failed to list archive contents: {stderr}",
                archive_type=archive_type
            )
        
        # Parse archive contents
        has_addons_dir = False
        root_dirs = set()
        top_level_items = []
        
        lines = list_output.strip().split('\n') if list_output.strip() else []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Parse based on archive type
            if archive_type == 'zip':
                # unzip -l output format: "size  date time  path"
                parts = line.split()
                if len(parts) >= 4:
                    path = ' '.join(parts[3:])
                else:
                    continue
            else:
                # tar output is just paths
                path = line
            
            # Normalize path
            path = path.strip('/')
            if not path:
                continue
            
            # Get top-level component
            top_level = path.split('/')[0]
            
            # Check for addons directory
            if path == 'addons' or path.startswith('addons/'):
                has_addons_dir = True
            
            # Track root directories
            if '/' in path:
                root_dirs.add(top_level)
            
            # Add to top-level items (only first level)
            if '/' not in path or path.endswith('/'):
                is_dir = path.endswith('/') or any(
                    other_path.startswith(path.rstrip('/') + '/') 
                    for other_path in [l.strip().split()[-1] if archive_type == 'zip' else l for l in lines]
                    if other_path != path
                )
                if not any(item.path == path.rstrip('/') for item in top_level_items):
                    top_level_items.append(ArchiveContentItem(
                        path=path.rstrip('/'),
                        is_dir=is_dir
                    ))
        
        # Collect all directories from the archive for exclusion selection
        all_dirs = set()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if archive_type == 'zip':
                parts = line.split()
                if len(parts) >= 4:
                    path = ' '.join(parts[3:])
                else:
                    continue
            else:
                path = line
            
            path = path.strip('/')
            if not path:
                continue
            
            # If path ends with / it's definitely a directory
            if path.endswith('/'):
                all_dirs.add(path.rstrip('/'))
            else:
                # Check if it's a directory by seeing if any other path starts with it
                for other_line in lines:
                    other_line = other_line.strip()
                    if archive_type == 'zip':
                        parts = other_line.split()
                        if len(parts) >= 4:
                            other_path = ' '.join(parts[3:]).strip('/')
                        else:
                            continue
                    else:
                        other_path = other_line.strip('/')
                    
                    if other_path.startswith(path + '/'):
                        all_dirs.add(path)
                        break
            
            # Also add parent directories
            parts = path.split('/')
            for i in range(1, len(parts)):
                parent = '/'.join(parts[:i])
                if parent:
                    all_dirs.add(parent)
        
        return ArchiveAnalysisResponse(
            success=True,
            has_addons_dir=has_addons_dir,
            root_dirs=sorted(list(root_dirs)),
            all_dirs=sorted(list(all_dirs)),
            top_level_items=top_level_items,
            archive_type=archive_type
        )
    
    except Exception as e:
        logger.error(f"Error analyzing archive: {e}")
        return ArchiveAnalysisResponse(
            success=False,
            error=f"Error analyzing archive: {str(e)}"
        )
    finally:
        await ssh_manager.disconnect()


@router.post("/servers/{server_id}/install")
async def install_github_plugin(
    server_id: int,
    request: GitHubPluginInstallRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
    from api.routes.actions import send_deployment_update
    
    server = await get_server_and_verify_ownership(db, server_id, current_user.id)
    
    async def progress(msg: str, msg_type: str = "status"):
        """Send progress update via WebSocket"""
        await send_deployment_update(server_id, msg_type, msg)
    
    ssh_manager = SSHManager()
    success, msg = await ssh_manager.connect(server)
    if not success:
        await progress(f"SSH connection failed: {msg}", "error")
        return GitHubPluginInstallResponse(
            success=False,
            message=f"SSH connection failed: {msg}"
        )
    
    try:
        await progress("Connected to server, starting plugin installation...")
        
        # Verify CS2 is installed
        cs2_dir = f"{server.game_directory}/cs2"
        csgo_dir = f"{cs2_dir}/game/csgo"
        
        await progress("Checking CS2 server installation...")
        check_cmd = f"test -d {csgo_dir} && echo 'exists'"
        success, check_output, _ = await ssh_manager.execute_command(check_cmd)
        
        if not success or 'exists' not in check_output:
            await progress("CS2 server not found. Please deploy the server first.", "error")
            return GitHubPluginInstallResponse(
                success=False,
                message="CS2 server not found. Please deploy the server first."
            )
        
        # Create temp directory
        temp_dir = f"/tmp/github_plugin_{server_id}"
        await ssh_manager.execute_command(f"rm -rf {temp_dir} && mkdir -p {temp_dir}")
        
        # Detect archive type (support zip, tar.gz, tgz, tar, 7z)
        url_lower = request.download_url.lower()
        if url_lower.endswith('.zip'):
            archive_type = 'zip'
            archive_file = f"{temp_dir}/plugin.zip"
        elif url_lower.endswith('.tar.gz') or url_lower.endswith('.tgz'):
            archive_type = 'tar.gz'
            archive_file = f"{temp_dir}/plugin.tar.gz"
        elif url_lower.endswith('.tar'):
            archive_type = 'tar'
            archive_file = f"{temp_dir}/plugin.tar"
        elif url_lower.endswith('.7z'):
            archive_type = '7z'
            archive_file = f"{temp_dir}/plugin.7z"
        else:
            archive_type = 'zip'  # Default assumption
            archive_file = f"{temp_dir}/plugin.zip"
        
        # Download archive with progress
        await progress(f"Downloading plugin archive ({archive_type})...")
        logger.info(f"Downloading plugin from {request.download_url}")
        
        # Use curl with progress output
        download_cmd = f"curl -fL --progress-bar -o {archive_file} '{request.download_url}' 2>&1"
        success, download_output, stderr = await ssh_manager.execute_command(download_cmd, timeout=300)
        
        if not success:
            await ssh_manager.execute_command(f"rm -rf {temp_dir}")
            await progress(f"Failed to download plugin: {stderr}", "error")
            return GitHubPluginInstallResponse(
                success=False,
                message=f"Failed to download plugin: {stderr}"
            )
        
        # Verify download and get file size
        size_cmd = f"stat -c%s {archive_file} 2>/dev/null || stat -f%z {archive_file} 2>/dev/null"
        success, size_output, _ = await ssh_manager.execute_command(size_cmd)
        
        if not success or not size_output.strip():
            await ssh_manager.execute_command(f"rm -rf {temp_dir}")
            await progress("Downloaded file is invalid", "error")
            return GitHubPluginInstallResponse(
                success=False,
                message="Downloaded file is invalid"
            )
        
        file_size = int(size_output.strip())
        if file_size < 1000:
            await ssh_manager.execute_command(f"rm -rf {temp_dir}")
            await progress("Downloaded file is too small or empty", "error")
            return GitHubPluginInstallResponse(
                success=False,
                message="Downloaded file is too small or empty"
            )
        
        # Format file size for display
        if file_size >= 1024 * 1024:
            size_str = f"{file_size / (1024 * 1024):.2f} MB"
        elif file_size >= 1024:
            size_str = f"{file_size / 1024:.2f} KB"
        else:
            size_str = f"{file_size} B"
        
        await progress(f"Download complete ({size_str})")
        
        # Create extraction directory
        extract_dir = f"{temp_dir}/extracted"
        await ssh_manager.execute_command(f"mkdir -p {extract_dir}")
        
        # Extract archive (support zip, tar.gz, tar, 7z)
        await progress(f"Extracting {archive_type} archive...")
        if archive_type == 'zip':
            extract_cmd = f"unzip -o {archive_file} -d {extract_dir}"
        elif archive_type == '7z':
            # Check if 7z is available
            check_7z = "command -v 7z || command -v 7za"
            success, seven_zip_path, _ = await ssh_manager.execute_command(check_7z)
            if not seven_zip_path.strip():
                extract_cmd = f"7za x -y -o{extract_dir} {archive_file} 2>/dev/null || 7zr x -y -o{extract_dir} {archive_file}"
            else:
                extract_cmd = f"7z x -y -o{extract_dir} {archive_file}"
        else:
            extract_cmd = f"tar -xzf {archive_file} -C {extract_dir} 2>/dev/null || tar -xf {archive_file} -C {extract_dir}"
        
        success, _, stderr = await ssh_manager.execute_command(extract_cmd, timeout=120)
        
        if not success:
            await ssh_manager.execute_command(f"rm -rf {temp_dir}")
            await progress(f"Failed to extract archive: {stderr}", "error")
            return GitHubPluginInstallResponse(
                success=False,
                message=f"Failed to extract archive: {stderr}"
            )
        
        await progress("Extraction complete, analyzing archive structure...")
        
        # Check if addons directory exists in extracted content
        addons_check = f"test -d {extract_dir}/addons && echo 'addons_found'"
        success, addons_output, _ = await ssh_manager.execute_command(addons_check)
        has_addons = 'addons_found' in addons_output
        
        # Determine source directory for copy
        if has_addons:
            # Archive has proper structure (addons/, cfg/, etc.)
            source_dir = extract_dir
            await progress("Found addons/ directory at root level")
        else:
            # Check if there's a single subdirectory that contains addons
            find_cmd = f"find {extract_dir} -maxdepth 2 -type d -name 'addons' | head -1"
            success, find_output, _ = await ssh_manager.execute_command(find_cmd)
            
            if find_output.strip():
                # Found addons in subdirectory
                addons_path = find_output.strip()
                source_dir = addons_path.rsplit('/addons', 1)[0]
                await progress(f"Found addons/ directory in subdirectory")
            else:
                # No addons directory found - reject installation
                await ssh_manager.execute_command(f"rm -rf {temp_dir}")
                error_msg = "No addons/ directory found in archive. This does not appear to be a valid CS2 plugin package."
                await progress(error_msg, "error")
                return GitHubPluginInstallResponse(
                    success=False,
                    message=error_msg
                )
        
        # Build exclusion patterns for directories
        exclude_raw_patterns = []
        
        # Exclude specified directories
        for exclude_dir in request.exclude_dirs:
            # Sanitize directory name
            safe_dir = exclude_dir.strip().strip('/')
            if safe_dir and '..' not in safe_dir:
                exclude_raw_patterns.append(safe_dir)
                exclude_raw_patterns.append(f'{safe_dir}/')
                exclude_raw_patterns.append(f'{safe_dir}/*')
        
        if exclude_raw_patterns:
            await progress(f"Excluding {len(request.exclude_dirs)} director(y/ies) from installation")
        
        # Count files before copy
        count_before_cmd = f"find {csgo_dir}/addons -type f 2>/dev/null | wc -l"
        _, count_before, _ = await ssh_manager.execute_command(count_before_cmd)
        count_before = int(count_before.strip()) if count_before.strip().isdigit() else 0
        
        await progress("Installing plugin files...")
        
        # Copy files using rsync for better control
        rsync_check = "command -v rsync"
        success, rsync_path, _ = await ssh_manager.execute_command(rsync_check)
        
        if rsync_path.strip():
            # Use rsync for better control
            rsync_excludes = ' '.join([f"--exclude='{p}'" for p in exclude_raw_patterns])
            copy_cmd = f"rsync -av {rsync_excludes} {source_dir}/ {csgo_dir}/"
        else:
            # Fallback to cp with tar for exclusions
            if exclude_raw_patterns:
                tar_excludes = ' '.join([f"--exclude={p}" for p in exclude_raw_patterns])
                copy_cmd = f"cd {source_dir} && tar {tar_excludes} -cf - . | tar -xf - -C {csgo_dir}"
            else:
                copy_cmd = f"cp -r {source_dir}/* {csgo_dir}/"
        
        success, copy_output, stderr = await ssh_manager.execute_command(copy_cmd, timeout=120)
        
        # Count files after copy
        count_after_cmd = f"find {csgo_dir}/addons -type f 2>/dev/null | wc -l"
        _, count_after, _ = await ssh_manager.execute_command(count_after_cmd)
        count_after = int(count_after.strip()) if count_after.strip().isdigit() else 0
        
        installed_files = count_after - count_before if count_after > count_before else 0
        
        # Cleanup
        await ssh_manager.execute_command(f"rm -rf {temp_dir}")
        await progress("Cleanup complete")
        
        if not success:
            await progress(f"Failed to copy files: {stderr}", "error")
            return GitHubPluginInstallResponse(
                success=False,
                message=f"Failed to copy files: {stderr}",
                installed_files=installed_files
            )
        
        success_msg = f"Plugin installed successfully! {installed_files} files installed. Restart server to apply changes."
        await progress(success_msg, "complete")
        
        return GitHubPluginInstallResponse(
            success=True,
            message=success_msg,
            installed_files=installed_files
        )
    
    except Exception as e:
        logger.error(f"Error installing plugin: {e}")
        await progress(f"Installation error: {str(e)}", "error")
        return GitHubPluginInstallResponse(
            success=False,
            message=f"Installation error: {str(e)}"
        )
    finally:
        await ssh_manager.disconnect()

