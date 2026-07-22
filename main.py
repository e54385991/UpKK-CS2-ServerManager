"""
FastAPI application for CS2 Server Manager
Main entry point with organized structure
Using SQLModel for seamless FastAPI integration
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os
import logging
import uuid
import asyncio

from modules import (
    init_db, migrate_db, settings, Server, ServerResponse, User, get_db,
    get_current_web_user, get_current_web_admin, setup_logging, _get_log_level,
)
from services import redis_manager
from services.maintenance_lock import OperationBusyError
from api.routes import servers, actions, setup, auth, server_status, public, captcha, file_manager, scheduled_tasks, github_plugins, plugin_market, plugin_auto_update, plugin_configs, system_settings, gmail_oauth, map_management

# Initialize logging first (before anything else logs)
# Get log level from settings
log_level = _get_log_level(settings.LOG_LEVEL)
setup_logging(level=log_level, asyncssh_level=settings.ASYNCSSH_LOG_LEVEL)
logger = logging.getLogger(__name__)
STATIC_ASSET_VERSION = uuid.uuid4().hex


def static_url(path: str) -> str:
    """Return a cache-busted static asset URL for this app process."""
    normalized_path = path.lstrip("/")
    separator = "&" if "?" in normalized_path else "?"
    return f"/static/{normalized_path}{separator}v={STATIC_ASSET_VERSION}"

async def startup_event():
    """Initialize database and start monitoring on startup"""
    # migrate_db first creates missing tables, then adds columns that create_all
    # cannot add to an existing installation.
    await migrate_db()
    await init_db()
    
    # Start SSH connection pool cleanup task
    from services.ssh_connection_pool import ssh_connection_pool
    await ssh_connection_pool.start_cleanup()
    print("SSH connection pool started")
    
    # Clear old A2S cache to prevent double-encoding issues
    from services.redis_manager import redis_manager
    print("Clearing old A2S cache...")
    try:
        deleted = await redis_manager.delete_by_pattern("a2s:server:*")
        if deleted:
            print(f"Cleared {deleted} old A2S cache entries")
        else:
            print("No old A2S cache entries to clear")
    except Exception as e:
        print(f"Error clearing A2S cache: {e}")
    
    # Start A2S cache service
    from services.a2s_cache_service import a2s_cache_service
    await a2s_cache_service.start()
    print("A2S cache service started")
    
    # Start steam.inf version cache service
    from services.steam_inf_service import steam_inf_service
    await steam_inf_service.start()
    print("Steam.inf version cache service started")
    
    # Start auto-update service
    from services.auto_update_service import auto_update_service
    await auto_update_service.start()
    print("Auto-update service started")

    from services.plugin_auto_update_service import plugin_auto_update_service
    await plugin_auto_update_service.start()
    print("Plugin auto-update service started")
    
    # Start scheduled task service
    from services.scheduled_task_service import scheduled_task_service
    await scheduled_task_service.start()
    print("Scheduled task service started")
    
    # Start SSH health monitoring daemon
    from services.ssh_health_monitor import ssh_health_monitor
    await ssh_health_monitor.start()
    print("SSH health monitoring daemon started")
    
    # Start monitoring for servers with panel monitoring enabled
    from modules.database import async_session_maker
    from services.server_monitor import server_monitor
    from services.ssh_manager import SSHManager
    
    async with async_session_maker() as db:
        monitored_servers = await Server.get_all_with_panel_monitoring(db)
        
        if monitored_servers:
            print(f"Starting panel monitoring for {len(monitored_servers)} server(s)...")
            for server in monitored_servers:
                ssh_manager = SSHManager()
                server_monitor.start_monitoring(server.id, ssh_manager)
                print(f"  - Started monitoring for server {server.id} ({server.name})")
        else:
            print("No servers configured for panel monitoring")
    
    print("CS2 Server Manager started successfully!")


async def shutdown_event():
    """Cleanup on shutdown"""
    await asyncio.gather(
        actions.shutdown_background_tasks(),
        file_manager.shutdown_background_tasks(),
        plugin_auto_update.shutdown_background_tasks(),
        return_exceptions=True,
    )

    # Stop and await every service which may still use SSH, Redis, HTTP or DB resources.
    from services.a2s_cache_service import a2s_cache_service
    from services.steam_inf_service import steam_inf_service
    from services.auto_update_service import auto_update_service
    from services.plugin_auto_update_service import plugin_auto_update_service
    from services.scheduled_task_service import scheduled_task_service
    from services.ssh_health_monitor import ssh_health_monitor
    from services.server_monitor import server_monitor
    from services.discord_notification_service import discord_notification_service
    from services.ssh_manager import shutdown_background_tasks as shutdown_ssh_manager_tasks

    service_results = await asyncio.gather(
        a2s_cache_service.stop(),
        steam_inf_service.stop(),
        auto_update_service.stop(),
        plugin_auto_update_service.stop(),
        scheduled_task_service.stop(),
        ssh_health_monitor.stop(),
        server_monitor.stop_all(),
        discord_notification_service.shutdown(),
        shutdown_ssh_manager_tasks(),
        return_exceptions=True,
    )
    for result in service_results:
        if isinstance(result, Exception):
            logger.error("Background service shutdown failed", exc_info=result)

    # Shared transports close only after their consumers have stopped.
    from services.ssh_connection_pool import ssh_connection_pool
    await ssh_connection_pool.stop_cleanup()
    await ssh_connection_pool.close_all()
    print("SSH connection pool stopped")

    await redis_manager.close()
    from modules.http_helper import http_helper
    from modules.database import engine
    await http_helper.close()
    await engine.dispose()
    print("CS2 Server Manager shutdown complete!")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    try:
        await startup_event()
        yield
    finally:
        await shutdown_event()


# Create FastAPI app
app = FastAPI(
    title="CS2 Server Manager",
    description="Manage multiple CS2 servers via FastAPI + Redis + MySQL with WebSocket support",
    version="1.0.0",
    lifespan=lifespan
)


@app.exception_handler(OperationBusyError)
async def operation_busy_handler(request: Request, exc: OperationBusyError):
    """Return a stable conflict response for distributed server-operation locks."""
    return JSONResponse(status_code=409, content={"detail": str(exc)})

# Mount static files
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory="templates")
templates.env.globals["static_url"] = static_url
templates.env.globals["static_version"] = STATIC_ASSET_VERSION

# Include routers
# PUBLIC ROUTER FIRST - no authentication, no prefix
app.include_router(public.router)
# CAPTCHA ROUTER - no authentication required for generation
app.include_router(captcha.router)
# Then authenticated routers
app.include_router(auth.router)
app.include_router(servers.router)
app.include_router(actions.router)
app.include_router(setup.router)
app.include_router(server_status.router)
app.include_router(file_manager.router)
app.include_router(scheduled_tasks.router)
app.include_router(github_plugins.router)
app.include_router(plugin_market.router)
app.include_router(plugin_auto_update.router)
app.include_router(plugin_configs.router)
app.include_router(system_settings.router)
app.include_router(gmail_oauth.router)
app.include_router(map_management.router)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root endpoint - serve home page"""
    return templates.TemplateResponse(request, "home.html")


@app.get("/deployment-tutorial", response_class=HTMLResponse)
async def deployment_tutorial_page(request: Request):
    """Deployment tutorial page"""
    return templates.TemplateResponse(request, "deployment_tutorial.html")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    return templates.TemplateResponse(request, "login.html")


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page"""
    return templates.TemplateResponse(request, "register.html")


@app.get("/google-callback", response_class=HTMLResponse)
async def google_callback_page(request: Request):
    """Google OAuth callback page"""
    return templates.TemplateResponse(request, "google_callback.html")


@app.get("/servers-ui", response_class=HTMLResponse)
async def servers_ui(request: Request):
    """Servers management UI"""
    return templates.TemplateResponse(request, "servers.html")


@app.get("/servers-ui/{server_id}", response_class=HTMLResponse)
async def server_detail_ui(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_web_user),
):
    """Server detail UI with real-time monitoring"""
    server = await servers.get_server_with_permission(server_id, current_user, db)
    server_data = ServerResponse.model_validate(server)
    server_json = server_data.model_dump_json()

    return templates.TemplateResponse(request, "server_detail.html", {
        "server": server,
        "server_json": server_json,
    })


@app.get("/servers/{server_id}/console-popup/{console_type}", response_class=HTMLResponse)
async def console_popup(
    request: Request,
    server_id: int,
    console_type: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_web_user),
):
    """Console popup window"""
    if console_type.lower() not in {"ssh", "game"}:
        raise HTTPException(status_code=404, detail="Unsupported console type")
    await servers.get_server_with_permission(server_id, current_user, db)
    
    return templates.TemplateResponse(request, "console_popup.html", {
        "server_id": server_id,
        "console_type": console_type.upper()
    })


@app.get("/plugin-market", response_class=HTMLResponse)
async def plugin_market_page(request: Request, _: User = Depends(get_current_web_user)):
    """Plugin market page"""
    return templates.TemplateResponse(request, "plugin_market.html")


@app.get("/servers/{server_id}/ssh-console", response_class=HTMLResponse)
async def ssh_console(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_web_user),
):
    """Independent SSH console page"""
    await servers.get_server_with_permission(server_id, current_user, db)
    
    return templates.TemplateResponse(request, "ssh_console.html", {
        "server_id": server_id
    })


@app.get("/servers/{server_id}/game-console", response_class=HTMLResponse)
async def game_console(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_web_user),
):
    """Independent game console page"""
    await servers.get_server_with_permission(server_id, current_user, db)
    
    return templates.TemplateResponse(request, "game_console.html", {
        "server_id": server_id
    })


@app.get("/servers/{server_id}/file-editor-popup", response_class=HTMLResponse)
async def file_editor_popup(
    request: Request,
    server_id: int,
    file_path: str,
    file_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_web_user),
):
    """File editor popup window"""
    server = await servers.get_server_with_permission(server_id, current_user, db)
    
    # Fetch file content
    from services.ssh_manager import SSHManager
    ssh_manager = SSHManager()
    success, msg = await ssh_manager.connect(server)
    
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to connect to server: {msg}")
    
    try:
        valid, validation_error = await ssh_manager.validate_path_within_base(
            server.game_directory,
            file_path,
            server,
            require_regular=True,
        )
        if not valid:
            raise HTTPException(status_code=403, detail=f"Access denied: {validation_error}")

        success, stdout, stderr = await ssh_manager.read_file(file_path, server)
        
        if not success:
            raise HTTPException(status_code=500, detail=f"Failed to read file: {stderr}")
        
        file_content = stdout
        
        # Escape content for safe JavaScript embedding
        file_content = file_content.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
        
    finally:
        await ssh_manager.disconnect()
    
    return templates.TemplateResponse(request, "file_editor_popup.html", {
        "server_id": server_id,
        "file_path": file_path,
        "file_name": file_name,
        "file_content": file_content
    })


@app.get("/setup-wizard", response_class=HTMLResponse)
async def setup_wizard(request: Request, _: User = Depends(get_current_web_user)):
    """Server setup wizard UI - authentication checked client-side"""
    return templates.TemplateResponse(request, "server_setup_wizard.html")



@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, _: User = Depends(get_current_web_user)):
    """User profile page"""
    return templates.TemplateResponse(request, "profile.html")


@app.get("/system-settings", response_class=HTMLResponse)
async def system_settings_page(request: Request, _: User = Depends(get_current_web_admin)):
    """System settings page (admin only - auth checked client-side)"""
    return templates.TemplateResponse(request, "system_settings.html")


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    """Forgot password page"""
    return templates.TemplateResponse(request, "forgot_password.html")


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    """Reset password page"""
    return templates.TemplateResponse(request, "reset_password.html")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    redis_status = await redis_manager.ping()
    return {
        "status": "healthy",
        "redis": "connected" if redis_status else "disconnected",
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG
    )
