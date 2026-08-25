"""
System settings routes (admin only)
"""

from fastapi import APIRouter, HTTPException, Query, Request, status

from api.dependencies import AdminUser, DatabaseSession
from modules import (
    AuditLogListResponse,
    EmailTestRequest,
    SystemSettings,
    SystemSettingsResponse,
    SystemSettingsUpdate,
)
from services.audit_log_service import (
    AUDIT_CATEGORIES,
    AUDIT_STATUSES,
    list_audit_logs,
    record_audit_event,
)
from services.email_service import email_service

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/audit-logs", response_model=AuditLogListResponse)
async def get_audit_logs(
    db: DatabaseSession,
    current_user: AdminUser,
    category: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    username: str | None = Query(default=None),
    ip_address: str | None = Query(default=None),
    server_id: int | None = Query(default=None),
    action: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List the last 30 days of administrator audit events."""
    if category and category not in AUDIT_CATEGORIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid category")
    if status_filter and status_filter not in AUDIT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")
    return await list_audit_logs(
        db,
        category=category,
        status=status_filter,
        username=username,
        ip_address=ip_address,
        server_id=server_id,
        action=action,
        limit=limit,
        offset=offset,
    )


@router.get("/settings", response_model=SystemSettingsResponse)
async def get_system_settings(db: DatabaseSession, current_user: AdminUser):
    """Get system settings (admin only)"""
    settings = await SystemSettings.get_or_create_settings(db)
    return settings


@router.put("/settings", response_model=SystemSettingsResponse)
async def update_system_settings(
    settings_update: SystemSettingsUpdate,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
):
    """Update system settings (admin only)"""
    settings = await SystemSettings.get_or_create_settings(db)

    # Update fields if provided
    update_data = settings_update.model_dump(exclude_unset=True)
    clear_global_github_token = update_data.pop("clear_global_github_token", False)
    global_github_token = update_data.pop("global_github_token", None)
    settings.sqlmodel_update(update_data)

    if clear_global_github_token:
        settings.global_github_token = None
    elif global_github_token and global_github_token.strip():
        settings.global_github_token = global_github_token.strip()

    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    await record_audit_event(
        category="settings",
        action="system.update",
        status="success",
        user=current_user,
        request=request,
        details={
            "changed_fields": [
                field
                for field in update_data
                if field
                not in {
                    "global_github_token",
                    "smtp_password",
                    "gmail_credentials_json",
                }
            ]
            + (["global_github_token"] if clear_global_github_token or global_github_token else [])
        },
    )

    return settings


@router.post("/settings/test-email")
async def test_email(
    request: EmailTestRequest,
    db: DatabaseSession,
    current_user: AdminUser,
):
    """Send a test email to verify email configuration (admin only)"""

    # Create test email content
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #28a745; color: white; padding: 20px; text-align: center; }}
            .content {{ padding: 20px; background-color: #f9f9f9; }}
            .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>✅ Email Test Successful</h1>
            </div>
            <div class="content">
                <p>This is a test email from CS2 Server Manager.</p>
                <p>If you're reading this, your email configuration is working correctly!</p>
                <p><strong>Configuration Details:</strong></p>
                <ul>
                    <li>System: CS2 Server Manager</li>
                    <li>Test initiated by: {current_user.username}</li>
                </ul>
            </div>
            <div class="footer">
                <p>This is an automated test message from CS2 Server Manager.</p>
            </div>
        </div>
    </body>
    </html>
    """

    text_content = f"""
    Email Test Successful
    
    This is a test email from CS2 Server Manager.
    
    If you're reading this, your email configuration is working correctly!
    
    Configuration Details:
    - System: CS2 Server Manager
    - Test initiated by: {current_user.username}
    
    ---
    This is an automated test message from CS2 Server Manager.
    """

    # Send test email
    success = await email_service.send_email(
        db, request.test_email, "CS2 Server Manager - Email Test", html_content, text_content
    )

    if success:
        return {"success": True, "message": f"Test email sent successfully to {request.test_email}"}
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send test email. Please check your email configuration and server logs.",
        )
