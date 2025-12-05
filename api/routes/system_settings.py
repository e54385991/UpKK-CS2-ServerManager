"""
System settings routes (admin only)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from services.email_service import email_service

from modules import (
    get_db, User, get_current_admin_user,
    SystemSettings, SystemSettingsResponse, SystemSettingsUpdate,
    EmailTestRequest
)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/settings", response_model=SystemSettingsResponse)
async def get_system_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """Get system settings (admin only)"""
    settings = await SystemSettings.get_or_create_settings(db)
    return settings


@router.put("/settings", response_model=SystemSettingsResponse)
async def update_system_settings(
    settings_update: SystemSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """Update system settings (admin only)"""
    settings = await SystemSettings.get_or_create_settings(db)
    
    # Update fields if provided
    update_data = settings_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(settings, field, value)
    
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    
    return settings


@router.post("/settings/test-email")
async def test_email(
    request: EmailTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
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
        db,
        request.test_email,
        "CS2 Server Manager - Email Test",
        html_content,
        text_content
    )
    
    if success:
        return {
            "success": True,
            "message": f"Test email sent successfully to {request.test_email}"
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send test email. Please check your email configuration and server logs."
        )
