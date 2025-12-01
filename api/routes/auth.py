"""
Authentication routes for user registration and login
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from datetime import timedelta

from modules import (
    User, UserCreate, UserLogin, UserResponse, Token,
    PasswordReset, UserProfileUpdate, ApiKeyResponse, ApiKeyGenerate,
    SteamApiKeyResponse, GenerateServerTokenRequest, GenerateServerTokenResponse,
    get_db, get_password_hash, verify_password, create_access_token,
    get_current_active_user, settings, generate_api_key
)
from services.captcha_service import captcha_service
from services.steam_api_service import steam_api_service

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    """Register a new user"""
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(user_data.captcha_token, user_data.captcha_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired CAPTCHA code"
        )
    
    # Check if username already exists
    existing_user = await User.get_by_username(db, user_data.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Check if email already exists
    existing_email = await User.get_by_email(db, user_data.email)
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new user
    hashed_password = get_password_hash(user_data.password)
    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_password
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return user


@router.post("/login", response_model=Token)
async def login(
    user_data: UserLogin,
    db: AsyncSession = Depends(get_db)
):
    """Login and get access token"""
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(user_data.captcha_token, user_data.captcha_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired CAPTCHA code"
        )
    
    # Find user by username
    user = await User.get_by_username(db, user_data.username)
    
    if not user or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username},
        expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_active_user)):
    """Get current user information"""
    return current_user


@router.post("/reset-password")
async def reset_password(
    password_data: PasswordReset,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Reset user password"""
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(password_data.captcha_token, password_data.captcha_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired CAPTCHA code"
        )
    
    # Verify current password
    if not verify_password(password_data.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    # Verify new password and confirm password match
    if password_data.new_password != password_data.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password and confirm password do not match"
        )
    
    # Update password
    current_user.hashed_password = get_password_hash(password_data.new_password)
    await db.commit()
    
    return {"success": True, "message": "Password reset successfully"}


@router.put("/profile", response_model=UserResponse)
async def update_profile(
    profile_data: UserProfileUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Update user profile"""
    # Validate CAPTCHA first
    is_valid = await captcha_service.validate_captcha(profile_data.captcha_token, profile_data.captcha_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired CAPTCHA code"
        )
    
    # Update email if provided
    if profile_data.email:
        # Check if email already exists for another user
        result = await db.execute(
            select(User).where(User.email == profile_data.email, User.id != current_user.id)
        )
        existing_email = result.scalar_one_or_none()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered by another user"
            )
        current_user.email = profile_data.email
    
    # Update Steam API key if provided
    if profile_data.steam_api_key is not None:
        # Allow empty string to clear the Steam API key
        if profile_data.steam_api_key.strip() == "":
            current_user.steam_api_key = None
        else:
            current_user.steam_api_key = profile_data.steam_api_key.strip()
    
    await db.commit()
    await db.refresh(current_user)
    
    return current_user


@router.get("/api-key", response_model=ApiKeyResponse)
async def get_api_key(
    current_user: User = Depends(get_current_active_user),
):
    """Get current user's API key"""
    if not current_user.api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API key generated. Please generate one first."
        )
    
    # Note: Using updated_at as a proxy for API key creation time.
    # This timestamp reflects the last time the user record was updated,
    # which includes API key generation/regeneration.
    return {
        "api_key": current_user.api_key,
        "created_at": current_user.updated_at
    }


@router.post("/api-key/generate", response_model=ApiKeyResponse)
async def generate_user_api_key(
    api_key_data: ApiKeyGenerate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Generate a new API key for the current user (or regenerate if exists)"""
    # Validate CAPTCHA if provided (optional for automation)
    if api_key_data.captcha_token and api_key_data.captcha_code:
        is_valid = await captcha_service.validate_captcha(api_key_data.captcha_token, api_key_data.captcha_code)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired CAPTCHA code"
            )
    
    # Generate new API key
    new_api_key = generate_api_key()
    
    # Check if the generated key already exists (very unlikely but possible)
    max_retries = 5
    for _ in range(max_retries):
        existing_user = await User.get_by_api_key(db, new_api_key)
        if not existing_user:
            break
        new_api_key = generate_api_key()
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate unique API key. Please try again."
        )
    
    # Update user's API key
    current_user.api_key = new_api_key
    await db.commit()
    await db.refresh(current_user)
    
    return {
        "api_key": current_user.api_key,
        "created_at": current_user.updated_at
    }


@router.delete("/api-key")
async def revoke_api_key(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Revoke (delete) the current user's API key"""
    if not current_user.api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API key to revoke"
        )
    
    # Remove API key
    current_user.api_key = None
    await db.commit()
    
    return {"success": True, "message": "API key revoked successfully"}


@router.get("/steam-api-key", response_model=SteamApiKeyResponse)
async def get_steam_api_key(
    current_user: User = Depends(get_current_active_user),
):
    """Get current user's Steam API key"""
    return {"steam_api_key": current_user.steam_api_key}


@router.post("/generate-server-token", response_model=GenerateServerTokenResponse)
async def generate_server_token(
    request_data: GenerateServerTokenRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Generate a Steam game server login token (GSLT) using user's Steam API key"""
    # Validate CAPTCHA (required for security)
    is_valid = await captcha_service.validate_captcha(request_data.captcha_token, request_data.captcha_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired CAPTCHA code"
        )
    
    # Check if user has Steam API key set
    if not current_user.steam_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Steam API key not set. Please set your Steam API key in profile settings first."
        )
    
    # Use provided server name or fallback to username-based name
    if request_data.server_name and isinstance(request_data.server_name, str):
        memo = request_data.server_name.strip() or f"CS2 Server - {current_user.username}"
    else:
        memo = f"CS2 Server - {current_user.username}"
    
    success, result = await steam_api_service.create_game_server_account(
        steam_api_key=current_user.steam_api_key,
        memo=memo
    )
    
    if not success or not result.get('success'):
        error_msg = result.get('error', 'Unknown error') if result else 'Failed to generate token'
        return GenerateServerTokenResponse(
            success=False,
            error=error_msg
        )
    
    return GenerateServerTokenResponse(
        success=True,
        login_token=result.get('login_token')
    )
