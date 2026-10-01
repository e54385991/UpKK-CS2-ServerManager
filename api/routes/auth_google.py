"""Focused route implementation; the original module retains patchable dependencies."""

from __future__ import annotations

from fastapi import Request, Response

from api.dependencies import DatabaseSession
from modules import (
    GoogleOAuthRequest,
    Token,
)
from services.compat import LateBoundModule

host = LateBoundModule("api.routes.auth")
router = host.router


@router.post("/google-oauth", response_model=Token)
async def google_oauth_login(
    oauth_data: GoogleOAuthRequest,
    request: Request,
    response: Response,
    db: DatabaseSession,
):
    """
    Google OAuth login/register endpoint

    If user exists with this Google ID, log them in.
    If user doesn't exist, register a new user with username and password from request.
    Email is auto-bound from Google account.
    """
    logger = host.logging.getLogger(__name__)
    await host.enforce_rate_limit(request, "google_oauth", limit=10, window=60)

    try:
        # Verify the Google ID token
        try:
            # Verify with Google Client ID if configured
            client_id = await host.resolve_google_client_id(db)
            if not client_id:
                raise host.HTTPException(
                    status_code=host.status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Google OAuth is not configured. Set the client ID in Settings or GOOGLE_CLIENT_ID.",
                )

            idinfo = await host.to_thread.run_sync(
                host.id_token.verify_oauth2_token,
                oauth_data.id_token,
                host.requests.Request(),
                client_id,
            )

            # Get user info from token
            google_user_id = idinfo["sub"]
            email = idinfo.get("email")

            if not email:
                raise host.HTTPException(
                    status_code=host.status.HTTP_400_BAD_REQUEST,
                    detail="Email not provided by Google. Please ensure email permission is granted.",
                )

        except ValueError as e:
            logger.error(f"Invalid Google token: {e}")
            await host.record_audit_event(
                category="auth",
                action="google_oauth",
                status="failure",
                request=request,
                details={"reason": "invalid_token"},
            )
            raise host.HTTPException(
                status_code=host.status.HTTP_401_UNAUTHORIZED, detail="Invalid Google ID token"
            ) from e

        # Check if user exists with this Google ID
        user = await host.User.get_by_google_id(db, google_user_id)

        if user:
            # User exists, log them in
            if not user.is_active:
                raise host.HTTPException(
                    status_code=host.status.HTTP_400_BAD_REQUEST, detail="User account is inactive"
                )

            # Create access token
            access_token_expires = host.timedelta(
                minutes=host.settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
            )
            access_token = host.create_access_token(
                data={"sub": str(user.id), "username": user.username},
                expires_delta=access_token_expires,
            )
            host.set_web_session_cookie(request, response, access_token)
            await host.record_audit_event(
                category="auth",
                action="google_oauth",
                status="success",
                user=user,
                request=request,
                details={"flow": "login"},
            )

            return {"access_token": access_token, "token_type": "bearer"}

        else:
            # User doesn't exist, need to register
            if not oauth_data.username or not oauth_data.password:
                raise host.HTTPException(
                    status_code=host.status.HTTP_400_BAD_REQUEST,
                    detail="Username and password required for new Google account registration",
                )

            await host.ensure_registration_enabled(db)

            # Check if username already exists
            existing_user = await host.User.get_by_username(db, oauth_data.username)
            if existing_user:
                raise host.HTTPException(
                    status_code=host.status.HTTP_400_BAD_REQUEST,
                    detail="Username already taken. Please choose a different username.",
                )

            # Check if email already exists (from non-Google registration)
            existing_email = await host.User.get_by_email(db, email)
            if existing_email:
                raise host.HTTPException(
                    status_code=host.status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "An account with this email already exists. "
                        "Sign in with your password, then bind Google in your profile."
                    ),
                )

            await db.commit()
            # Create new user with Google OAuth
            hashed_password = await host.get_password_hash_async(oauth_data.password)
            new_user = host.User(
                username=oauth_data.username,
                email=email,
                hashed_password=hashed_password,
                google_id=google_user_id,
                oauth_provider="google",
                is_active=True,
            )
            db.add(new_user)
            await db.commit()
            await db.refresh(new_user)

            # Create access token for the new user
            access_token_expires = host.timedelta(
                minutes=host.settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
            )
            access_token = host.create_access_token(
                data={"sub": str(new_user.id), "username": new_user.username},
                expires_delta=access_token_expires,
            )
            host.set_web_session_cookie(request, response, access_token)
            await host.record_audit_event(
                category="auth",
                action="google_oauth",
                status="success",
                user=new_user,
                request=request,
                details={"flow": "register"},
            )

            return {"access_token": access_token, "token_type": "bearer"}

    except host.HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in Google OAuth login: {e}", exc_info=True)
        await host.record_audit_event(
            category="auth",
            action="google_oauth",
            status="failure",
            request=request,
            details={"reason": "oauth_error"},
        )
        raise host.HTTPException(
            status_code=host.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Google OAuth login failed: {str(e)}",
        ) from e
