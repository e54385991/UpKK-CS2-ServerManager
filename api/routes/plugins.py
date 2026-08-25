"""
Plugin management routes for CounterStrikeSharp plugins
"""

import json
import math
import os
from typing import Optional

from anyio import to_thread
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.dependencies import (
    ActiveUser,
    DatabaseSession,
    LockedServerOperation,
)
from modules import (
    InstalledPlugin,
    InstalledPluginResponse,
    Plugin,
    PluginCategory,
    PluginCreate,
    PluginInstallRequest,
    PluginListResponse,
    PluginResponse,
    Server,
)
from services import SSHManager

router = APIRouter(prefix="/api/plugins", tags=["plugins"])
MAX_PLUGIN_UPLOAD_BYTES = 512 * 1024 * 1024


@router.get("/categories", response_model=list)
async def get_plugin_categories():
    """Get all available plugin categories"""
    return [
        {"value": category.value, "label": category.value.capitalize()}
        for category in PluginCategory
    ]


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    category: Optional[str] = Query(None, description="Filter by category"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: DatabaseSession = None,
):
    """
    Get paginated list of available plugins
    Optionally filter by category
    """
    skip = (page - 1) * page_size

    # Build query based on filters
    if category and category in [c.value for c in PluginCategory]:
        plugin_category = PluginCategory(category)
        plugins = await Plugin.get_by_category(db, plugin_category, skip=skip, limit=page_size)
        total = await Plugin.count_by_category(db, plugin_category)
    else:
        plugins = await Plugin.get_all_enabled(db, skip=skip, limit=page_size)
        total = await Plugin.count_by_category(db, None)

    total_pages = math.ceil(total / page_size) if total > 0 else 0

    return PluginListResponse(
        plugins=[PluginResponse.model_validate(p) for p in plugins],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{plugin_id}", response_model=PluginResponse)
async def get_plugin(plugin_id: int, db: DatabaseSession):
    """Get details of a specific plugin"""
    plugin = await db.get(Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    return PluginResponse.model_validate(plugin)


@router.post("", response_model=PluginResponse)
async def create_plugin(
    plugin: PluginCreate,
    current_user: ActiveUser,
    db: DatabaseSession,
):
    """
    Create a new plugin in the catalog
    Admin only
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only administrators can create plugins"
        )

    # Verify category is valid
    try:
        plugin_category = PluginCategory(plugin.category)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category. Must be one of: {', '.join([c.value for c in PluginCategory])}",
        ) from None

    new_plugin = Plugin(**plugin.model_dump(), category=plugin_category)

    db.add(new_plugin)
    await db.commit()
    await db.refresh(new_plugin)

    return PluginResponse.model_validate(new_plugin)


@router.post("/upload", response_model=PluginResponse)
async def upload_plugin(
    file: UploadFile = File(..., description="Plugin tar.gz file"),
    name: str = Form(..., description="Plugin unique identifier (alphanumeric + dash/underscore)"),
    display_name: str = Form(..., description="User-friendly display name"),
    description: str = Form(..., description="Plugin description"),
    category: str = Form(..., description="Plugin category"),
    version: str = Form(..., description="Plugin version"),
    author: Optional[str] = Form(None, description="Plugin author"),
    homepage: Optional[str] = Form(None, description="Plugin homepage URL"),
    dependencies: Optional[str] = Form(None, description="JSON array of plugin IDs"),
    install_path: str = Form(
        default="addons/counterstrikesharp/plugins", description="Installation path"
    ),
    config_required: bool = Form(
        default=False, description="Whether plugin requires configuration"
    ),
    current_user: ActiveUser = None,
    db: DatabaseSession = None,
):
    """
    Upload a plugin file and add it to the catalog
    Admin only

    The uploaded file will be stored in a static directory and served for installations.
    """
    import logging
    import re

    logger = logging.getLogger(__name__)

    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only administrators can upload plugins"
        )

    # Validate file type
    if not file.filename.endswith(".tar.gz"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Plugin file must be a .tar.gz archive"
        )

    # Validate plugin name (alphanumeric + dash/underscore only)
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Plugin name must contain only alphanumeric characters, dashes, and underscores",
        )

    # Verify category is valid
    try:
        plugin_category = PluginCategory(category)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category. Must be one of: {', '.join([c.value for c in PluginCategory])}",
        ) from None

    await db.commit()
    # Create uploads directory if it doesn't exist
    upload_dir = os.path.join(os.getcwd(), "static", "uploads", "plugins")
    os.makedirs(upload_dir, exist_ok=True)

    # Generate safe filename
    safe_version = "".join(c for c in version if c.isalnum() or c in ".-_")
    filename = f"{name}_{safe_version}.tar.gz"
    file_path = os.path.join(upload_dir, filename)

    try:
        # Save uploaded file
        def copy_upload() -> None:
            with open(file_path, "wb") as buffer:
                copied = 0
                while chunk := file.file.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > MAX_PLUGIN_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail="Plugin archive exceeds the 512 MiB limit",
                        )
                    buffer.write(chunk)

        await to_thread.run_sync(copy_upload)

        logger.info(f"Plugin file uploaded: {file_path}")

        # Generate download URL (relative to static directory)
        # This assumes the app serves static files from /static
        download_url = f"/static/uploads/plugins/{filename}"

        # Create plugin entry in database
        new_plugin = Plugin(
            name=name,
            display_name=display_name,
            description=description,
            category=plugin_category,
            version=version,
            download_url=download_url,
            author=author,
            homepage=homepage,
            dependencies=dependencies,
            install_path=install_path,
            config_required=config_required,
            enabled=True,
        )

        db.add(new_plugin)
        await db.commit()
        await db.refresh(new_plugin)

        logger.info(f"Plugin added to catalog: {name} v{version}")

        return PluginResponse.model_validate(new_plugin)

    except HTTPException:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise
    except Exception as e:
        logger.exception(f"Error uploading plugin: {e}")
        # Clean up uploaded file if database operation fails
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload plugin: {str(e)}",
        ) from e


@router.get("/servers/{server_id}/installed", response_model=list[InstalledPluginResponse])
async def get_installed_plugins(
    server_id: int,
    current_user: ActiveUser,
    db: DatabaseSession,
):
    """Get all plugins installed on a specific server"""
    # Verify user owns the server
    server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    installed_plugins = await InstalledPlugin.get_by_server(db, server_id)

    # Fetch plugin details for each installed plugin
    result = []
    for installed in installed_plugins:
        plugin = await db.get(Plugin, installed.plugin_id)
        response = InstalledPluginResponse.model_validate(installed)
        if plugin:
            response.plugin = PluginResponse.model_validate(plugin)
        result.append(response)

    return result


@router.post("/servers/{server_id}/install")
async def install_plugin(
    server_id: int,
    install_request: PluginInstallRequest,
    current_user: ActiveUser,
    db: DatabaseSession,
    _operation_server: LockedServerOperation,
):
    """Install a plugin on a specific server"""
    # Verify user owns the server
    server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    # Get plugin details
    plugin = await db.get(Plugin, install_request.plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    # Check if already installed
    existing = await InstalledPlugin.get_by_server_and_plugin(
        db, server_id, install_request.plugin_id
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Plugin is already installed on this server",
        )

    # Check and install dependencies first
    if plugin.dependencies:
        try:
            dependency_ids = json.loads(plugin.dependencies)
            for dep_id in dependency_ids:
                dep_installed = await InstalledPlugin.get_by_server_and_plugin(
                    db, server_id, dep_id
                )
                if not dep_installed:
                    # Auto-install dependency
                    dep_plugin = await db.get(Plugin, dep_id)
                    if dep_plugin:
                        await _install_plugin_to_server(server, dep_plugin, None, None, db)
        except json.JSONDecodeError, TypeError:
            pass  # Invalid JSON, skip dependency check

    # Install the plugin
    success = await _install_plugin_to_server(
        server, plugin, install_request.custom_download_url, install_request.config_data, db
    )

    if success:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "success": True,
                "message": f"Plugin {plugin.display_name} installed successfully",
            },
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to install plugin"
        )


@router.delete("/servers/{server_id}/installed/{installed_plugin_id}")
async def uninstall_plugin(
    server_id: int,
    installed_plugin_id: int,
    current_user: ActiveUser,
    db: DatabaseSession,
    _operation_server: LockedServerOperation,
):
    """Uninstall a plugin from a specific server"""
    import logging
    import shlex

    logger = logging.getLogger(__name__)

    # Verify user owns the server
    server = await Server.get_by_id_and_user(db, server_id, current_user.id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    # Get installed plugin
    result = await db.execute(
        select(InstalledPlugin).where(
            InstalledPlugin.id == installed_plugin_id, InstalledPlugin.server_id == server_id
        )
    )
    installed_plugin = result.scalar_one_or_none()

    if not installed_plugin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Installed plugin not found"
        )

    # Get plugin details for the name and path
    plugin = await db.get(Plugin, installed_plugin.plugin_id)
    plugin_name = plugin.display_name if plugin else "Plugin"

    await db.commit()
    # Remove plugin files from server
    if plugin:
        ssh_manager = SSHManager()
        try:
            success, msg = await ssh_manager.connect(server)
            if success:
                # Generate safe plugin directory name (same logic as install)
                safe_name = "".join(c for c in plugin.name if c.isalnum() or c in "-_")
                plugin_dir = f"{server.game_directory}/game/csgo/{plugin.install_path}/{safe_name}"

                # Remove plugin directory
                await ssh_manager.execute_command(f"rm -rf {shlex.quote(plugin_dir)}")
                logger.info(f"Removed plugin files for {plugin.name} from server {server_id}")
            else:
                logger.warning(
                    f"Could not connect to server {server_id} to remove plugin files: {msg}"
                )
        except Exception as e:
            logger.exception(f"Error removing plugin files from server {server_id}: {e}")
        finally:
            await ssh_manager.disconnect()

    # Delete from database
    await db.delete(installed_plugin)
    await db.commit()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"success": True, "message": f"{plugin_name} uninstalled successfully"},
    )


async def _install_plugin_to_server(
    server: Server,
    plugin: Plugin,
    custom_url: Optional[str],
    config_data: Optional[str],
    db: AsyncSession,
) -> bool:
    """
    Internal function to install a plugin to a server
    Downloads the plugin file and extracts it to the correct location

    Note: Plugin archives should contain a directory named after the plugin
    (matching plugin.name sanitized to alphanumeric + dashes/underscores)
    to ensure proper cleanup during uninstallation.
    """
    import logging
    import shlex

    logger = logging.getLogger(__name__)
    await db.commit()
    ssh_manager = SSHManager()

    try:
        # Connect to server
        success, msg = await ssh_manager.connect(server)
        if not success:
            logger.error(f"Failed to connect to server {server.id}: {msg}")
            return False

        download_url = custom_url if custom_url else plugin.download_url
        install_path = f"{server.game_directory}/game/csgo/{plugin.install_path}"

        # Create installation directory (use proper escaping)
        success, stdout, stderr = await ssh_manager.execute_command(
            f"mkdir -p {shlex.quote(install_path)}"
        )
        if not success:
            logger.error(f"Failed to create directory {install_path}: {stderr}")
            return False

        # Download plugin archive (properly escape URL and filename)
        # Generate safe temp filename from plugin name and version (alphanumeric only)
        safe_name = "".join(c for c in plugin.name if c.isalnum() or c in "-_")
        safe_version = "".join(c for c in plugin.version if c.isalnum() or c in ".-_")
        temp_file = f"/tmp/{safe_name}_{safe_version}.tar.gz"

        # Download with security options: timeout, SSL verification
        success, stdout, stderr = await ssh_manager.execute_command(
            f"wget --timeout=60 --tries=3 -O {shlex.quote(temp_file)} {shlex.quote(download_url)}"
        )
        if not success:
            logger.error(f"Failed to download plugin from {download_url}: {stderr}")
            return False

        # Extract archive with security options to prevent directory traversal
        success, stdout, stderr = await ssh_manager.execute_command(
            f"tar --no-absolute-filenames -xzf {shlex.quote(temp_file)} -C {shlex.quote(install_path)}"
        )
        if not success:
            logger.error(f"Failed to extract plugin archive: {stderr}")
            return False

        # Clean up temp file
        await ssh_manager.execute_command(f"rm -f {shlex.quote(temp_file)}")

        # Record installation in database
        installed_plugin = InstalledPlugin(
            server_id=server.id,
            plugin_id=plugin.id,
            version=plugin.version,
            custom_download_url=custom_url,
            config_data=config_data,
        )

        db.add(installed_plugin)
        await db.commit()

        logger.info(
            f"Successfully installed plugin {plugin.name} v{plugin.version} on server {server.id}"
        )
        return True

    except Exception as e:
        logger.exception(f"Error installing plugin {plugin.name} on server {server.id}: {e}")
        return False
    finally:
        await ssh_manager.disconnect()
