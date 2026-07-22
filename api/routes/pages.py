"""Server-rendered HTML page routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.routes import servers
from api.templating import templates
from modules import (
    ServerResponse,
    User,
    get_current_web_admin,
    get_current_web_user,
    get_db,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root endpoint - serve home page"""
    return templates.TemplateResponse(request, "home.html")


@router.get("/deployment-tutorial", response_class=HTMLResponse)
async def deployment_tutorial_page(request: Request):
    """Deployment tutorial page"""
    return templates.TemplateResponse(request, "deployment_tutorial.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    return templates.TemplateResponse(request, "login.html")


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page"""
    return templates.TemplateResponse(request, "register.html")


@router.get("/google-callback", response_class=HTMLResponse)
async def google_callback_page(request: Request):
    """Google OAuth callback page"""
    return templates.TemplateResponse(request, "google_callback.html")


@router.get("/servers-ui", response_class=HTMLResponse)
async def servers_ui(request: Request):
    """Servers management UI"""
    return templates.TemplateResponse(request, "servers.html")


@router.get("/servers-ui/{server_id}", response_class=HTMLResponse)
async def server_detail_ui(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_web_user),
):
    """Server detail UI with real-time monitoring"""
    server = await servers.get_server_with_permission(server_id, current_user, db)
    server_data = ServerResponse.model_validate(server)

    return templates.TemplateResponse(
        request,
        "server_detail.html",
        {
            "server": server,
            "server_json": server_data.model_dump_json(),
        },
    )


@router.get(
    "/servers/{server_id}/console-popup/{console_type}",
    response_class=HTMLResponse,
)
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

    return templates.TemplateResponse(
        request,
        "console_popup.html",
        {
            "server_id": server_id,
            "console_type": console_type.upper(),
        },
    )


@router.get("/plugin-market", response_class=HTMLResponse)
async def plugin_market_page(
    request: Request,
    _: User = Depends(get_current_web_user),
):
    """Plugin market page"""
    return templates.TemplateResponse(request, "plugin_market.html")


@router.get("/servers/{server_id}/ssh-console", response_class=HTMLResponse)
async def ssh_console(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_web_user),
):
    """Independent SSH console page"""
    await servers.get_server_with_permission(server_id, current_user, db)
    return templates.TemplateResponse(
        request,
        "ssh_console.html",
        {"server_id": server_id},
    )


@router.get("/servers/{server_id}/game-console", response_class=HTMLResponse)
async def game_console(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_web_user),
):
    """Independent game console page"""
    await servers.get_server_with_permission(server_id, current_user, db)
    return templates.TemplateResponse(
        request,
        "game_console.html",
        {"server_id": server_id},
    )


@router.get("/servers/{server_id}/file-editor-popup", response_class=HTMLResponse)
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

    from services.ssh_manager import SSHManager

    ssh_manager = SSHManager()
    success, message = await ssh_manager.connect(server)
    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect to server: {message}",
        )

    try:
        valid, validation_error = await ssh_manager.validate_path_within_base(
            server.game_directory,
            file_path,
            server,
            require_regular=True,
        )
        if not valid:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied: {validation_error}",
            )

        success, stdout, stderr = await ssh_manager.read_file(file_path, server)
        if not success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read file: {stderr}",
            )

        # Escape content for safe JavaScript template-literal embedding.
        file_content = stdout.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    finally:
        await ssh_manager.disconnect()

    return templates.TemplateResponse(
        request,
        "file_editor_popup.html",
        {
            "server_id": server_id,
            "file_path": file_path,
            "file_name": file_name,
            "file_content": file_content,
        },
    )


@router.get("/setup-wizard", response_class=HTMLResponse)
async def setup_wizard(
    request: Request,
    _: User = Depends(get_current_web_user),
):
    """Server setup wizard UI - authentication checked client-side"""
    return templates.TemplateResponse(request, "server_setup_wizard.html")


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    _: User = Depends(get_current_web_user),
):
    """User profile page"""
    return templates.TemplateResponse(request, "profile.html")


@router.get("/system-settings", response_class=HTMLResponse)
async def system_settings_page(
    request: Request,
    _: User = Depends(get_current_web_admin),
):
    """System settings page (admin only - auth checked client-side)"""
    return templates.TemplateResponse(request, "system_settings.html")


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    """Forgot password page"""
    return templates.TemplateResponse(request, "forgot_password.html")


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    """Reset password page"""
    return templates.TemplateResponse(request, "reset_password.html")
