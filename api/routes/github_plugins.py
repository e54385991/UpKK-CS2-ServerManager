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
import tempfile
import os
import uuid
import shutil

from modules import (
    Server, get_db, User, get_current_active_user,
    GitHubReleasesResponse, GitHubRelease, GitHubReleaseAsset,
    ArchiveAnalysisResponse, ArchiveContentItem,
    GitHubPluginInstallRequest, GitHubPluginInstallResponse,
    PluginUninstallRequest, PluginUninstallResponse,
    ActionResponse
)
from modules.http_helper import http_helper
from services import SSHManager

router = APIRouter(prefix="/api/github-plugins", tags=["github-plugins"])

logger = logging.getLogger(__name__)

# Regex to validate GitHub repository URL
GITHUB_REPO_PATTERN = re.compile(r'^https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)(?:/.*)?$')

# Progress update interval (percent) for panel proxy downloads/uploads
PROGRESS_UPDATE_INTERVAL = 10  # Update progress every 10%


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
    db: AsyncSession, server_id: int, user: User
) -> Server:
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found"
        )
    return server


@router.get("/releases")
async def get_github_releases(
    repo_url: str,
    count: int = 5,
    server_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
        return GitHubReleasesResponse(
            success=False,
            error=str(e),
            releases=[]
        )
    
    # Get server's GitHub proxy if server_id is provided
    github_proxy = None
    if server_id:
        server = await get_server_and_verify_ownership(db, server_id, current_user)
        github_proxy = server.github_proxy
    
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
        timeout=30,
        proxy=github_proxy
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
    server = await get_server_and_verify_ownership(db, server_id, current_user)
    
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
        
        # Download archive (use GitHub proxy if configured)
        actual_download_url = download_url
        if server.github_proxy and server.github_proxy.strip():
            # Apply GitHub proxy
            proxy_base = server.github_proxy.strip().rstrip('/')
            actual_download_url = f"{proxy_base}/{download_url}"
        
        download_cmd = f"curl -fsSL -o {archive_file} '{actual_download_url}'"
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
        
        # Collect all directories and files from the archive for exclusion selection
        all_dirs = set()
        all_files = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Parse path and size based on archive type
            if archive_type == 'zip':
                parts = line.split()
                if len(parts) >= 4:
                    path = ' '.join(parts[3:])
                    # Try to extract size (first column)
                    try:
                        size = int(parts[0])
                    except (ValueError, IndexError):
                        size = 0
                else:
                    continue
            else:
                # tar output is just paths, no size info
                path = line
                size = 0
            
            path = path.strip('/')
            if not path:
                continue
            
            # Determine if this is a file or directory
            is_dir = False
            
            # If path ends with / it's definitely a directory
            if path.endswith('/'):
                all_dirs.add(path.rstrip('/'))
                is_dir = True
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
                        is_dir = True
                        break
                
                # If not a directory, it's a file
                if not is_dir:
                    all_files.append(ArchiveContentItem(
                        path=path,
                        is_dir=False,
                        size=size
                    ))
            
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
            all_files=all_files,
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
    
    server = await get_server_and_verify_ownership(db, server_id, current_user)
    
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
        
        # Detect archive type (support zip, tar.gz, tgz, tar, 7z)
        url_lower = request.download_url.lower()
        if url_lower.endswith('.zip'):
            archive_type = 'zip'
            archive_filename = "plugin.zip"
        elif url_lower.endswith('.tar.gz') or url_lower.endswith('.tgz'):
            archive_type = 'tar.gz'
            archive_filename = "plugin.tar.gz"
        elif url_lower.endswith('.tar'):
            archive_type = 'tar'
            archive_filename = "plugin.tar"
        elif url_lower.endswith('.7z'):
            archive_type = '7z'
            archive_filename = "plugin.7z"
        else:
            archive_type = 'zip'  # Default assumption
            archive_filename = "plugin.zip"
        
        # Check if we should use panel proxy mode (server-level setting)
        if server.use_panel_proxy:
            # Panel Proxy Mode: Download to panel server first, then SFTP upload
            await progress("Using panel server proxy mode (github_proxy setting ignored)...")
            
            # Create UID-isolated temp directory on panel server
            panel_temp_dir = os.path.join(tempfile.gettempdir(), f"cs2_panel_proxy_{current_user.id}")
            os.makedirs(panel_temp_dir, exist_ok=True)
            
            # Create unique subdirectory for this download
            download_id = str(uuid.uuid4())
            download_dir = os.path.join(panel_temp_dir, download_id)
            os.makedirs(download_dir, exist_ok=True)
            
            panel_archive_path = os.path.join(download_dir, archive_filename)
            
            try:
                # Download to panel server
                await progress(f"Downloading {archive_type} archive to panel server...")
                logger.info(f"Panel proxy: Downloading from {request.download_url} to {panel_archive_path}")
                
                from modules.http_helper import http_helper
                
                # Progress tracking for download
                last_progress_percent = 0
                async def download_progress(bytes_downloaded, total_bytes):
                    nonlocal last_progress_percent
                    if total_bytes > 0:
                        percent = int((bytes_downloaded / total_bytes) * 100)
                        # Only update at configured interval
                        if percent >= last_progress_percent + PROGRESS_UPDATE_INTERVAL or percent == 100:
                            last_progress_percent = percent
                            size_mb = bytes_downloaded / (1024 * 1024)
                            total_mb = total_bytes / (1024 * 1024)
                            await progress(f"Download progress: {percent}% ({size_mb:.1f}/{total_mb:.1f} MB)")
                
                success, error = await http_helper.download_file(
                    request.download_url,
                    panel_archive_path,
                    timeout=600,
                    progress_callback=download_progress
                )
                
                if not success:
                    await progress(f"Failed to download to panel server: {error}", "error")
                    return GitHubPluginInstallResponse(
                        success=False,
                        message=f"Failed to download to panel server: {error}"
                    )
                
                # Verify download
                if not os.path.exists(panel_archive_path):
                    await progress("Downloaded file not found", "error")
                    return GitHubPluginInstallResponse(
                        success=False,
                        message="Downloaded file not found"
                    )
                
                file_size = os.path.getsize(panel_archive_path)
                if file_size < 1000:
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
                
                await progress(f"Download complete ({size_str}), uploading to server via SFTP...")
                
                # Upload to remote server via SFTP
                remote_temp_dir = f"/tmp/github_plugin_{server_id}"
                await ssh_manager.execute_command(f"rm -rf {remote_temp_dir} && mkdir -p {remote_temp_dir}")
                remote_archive_path = f"{remote_temp_dir}/{archive_filename}"
                
                # Progress tracking for upload
                last_upload_percent = 0
                async def upload_progress(bytes_uploaded, total_bytes):
                    nonlocal last_upload_percent
                    if total_bytes > 0:
                        percent = int((bytes_uploaded / total_bytes) * 100)
                        # Only update at configured interval
                        if percent >= last_upload_percent + PROGRESS_UPDATE_INTERVAL or percent == 100:
                            last_upload_percent = percent
                            size_mb = bytes_uploaded / (1024 * 1024)
                            total_mb = total_bytes / (1024 * 1024)
                            await progress(f"Upload progress: {percent}% ({size_mb:.1f}/{total_mb:.1f} MB)")
                
                success, error = await ssh_manager.upload_file_with_progress(
                    panel_archive_path,
                    remote_archive_path,
                    server,
                    progress_callback=upload_progress
                )
                
                if not success:
                    await progress(f"Failed to upload to server: {error}", "error")
                    return GitHubPluginInstallResponse(
                        success=False,
                        message=f"Failed to upload to server: {error}"
                    )
                
                await progress("Upload complete, proceeding with extraction...")
                
                # Set archive_file for extraction phase
                archive_file = remote_archive_path
                
            finally:
                # Clean up panel temp directory
                try:
                    if os.path.exists(download_dir):
                        shutil.rmtree(download_dir)
                        logger.info(f"Cleaned up panel temp directory: {download_dir}")
                except Exception as e:
                    logger.warning(f"Failed to clean up panel temp directory {download_dir}: {e}")
        else:
            # Original Mode: Download directly on remote server
            # Create temp directory
            temp_dir = f"/tmp/github_plugin_{server_id}"
            await ssh_manager.execute_command(f"rm -rf {temp_dir} && mkdir -p {temp_dir}")
            archive_file = f"{temp_dir}/{archive_filename}"
            
            # Download archive with progress (use GitHub proxy if configured)
            await progress(f"Downloading plugin archive ({archive_type})...")
            logger.info(f"Downloading plugin from {request.download_url}")
            
            # Apply GitHub proxy if configured
            actual_download_url = request.download_url
            if server.github_proxy and server.github_proxy.strip():
                proxy_base = server.github_proxy.strip().rstrip('/')
                actual_download_url = f"{proxy_base}/{request.download_url}"
                logger.info(f"Using GitHub proxy: {proxy_base}")
            
            # Use curl with progress output
            download_cmd = f"curl -fL --progress-bar -o {archive_file} '{actual_download_url}' 2>&1"
            success, download_output, stderr = await ssh_manager.execute_command(download_cmd, timeout=300)
            
            if not success:
                await ssh_manager.execute_command(f"rm -rf {temp_dir}")
                await progress(f"Failed to download plugin: {stderr}", "error")
                return GitHubPluginInstallResponse(
                    success=False,
                    message=f"Failed to download plugin: {stderr}"
                )
        
        # Continue with common extraction logic
        # Get remote temp directory based on mode
        if server.use_panel_proxy:
            remote_temp_dir = f"/tmp/github_plugin_{server_id}"
        else:
            remote_temp_dir = temp_dir
        
        # Verify download and get file size (only needed for non-panel-proxy mode)
        if not server.use_panel_proxy:
            size_cmd = f"stat -c%s {archive_file} 2>/dev/null || stat -f%z {archive_file} 2>/dev/null"
            success, size_output, _ = await ssh_manager.execute_command(size_cmd)
            
            if not success or not size_output.strip():
                await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
                await progress("Downloaded file is invalid", "error")
                return GitHubPluginInstallResponse(
                    success=False,
                    message="Downloaded file is invalid"
                )
            
            file_size = int(size_output.strip())
            if file_size < 1000:
                await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
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
        extract_dir = f"{remote_temp_dir}/extracted"
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
            await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
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
            elif request.custom_install_path:
                # No addons directory found, but custom install path is specified
                # Extract to the custom path (e.g., 'addons')
                safe_custom_path = request.custom_install_path.strip().strip('/')
                
                # Validate custom path to prevent path traversal
                if '..' in safe_custom_path or safe_custom_path.startswith('/'):
                    await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
                    error_msg = "Invalid custom install path specified"
                    await progress(error_msg, "error")
                    return GitHubPluginInstallResponse(
                        success=False,
                        message=error_msg
                    )
                
                # Build exclusion patterns for files and directories
                exclude_raw_patterns = []
                
                # Exclude specified files (new preferred method)
                for exclude_file in request.exclude_files:
                    # Sanitize file path
                    safe_file = exclude_file.strip().strip('/')
                    if safe_file and '..' not in safe_file:
                        exclude_raw_patterns.append(safe_file)
                
                # Also support excluding directories for backward compatibility
                for exclude_dir in request.exclude_dirs:
                    # Sanitize directory name
                    safe_dir = exclude_dir.strip().strip('/')
                    if safe_dir and '..' not in safe_dir:
                        exclude_raw_patterns.append(safe_dir)
                        exclude_raw_patterns.append(f'{safe_dir}/')
                        exclude_raw_patterns.append(f'{safe_dir}/*')
                
                if exclude_raw_patterns:
                    exclude_count = len(request.exclude_files) + len(request.exclude_dirs)
                    await progress(f"Excluding {exclude_count} item(s) from installation")
                
                # Create the target directory structure
                target_custom_dir = f"{csgo_dir}/{safe_custom_path}"
                mkdir_cmd = f"mkdir -p {target_custom_dir}"
                await ssh_manager.execute_command(mkdir_cmd)
                
                # Copy with exclusions
                rsync_check = "command -v rsync"
                success_check, rsync_path, _ = await ssh_manager.execute_command(rsync_check)
                
                if rsync_path.strip():
                    # Use rsync for better control
                    rsync_excludes = ''
                    if exclude_raw_patterns:
                        for pattern in exclude_raw_patterns:
                            rsync_excludes += f' --exclude="{pattern}"'
                        await progress(f"Applying {len(exclude_raw_patterns)} exclusion pattern(s)")
                    copy_cmd = f'rsync -av{rsync_excludes} "{extract_dir}/" "{target_custom_dir}/"'
                else:
                    # Fallback to cp with tar for exclusions
                    if exclude_raw_patterns:
                        tar_excludes = ' '.join([f'--exclude="{p}"' for p in exclude_raw_patterns])
                        copy_cmd = f'cd "{extract_dir}" && tar {tar_excludes} -cf - . | tar -xf - -C "{target_custom_dir}"'
                        await progress(f"Using tar with {len(exclude_raw_patterns)} exclusion pattern(s)")
                    else:
                        copy_cmd = f'cp -r "{extract_dir}"/* "{target_custom_dir}/"'
                
                logger.info(f"Custom path copy command: {copy_cmd}")
                success, _, stderr = await ssh_manager.execute_command(copy_cmd)
                
                if not success:
                    await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
                    error_msg = f"Failed to copy files to custom path: {stderr}"
                    await progress(error_msg, "error")
                    return GitHubPluginInstallResponse(
                        success=False,
                        message=error_msg
                    )
                
                await progress(f"Extracted to custom path: {safe_custom_path}")
                
                # Cleanup and return success
                await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
                
                # Count files after installation
                count_after_cmd = f"find {csgo_dir}/addons -type f 2>/dev/null | wc -l"
                _, count_after, _ = await ssh_manager.execute_command(count_after_cmd)
                count_after = int(count_after.strip()) if count_after.strip().isdigit() else 0
                
                await progress(f"Installation complete! Custom path used: {safe_custom_path}", "success")
                
                return GitHubPluginInstallResponse(
                    success=True,
                    message=f"Plugin installed successfully to custom path: {safe_custom_path}",
                    installed_files=count_after
                )
            else:
                # No addons directory found - reject installation
                await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
                error_msg = "No addons/ directory found in archive. This does not appear to be a valid CS2 plugin package."
                await progress(error_msg, "error")
                return GitHubPluginInstallResponse(
                    success=False,
                    message=error_msg
                )
        
        # Build exclusion patterns for files and directories
        exclude_raw_patterns = []
        
        # Exclude specified files (new preferred method)
        for exclude_file in request.exclude_files:
            # Sanitize file path
            safe_file = exclude_file.strip().strip('/')
            if safe_file and '..' not in safe_file:
                exclude_raw_patterns.append(safe_file)
        
        # Also support excluding directories for backward compatibility
        for exclude_dir in request.exclude_dirs:
            # Sanitize directory name
            safe_dir = exclude_dir.strip().strip('/')
            if safe_dir and '..' not in safe_dir:
                exclude_raw_patterns.append(safe_dir)
                exclude_raw_patterns.append(f'{safe_dir}/')
                exclude_raw_patterns.append(f'{safe_dir}/*')
        
        if exclude_raw_patterns:
            exclude_count = len(request.exclude_files) + len(request.exclude_dirs)
            await progress(f"Excluding {exclude_count} item(s) from installation")
        
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
            # Build exclude arguments properly for rsync
            rsync_excludes = ''
            if exclude_raw_patterns:
                for pattern in exclude_raw_patterns:
                    rsync_excludes += f' --exclude="{pattern}"'
                await progress(f"Applying {len(exclude_raw_patterns)} exclusion pattern(s)")
            copy_cmd = f'rsync -av{rsync_excludes} "{source_dir}/" "{csgo_dir}/"'
        else:
            # Fallback to cp with tar for exclusions
            if exclude_raw_patterns:
                tar_excludes = ' '.join([f'--exclude="{p}"' for p in exclude_raw_patterns])
                copy_cmd = f'cd "{source_dir}" && tar {tar_excludes} -cf - . | tar -xf - -C "{csgo_dir}"'
                await progress(f"Using tar with {len(exclude_raw_patterns)} exclusion pattern(s)")
            else:
                copy_cmd = f'cp -r "{source_dir}"/* "{csgo_dir}/"'
        
        logger.info(f"Copy command: {copy_cmd}")
        success, copy_output, stderr = await ssh_manager.execute_command(copy_cmd, timeout=120)
        
        # Count files after copy
        count_after_cmd = f"find {csgo_dir}/addons -type f 2>/dev/null | wc -l"
        _, count_after, _ = await ssh_manager.execute_command(count_after_cmd)
        count_after = int(count_after.strip()) if count_after.strip().isdigit() else 0
        
        installed_files = count_after - count_before if count_after > count_before else 0
        
        # Cleanup - use the correct temp directory
        await ssh_manager.execute_command(f"rm -rf {remote_temp_dir}")
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


@router.get("/servers/{server_id}/analyze-installed-plugins")
async def analyze_installed_plugins(
    server_id: int,
    directory: str = "addons",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
        return InstalledPluginAnalysisResponse(
            success=False,
            error=f"SSH connection failed: {msg}"
        )
    
    try:
        # Sanitize directory input
        safe_dir = directory.strip().strip('/')
        if '..' in safe_dir or safe_dir.startswith('/'):
            return InstalledPluginAnalysisResponse(
                success=False,
                error="Invalid directory path"
            )
        
        csgo_dir = f"{server.game_directory}/cs2/game/csgo"
        target_dir = f"{csgo_dir}/{safe_dir}"
        
        # Check if directory exists
        check_cmd = f"test -d {target_dir} && echo 'exists'"
        success, output, _ = await ssh_manager.execute_command(check_cmd)
        
        if 'exists' not in output:
            return InstalledPluginAnalysisResponse(
                success=False,
                error=f"Directory {safe_dir} does not exist"
            )
        
        # List all files and directories with sizes
        # Use find to get all files and directories recursively
        list_cmd = f"cd {target_dir} && find . -type f -exec ls -l {{}} \\; 2>/dev/null | awk '{{print $5 \" \" $9}}' || find . -type f 2>/dev/null"
        success, output, stderr = await ssh_manager.execute_command(list_cmd, timeout=30)
        
        if not success:
            return InstalledPluginAnalysisResponse(
                success=False,
                error=f"Failed to list files: {stderr}"
            )
        
        files = []
        total_size = 0
        
        if output.strip():
            for line in output.strip().split('\n'):
                line = line.strip()
                if not line or line == '.':
                    continue
                
                # Try to parse size and path
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    size = int(parts[0])
                    path = parts[1].strip().lstrip('./')
                else:
                    # Fallback if no size info
                    size = 0
                    path = line.lstrip('./')
                
                if path:
                    # Make path relative to csgo directory
                    full_path = f"{safe_dir}/{path}"
                    files.append(InstalledPluginFile(
                        path=full_path,
                        size=size,
                        is_dir=False
                    ))
                    total_size += size
        
        # Also get directories
        dir_cmd = f"cd {target_dir} && find . -type d 2>/dev/null | grep -v '^\\.\\?$' || echo ''"
        success, dir_output, _ = await ssh_manager.execute_command(dir_cmd, timeout=30)
        
        if success and dir_output.strip():
            for line in dir_output.strip().split('\n'):
                path = line.strip().lstrip('./')
                if path:
                    full_path = f"{safe_dir}/{path}"
                    # Only add if not already in files list
                    if not any(f.path == full_path for f in files):
                        files.append(InstalledPluginFile(
                            path=full_path,
                            size=0,
                            is_dir=True
                        ))
        
        return InstalledPluginAnalysisResponse(
            success=True,
            files=files,
            total_size=total_size
        )
        
    except Exception as e:
        logger.error(f"Error analyzing installed plugins: {e}")
        return InstalledPluginAnalysisResponse(
            success=False,
            error=f"Error analyzing plugins: {str(e)}"
        )
    finally:
        await ssh_manager.disconnect()


@router.post("/servers/{server_id}/uninstall", response_model=PluginUninstallResponse)
async def uninstall_plugin(
    server_id: int,
    request: PluginUninstallRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> PluginUninstallResponse:
    """
    Uninstall a plugin by deleting selected files.
    
    Args:
        server_id: Server ID
        request: Uninstall request with list of files to delete
    
    Returns:
        Uninstallation result
    """
    from api.routes.actions import send_deployment_update
    
    server = await get_server_and_verify_ownership(db, server_id, current_user)
    
    async def progress(msg: str, msg_type: str = "status"):
        """Send progress update via WebSocket"""
        await send_deployment_update(server_id, msg_type, msg)
    
    ssh_manager = SSHManager()
    success, msg = await ssh_manager.connect(server)
    if not success:
        await progress(f"SSH connection failed: {msg}", "error")
        return PluginUninstallResponse(
            success=False,
            message=f"SSH connection failed: {msg}"
        )
    
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
            failed_files=failed_files
        )
        
    except Exception as e:
        logger.error(f"Error uninstalling plugin: {e}")
        error_msg = f"Uninstallation error: {str(e)}"
        await progress(error_msg, "error")
        return PluginUninstallResponse(
            success=False,
            message=error_msg
        )
    finally:
        await ssh_manager.disconnect()

