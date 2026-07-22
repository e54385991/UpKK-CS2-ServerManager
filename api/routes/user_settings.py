"""
User settings routes for customizable mirror URLs
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules import (
    User,
    UserSettings,
    UserSettingsResponse,
    UserSettingsUpdate,
    get_current_active_user,
    get_db,
    settings,
)

router = APIRouter(prefix="/api/user-settings", tags=["user-settings"])


@router.get("/mirrors", response_model=dict)
async def get_mirror_presets():
    """Get available mirror presets from config"""
    return {
        "steamcmd_mirrors": settings.STEAMCMD_MIRRORS,
        "github_api_mirrors": settings.GITHUB_API_MIRRORS,
    }


@router.get("", response_model=UserSettingsResponse)
async def get_user_settings(
    current_user: User = Depends(get_current_active_user), db: AsyncSession = Depends(get_db)
):
    """Get current user's settings"""
    # Query user settings
    result = await db.execute(select(UserSettings).filter(UserSettings.user_id == current_user.id))
    user_settings = result.scalar_one_or_none()

    if not user_settings:
        # Return default settings if not set
        return UserSettingsResponse(steamcmd_mirror_url=None, github_api_mirror_url=None)

    return user_settings


@router.put("", response_model=UserSettingsResponse)
async def update_user_settings(
    settings_data: UserSettingsUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user settings"""
    # Query existing settings
    result = await db.execute(select(UserSettings).filter(UserSettings.user_id == current_user.id))
    user_settings = result.scalar_one_or_none()

    if not user_settings:
        # Create new settings if not exists
        user_settings = UserSettings(
            user_id=current_user.id,
            steamcmd_mirror_url=settings_data.steamcmd_mirror_url,
            github_api_mirror_url=settings_data.github_api_mirror_url,
        )
        db.add(user_settings)
    else:
        # Update existing settings
        if settings_data.steamcmd_mirror_url is not None:
            user_settings.steamcmd_mirror_url = settings_data.steamcmd_mirror_url
        if settings_data.github_api_mirror_url is not None:
            user_settings.github_api_mirror_url = settings_data.github_api_mirror_url

    await db.commit()
    await db.refresh(user_settings)

    return user_settings


@router.delete("")
async def reset_user_settings(
    current_user: User = Depends(get_current_active_user), db: AsyncSession = Depends(get_db)
):
    """Reset user settings to default (delete custom settings)"""
    # Query existing settings
    result = await db.execute(select(UserSettings).filter(UserSettings.user_id == current_user.id))
    user_settings = result.scalar_one_or_none()

    if user_settings:
        await db.delete(user_settings)
        await db.commit()

    return {"success": True, "message": "Settings reset to default"}
