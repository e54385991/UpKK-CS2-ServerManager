"""
Authentication utilities for user management
"""

from datetime import timedelta
from typing import Optional
from urllib.parse import urlsplit

import bcrypt
import jwt
from anyio import to_thread
from fastapi import Depends, Header, HTTPException, Request, Response, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from jwt import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from cs2_manager.core import Principal

from .config import settings
from .database import async_session_maker, get_db
from .models import Server, User
from .schemas import TokenData
from .utils import get_current_time

BCRYPT_ROUNDS = 12
BCRYPT_MAX_PASSWORD_BYTES = 72
WEB_SESSION_COOKIE = "upkk_access_token"

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Optional OAuth2 scheme (doesn't raise error if no token)
optional_oauth2_scheme = HTTPBearer(auto_error=False)


def _bcrypt_password_bytes(password: str) -> bytes:
    """Bcrypt only accepts the first 72 password bytes."""
    return password.encode("utf-8")[:BCRYPT_MAX_PASSWORD_BYTES]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash"""
    try:
        return bcrypt.checkpw(
            _bcrypt_password_bytes(plain_password), hashed_password.encode("utf-8")
        )
    except TypeError, ValueError:
        return False


def get_password_hash(password: str) -> str:
    """Hash a password"""
    password_hash = bcrypt.hashpw(
        _bcrypt_password_bytes(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS, prefix=b"2b")
    )
    return password_hash.decode("utf-8")


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """Run the CPU-expensive bcrypt verification outside the event loop."""
    return await to_thread.run_sync(verify_password, plain_password, hashed_password)


async def get_password_hash_async(password: str) -> str:
    """Run the CPU-expensive bcrypt hash outside the event loop."""
    return await to_thread.run_sync(get_password_hash, password)


def set_web_session_cookie(request: Request, response: Response, token: str) -> None:
    """Set the HTTP-only cookie used only to protect HTML and WebSocket routes."""
    response.set_cookie(
        key=WEB_SESSION_COOKIE,
        value=token,
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )


def clear_web_session_cookie(response: Response) -> None:
    """Remove the browser session cookie without changing bearer-token behavior."""
    response.delete_cookie(WEB_SESSION_COOKIE, path="/", samesite="lax")


def _decode_user_id(token: str) -> int:
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    user_id = payload.get("sub")
    if user_id is None:
        raise InvalidTokenError("Token subject is missing")
    return int(user_id)


async def _get_active_user_for_token(token: str, db: AsyncSession) -> Optional[User]:
    try:
        user_id = _decode_user_id(token)
    except InvalidTokenError, ValueError, TypeError:
        return None

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


async def get_current_web_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate browser page navigation with the HTTP-only session cookie."""
    token = request.cookies.get(WEB_SESSION_COOKIE)
    user = await _get_active_user_for_token(token, db) if token else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Authentication required",
            headers={"Location": "/login"},
        )
    return user


async def get_current_web_admin(
    current_user: User = Depends(get_current_web_user),
) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required"
        )
    return current_user


async def authenticate_websocket(
    websocket: WebSocket,
    server_id: Optional[int] = None,
) -> tuple[Optional[User], Optional[Server]]:
    """Authenticate a WebSocket before accepting it and optionally check server ownership."""
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if origin and host and urlsplit(origin).netloc.lower() != host.lower():
        await websocket.close(code=4403, reason="Invalid WebSocket origin")
        return None, None

    token = websocket.cookies.get(WEB_SESSION_COOKIE)
    if not token:
        await websocket.close(code=4401, reason="Authentication required")
        return None, None

    async with async_session_maker() as db:
        user = await _get_active_user_for_token(token, db)
        if user is None:
            await websocket.close(code=4401, reason="Invalid or expired session")
            return None, None

        server = None
        if server_id is not None:
            server = await db.get(Server, server_id)
            if server is None or (not user.is_admin and server.user_id != user.id):
                await websocket.close(code=4404, reason="Server not found")
                return None, None

        # Detach data from the short-lived transaction before network I/O.
        await db.commit()
        return user, server


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
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
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
    except InvalidTokenError, ValueError:
        raise credentials_exception from None

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


async def get_current_principal(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> Principal:
    """Authenticate in a short session and return a detached identity.

    Unlike the legacy ORM-returning dependency, the database context has
    already exited when the route handler receives this value. This makes the
    dependency safe for endpoints which immediately perform slow remote I/O.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        user_id = _decode_user_id(token)
    except InvalidTokenError, ValueError, TypeError:
        raise credentials_exception from None

    container = getattr(request.app.state, "container", None)
    database = getattr(container, "database", None)
    session_factory = getattr(database, "session_factory", None)
    if not callable(session_factory):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication database is unavailable",
        )

    async with session_factory() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise credentials_exception
        if not user.is_active:
            raise HTTPException(status_code=400, detail="Inactive user")
        principal = Principal.from_user(user)

    return principal


async def get_optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_oauth2_scheme),
    db: AsyncSession = Depends(get_db),
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
    except InvalidTokenError, ValueError:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        return None

    return user


async def get_current_admin_user(current_user: User = Depends(get_current_active_user)) -> User:
    """Get the current admin user"""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
    return current_user


async def get_user_from_api_key(
    x_api_key: Optional[str] = Header(None, description="User API key for authentication"),
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
            payload = jwt.decode(
                token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
            )
            user_id_str: str = payload.get("sub")
            if user_id_str:
                user_id = int(user_id_str)
                user = await db.get(User, user_id)
                if user and user.is_active:
                    return user
        except InvalidTokenError, ValueError:
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
