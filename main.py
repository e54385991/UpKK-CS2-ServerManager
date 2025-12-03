"""
FastAPI application for CS2 Server Manager
Main entry point with organized structure
Using SQLModel for seamless FastAPI integration
"""
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
import os
import logging

from modules import init_db, migrate_db, settings, Server, get_db, ServerResponse, get_optional_current_user, User, setup_logging, _get_log_level
from services import redis_manager
from api.routes import servers, actions, setup, auth, server_status, public, captcha, file_manager, scheduled_tasks, github_plugins, plugin_market

# Initialize logging first (before anything else logs)
# Get log level from settings
log_level = _get_log_level(settings.LOG_LEVEL)
setup_logging(level=log_level, asyncssh_level=settings.ASYNCSSH_LOG_LEVEL)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="CS2 Server Manager",
    description="Manage multiple CS2 servers via FastAPI + Redis + MySQL with WebSocket support",
    version="1.0.0"
)

# Mount static files
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory="templates")

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


@app.on_event("startup")
async def startup_event():
    """Initialize database and start monitoring on startup"""
    # Run migrations first to add any missing columns to existing tables
    await migrate_db()
    # Then initialize database (create tables if they don't exist, create default admin)
    await init_db()
    
    # Start SSH connection pool cleanup task
    from services.ssh_connection_pool import ssh_connection_pool
    await ssh_connection_pool.start_cleanup()
    print("SSH connection pool started")
    
    # Clear old A2S cache to prevent double-encoding issues
    from services.redis_manager import redis_manager
    print("Clearing old A2S cache...")
    try:
        # Get all a2s cache keys
        keys = await redis_manager.client.keys("a2s:server:*")
        if keys:
            await redis_manager.client.delete(*keys)
            print(f"Cleared {len(keys)} old A2S cache entries")
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
    
    # Start scheduled task service
    from services.scheduled_task_service import scheduled_task_service
    await scheduled_task_service.start()
    print("Scheduled task service started")
    
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


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    # Stop SSH connection pool cleanup task and close all connections
    from services.ssh_connection_pool import ssh_connection_pool
    await ssh_connection_pool.stop_cleanup()
    await ssh_connection_pool.close_all()
    print("SSH connection pool stopped")
    
    # Stop A2S cache service
    from services.a2s_cache_service import a2s_cache_service
    a2s_cache_service.stop()
    
    # Stop steam.inf version cache service
    from services.steam_inf_service import steam_inf_service
    steam_inf_service.stop()
    
    # Stop auto-update service
    from services.auto_update_service import auto_update_service
    auto_update_service.stop()
    
    # Stop scheduled task service
    from services.scheduled_task_service import scheduled_task_service
    scheduled_task_service.stop()
    
    # Stop all monitoring tasks
    from services.server_monitor import server_monitor
    
    if server_monitor.monitoring_tasks:
        print(f"Stopping {len(server_monitor.monitoring_tasks)} monitoring task(s)...")
        for server_id in list(server_monitor.monitoring_tasks.keys()):
            server_monitor.stop_monitoring(server_id)
    
    await redis_manager.close()
    print("CS2 Server Manager shutdown complete!")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root endpoint - serve home page"""
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/deployment-tutorial", response_class=HTMLResponse)
async def deployment_tutorial_page(request: Request):
    """Deployment tutorial page"""
    return templates.TemplateResponse("deployment_tutorial.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page"""
    return templates.TemplateResponse("register.html", {"request": request})


@app.get("/servers-ui", response_class=HTMLResponse)
async def servers_ui(request: Request):
    """Servers management UI"""
    return templates.TemplateResponse("servers.html", {"request": request})


@app.get("/servers-ui/{server_id}", response_class=HTMLResponse)
async def server_detail_ui(request: Request, server_id: int):
    """Server detail UI with real-time monitoring"""
    from modules.database import async_session_maker
    
    async with async_session_maker() as db:
        server = await db.get(Server, server_id)
        
        if not server:
            raise HTTPException(status_code=404, detail=f"Server with ID {server_id} not found")
        
        # Convert to Pydantic model for JSON serialization
        server_data = ServerResponse.model_validate(server)
        # Create a JSON string for the JavaScript code
        server_json = server_data.model_dump_json()
        
        return templates.TemplateResponse("server_detail.html", {
            "request": request,
            "server": server,  # Pass original SQLAlchemy object for template attribute access
            "server_json": server_json  # Pass JSON string for JavaScript
        })


@app.get("/servers/{server_id}/console-popup/{console_type}", response_class=HTMLResponse)
async def console_popup(request: Request, server_id: int, console_type: str):
    """Console popup window"""
    from modules.database import async_session_maker
    
    async with async_session_maker() as db:
        server = await db.get(Server, server_id)
        
        if not server:
            raise HTTPException(status_code=404, detail=f"Server with ID {server_id} not found")
    
    return templates.TemplateResponse("console_popup.html", {
        "request": request,
        "server_id": server_id,
        "console_type": console_type.upper()
    })


@app.get("/plugin-market", response_class=HTMLResponse)
async def plugin_market_page(request: Request):
    """Plugin market page"""
    return templates.TemplateResponse("plugin_market.html", {"request": request})


@app.get("/servers/{server_id}/ssh-console", response_class=HTMLResponse)
async def ssh_console(request: Request, server_id: int):
    """Independent SSH console page"""
    from modules.database import async_session_maker
    
    async with async_session_maker() as db:
        server = await db.get(Server, server_id)
        
        if not server:
            raise HTTPException(status_code=404, detail=f"Server with ID {server_id} not found")
    
    return templates.TemplateResponse("ssh_console.html", {
        "request": request,
        "server_id": server_id
    })


@app.get("/servers/{server_id}/game-console", response_class=HTMLResponse)
async def game_console(request: Request, server_id: int):
    """Independent game console page"""
    from modules.database import async_session_maker
    
    async with async_session_maker() as db:
        server = await db.get(Server, server_id)
        
        if not server:
            raise HTTPException(status_code=404, detail=f"Server with ID {server_id} not found")
    
    return templates.TemplateResponse("game_console.html", {
        "request": request,
        "server_id": server_id
    })


@app.get("/servers/{server_id}/file-editor-popup", response_class=HTMLResponse)
async def file_editor_popup(request: Request, server_id: int, file_path: str, file_name: str):
    """File editor popup window"""
    from modules.database import async_session_maker
    
    async with async_session_maker() as db:
        server = await db.get(Server, server_id)
        
        if not server:
            raise HTTPException(status_code=404, detail=f"Server with ID {server_id} not found")
    
    # Fetch file content
    from services.ssh_manager import SSHManager
    ssh_manager = SSHManager()
    success, msg = await ssh_manager.connect(server)
    
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to connect to server: {msg}")
    
    try:
        # Read file content - execute_command returns (success, stdout, stderr)
        success, stdout, stderr = await ssh_manager.execute_command(f"cat {file_path}")
        
        if not success:
            raise HTTPException(status_code=500, detail=f"Failed to read file: {stderr}")
        
        file_content = stdout
        
        # Escape content for safe JavaScript embedding
        file_content = file_content.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
        
    finally:
        await ssh_manager.disconnect()
    
    return templates.TemplateResponse("file_editor_popup.html", {
        "request": request,
        "server_id": server_id,
        "file_path": file_path,
        "file_name": file_name,
        "file_content": file_content
    })


@app.get("/setup-wizard", response_class=HTMLResponse)
async def setup_wizard(request: Request):
    """Server setup wizard UI - authentication checked client-side"""
    return templates.TemplateResponse("server_setup_wizard.html", {"request": request})



@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    """User profile page"""
    return templates.TemplateResponse("profile.html", {"request": request})


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
