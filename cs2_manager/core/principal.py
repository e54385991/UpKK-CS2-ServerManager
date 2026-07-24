"""Detached authentication identity used outside database transactions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Principal(BaseModel):
    """Immutable, non-secret identity safe to pass to application services."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    username: str
    email: str
    is_admin: bool = False
    is_active: bool = True

    @property
    def user_id(self) -> int:
        """Explicit alias for call sites where a generic ``id`` is ambiguous."""
        return self.id

    @classmethod
    def from_user(cls, user: Any) -> "Principal":
        """Copy the public identity fields from a legacy ORM user."""
        return cls.model_validate(user)
