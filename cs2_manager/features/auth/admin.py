"""Explicit, idempotent administrator provisioning."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

if TYPE_CHECKING:
    from modules.models import User


class AdminCreationStatus(StrEnum):
    CREATED = "created"
    ALREADY_EXISTS = "already_exists"


class AdminConflictError(ValueError):
    """The requested identity overlaps an account that must not be modified."""


def _validated_identity(username: str, email: str, password: str) -> tuple[str, str]:
    username = username.strip()
    email = str(TypeAdapter(EmailStr).validate_python(email.strip())).lower()
    if not 3 <= len(username) <= 100:
        raise ValueError("Username must contain between 3 and 100 characters")
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password must contain at most 72 UTF-8 bytes")
    return username, email


async def _matching_users(
    session: AsyncSession,
    *,
    username: str,
    email: str,
) -> list[User]:
    from modules.models import User

    result = await session.execute(
        select(User).where(or_(User.username == username, User.email == email))
    )
    return result.scalars().all()


def _is_same_admin(users: Sequence[object], *, username: str, email: str) -> bool:
    return len(users) == 1 and all(
        getattr(user, "username", None) == username
        and str(getattr(user, "email", "")).lower() == email
        and getattr(user, "is_admin", False)
        for user in users
    )


async def create_admin(
    *,
    username: str,
    email: str,
    password: str,
    session_factory: Callable[[], AsyncSession] | None = None,
) -> AdminCreationStatus:
    """Create one admin without overwriting or promoting an existing account.

    Repeating the exact command for an existing administrator is a successful
    no-op.  Username/email collisions with any other identity fail closed.
    """
    username, email = _validated_identity(username, email, password)
    if session_factory is None:
        from modules.database import async_session_maker

        session_factory = async_session_maker

    from modules.auth import get_password_hash_async
    from modules.models import User

    async with session_factory() as session:
        users = await _matching_users(session, username=username, email=email)
        if _is_same_admin(users, username=username, email=email):
            return AdminCreationStatus.ALREADY_EXISTS
        if users:
            raise AdminConflictError(
                "Username or email belongs to an existing account; no changes were made"
            )

        admin = User(
            username=username,
            email=email,
            hashed_password=await get_password_hash_async(password),
            is_admin=True,
            is_active=True,
        )
        session.add(admin)
        try:
            await session.commit()
        except IntegrityError:
            # A concurrent provisioning command may have won the unique-key
            # race.  Treat only the exact same administrator as idempotent.
            await session.rollback()
            users = await _matching_users(session, username=username, email=email)
            if _is_same_admin(users, username=username, email=email):
                return AdminCreationStatus.ALREADY_EXISTS
            raise AdminConflictError(
                "Username or email was created concurrently; no changes were made"
            ) from None

    return AdminCreationStatus.CREATED
