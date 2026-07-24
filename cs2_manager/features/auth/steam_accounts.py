"""Steam account token persistence and detached configuration snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import User


class SteamAccountUserNotFoundError(LookupError):
    """The authenticated user disappeared before the Steam use case ran."""


@dataclass(frozen=True, slots=True)
class SteamAccountConfiguration:
    """Least-privilege values safe to use after the database phase."""

    username: str
    steam_api_key: str | None

    @classmethod
    def from_user(cls, user: User) -> "SteamAccountConfiguration":
        return cls(
            username=user.username,
            steam_api_key=user.steam_api_key,
        )


class SteamAccountRepository:
    """Load Steam token settings without owning transaction commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_configuration(self, user_id: int) -> SteamAccountConfiguration:
        user = await self._session.get(User, user_id)
        if user is None:
            raise SteamAccountUserNotFoundError("User not found")
        return SteamAccountConfiguration.from_user(user)
