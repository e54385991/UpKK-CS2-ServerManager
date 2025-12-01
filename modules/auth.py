"""
Authentication utilities for user management
"""
from datetime import timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from .config import settings
from .models import User
from .schemas import TokenData
from .database import get_db
from .utils import get_current_time

# Password hashing - configure bcrypt to handle long passwords automatically
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__ident="2b",  # Use bcrypt 2b format
    bcrypt__rounds=12  # Security rounds
)

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Optional OAuth2 scheme (doesn't raise error if no token)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
optional_oauth2_scheme = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash"""
    # Ensure password is not longer than 72 characters (bcrypt limit)
    if len(plain_password) > 72:
        plain_password = plain_password[:72]
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password"""
    # Ensure password is not longer than 72 characters (bcrypt limit)
    if len(password) > 72:
        password = password[:72]
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = get_current_time() + expires_delta
    else:
        expire = get_current_time() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get the current authenticated user from JWT token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = int(user_id_str)
        token_data = TokenData(user_id=user_id)
    except (JWTError, ValueError):
        raise credentials_exception
    
    result = await db.execute(select(User).where(User.id == token_data.user_id))
    user = result.scalar_one_or_none()
    
    if user is None:
        raise credentials_exception
    
    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get the current active user"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


async def get_optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """Get the current user if authenticated, None otherwise"""
    if credentials is None:
        return None
    
    try:
        token = credentials.credentials
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            return None
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        return None
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if user is None or not user.is_active:
        return None
    
    return user


async def get_current_admin_user(current_user: User = Depends(get_current_active_user)) -> User:
    """Get the current admin user"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user


async def get_user_from_api_key(
    x_api_key: Optional[str] = Header(None, description="User API key for authentication"),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """
    Get user from API key in header.
    
    Args:
        x_api_key: API key from X-API-Key header
        db: Database session
    
    Returns:
        User instance if API key is valid, None otherwise
    """
    if not x_api_key:
        return None
    
    user = await User.get_by_api_key(db, x_api_key)
    
    if user and user.is_active:
        return user
    
    return None


async def get_current_user_flexible(
    token: Optional[str] = Depends(oauth2_scheme),
    x_api_key: Optional[str] = Header(None, description="User API key for authentication"),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Get the current authenticated user from either JWT token or API key.
    Tries JWT first, then falls back to API key.
    
    Args:
        token: JWT token from Authorization header
        x_api_key: API key from X-API-Key header
        db: Database session
    
    Returns:
        Authenticated user
    
    Raises:
        HTTPException: If neither authentication method succeeds
    """
    # Try JWT authentication first
    if token:
        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            user_id_str: str = payload.get("sub")
            if user_id_str:
                user_id = int(user_id_str)
                user = await db.get(User, user_id)
                if user and user.is_active:
                    return user
        except (JWTError, ValueError):
            pass  # Fall through to API key authentication
    
    # Try API key authentication
    if x_api_key:
        user = await User.get_by_api_key(db, x_api_key)
        if user and user.is_active:
            return user
    
    # Neither authentication method succeeded
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
