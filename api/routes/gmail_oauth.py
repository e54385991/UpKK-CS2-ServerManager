"""
Gmail OAuth2 routes for system settings
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import json
import logging

from modules import (
    get_db, User, get_current_admin_user,
    SystemSettings, settings, GmailCredentialsUploadRequest
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gmail-oauth", tags=["gmail-oauth"])


@router.get("/authorize")
async def gmail_oauth_authorize(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Start Gmail OAuth2 authorization flow (admin only)
    
    This endpoint redirects the user to Google's OAuth consent screen.
    After authorization, Google will redirect back to /api/gmail-oauth/callback
    """
    try:
        from google_auth_oauthlib.flow import Flow
        
        # Get system settings to check if credentials are configured
        sys_settings = await SystemSettings.get_or_create_settings(db)
        
        if not sys_settings.gmail_credentials_json:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gmail API credentials not configured. Please upload credentials JSON first."
            )
        
        # Parse credentials JSON
        try:
            credentials_info = json.loads(sys_settings.gmail_credentials_json)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Gmail credentials JSON format"
            )
        
        # Create OAuth flow
        flow = Flow.from_client_config(
            credentials_info,
            scopes=['https://www.googleapis.com/auth/gmail.send'],
            redirect_uri=f"{settings.BACKEND_URL}/api/gmail-oauth/callback"
        )
        
        # Generate authorization URL
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'  # Force consent screen to get refresh token
        )
        
        # Store state in session or cache for verification in callback
        # For simplicity, we'll store it in the credentials JSON temporarily
        # In production, use Redis or session storage
        
        return {
            "authorization_url": authorization_url,
            "state": state
        }
        
    except ImportError as e:
        logger.error(f"Gmail OAuth libraries not installed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gmail OAuth libraries not installed. Please install google-auth-oauthlib."
        )
    except Exception as e:
        logger.error(f"Error starting Gmail OAuth flow: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start OAuth flow: {str(e)}"
        )


@router.get("/callback")
async def gmail_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle OAuth2 callback from Google
    
    This endpoint is called by Google after the user authorizes the application.
    It exchanges the authorization code for access and refresh tokens.
    """
    try:
        # Check for errors from OAuth provider
        if error:
            logger.error(f"OAuth error: {error}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"OAuth authorization failed: {error}"
            )
        
        if not code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authorization code not provided"
            )
        
        from google_auth_oauthlib.flow import Flow
        
        # Get system settings
        sys_settings = await SystemSettings.get_or_create_settings(db)
        
        if not sys_settings.gmail_credentials_json:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gmail API credentials not configured"
            )
        
        # Parse credentials JSON
        credentials_info = json.loads(sys_settings.gmail_credentials_json)
        
        # Create OAuth flow
        flow = Flow.from_client_config(
            credentials_info,
            scopes=['https://www.googleapis.com/auth/gmail.send'],
            redirect_uri=f"{settings.BACKEND_URL}/api/gmail-oauth/callback"
        )
        
        # Exchange authorization code for tokens
        flow.fetch_token(code=code)
        
        # Get credentials
        credentials = flow.credentials
        
        # Store token information
        token_data = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }
        
        # Save token to database
        sys_settings.gmail_token_json = json.dumps(token_data)
        db.add(sys_settings)
        await db.commit()
        
        logger.info("Gmail OAuth token saved successfully")
        
        # Redirect to system settings page with success message
        return RedirectResponse(
            url="/system-settings?gmail_auth=success",
            status_code=status.HTTP_302_FOUND
        )
        
    except Exception as e:
        logger.error(f"Error in Gmail OAuth callback: {e}", exc_info=True)
        # Redirect to system settings with error
        return RedirectResponse(
            url="/system-settings?gmail_auth=error",
            status_code=status.HTTP_302_FOUND
        )


@router.post("/upload-credentials")
async def upload_gmail_credentials(
    request: GmailCredentialsUploadRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Upload Gmail API credentials JSON (admin only)
    
    Args:
        request: Request body containing the credentials JSON
    """
    try:
        # Validate JSON format
        credentials_data = json.loads(request.credentials_json)
        
        # Verify it has the expected structure
        if 'web' not in credentials_data and 'installed' not in credentials_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid credentials JSON format. Please upload the credentials.json file from Google Cloud Console."
            )
        
        # Get system settings
        sys_settings = await SystemSettings.get_or_create_settings(db)
        
        # Save credentials
        sys_settings.gmail_credentials_json = request.credentials_json
        db.add(sys_settings)
        await db.commit()
        
        return {
            "success": True,
            "message": "Gmail credentials uploaded successfully. You can now authorize the application."
        }
        
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON format"
        )
    except Exception as e:
        logger.error(f"Error uploading Gmail credentials: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload credentials: {str(e)}"
        )


@router.delete("/revoke")
async def revoke_gmail_authorization(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Revoke Gmail API authorization and clear stored tokens (admin only)
    """
    try:
        # Get system settings
        sys_settings = await SystemSettings.get_or_create_settings(db)
        
        # Clear token
        sys_settings.gmail_token_json = None
        db.add(sys_settings)
        await db.commit()
        
        return {
            "success": True,
            "message": "Gmail authorization revoked successfully"
        }
        
    except Exception as e:
        logger.error(f"Error revoking Gmail authorization: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to revoke authorization: {str(e)}"
        )


@router.get("/status")
async def gmail_oauth_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Check Gmail OAuth configuration status (admin only)
    """
    try:
        sys_settings = await SystemSettings.get_or_create_settings(db)
        
        return {
            "credentials_configured": bool(sys_settings.gmail_credentials_json),
            "token_configured": bool(sys_settings.gmail_token_json),
            "ready": bool(sys_settings.gmail_credentials_json and sys_settings.gmail_token_json)
        }
        
    except Exception as e:
        logger.error(f"Error checking Gmail OAuth status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check status: {str(e)}"
        )
