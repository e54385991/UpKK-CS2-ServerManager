"""
Global settings routes for system-wide configuration
"""

from typing import List

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from api.dependencies import AdminUser, DatabaseSession
from modules import (
    AutoRestartSettings,
    GlobalSettings,
    GlobalSettingsResponse,
    GlobalSettingsUpdate,
)
from services.server_monitor import server_monitor

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/auto-restart", response_model=AutoRestartSettings)
async def get_auto_restart_settings(db: DatabaseSession):
    """
    Get auto-restart global configuration

    Returns the system-wide settings for auto-restart behavior including:
    - max_restarts: Maximum number of restarts within time window
    - time_window_minutes: Time window for counting restarts
    - default_interval: Default monitoring interval for new servers
    """
    # Get settings from database
    result = await db.execute(
        select(GlobalSettings).filter(
            GlobalSettings.setting_key.in_(
                [
                    "auto_restart_max_restarts",
                    "auto_restart_time_window_minutes",
                    "auto_restart_default_interval",
                ]
            )
        )
    )
    settings_list = result.scalars().all()

    # Convert to dict
    settings_dict = {s.setting_key: int(s.setting_value) for s in settings_list}

    return AutoRestartSettings(
        max_restarts=settings_dict.get("auto_restart_max_restarts", 5),
        time_window_minutes=settings_dict.get("auto_restart_time_window_minutes", 10),
        default_interval=settings_dict.get("auto_restart_default_interval", 60),
    )


@router.put("/auto-restart", response_model=AutoRestartSettings)
async def update_auto_restart_settings(
    settings: AutoRestartSettings,
    db: DatabaseSession,
    current_user: AdminUser,
):
    """
    Update auto-restart global configuration (Admin only)

    Updates the system-wide settings for auto-restart behavior.
    Changes will apply to:
    - New restart attempts (for max_restarts and time_window_minutes)
    - New servers (for default_interval)

    Existing monitoring tasks will continue with their current settings
    until restarted.
    """
    # Update max_restarts
    result = await db.execute(
        select(GlobalSettings).filter(GlobalSettings.setting_key == "auto_restart_max_restarts")
    )
    max_restarts_setting = result.scalar_one_or_none()
    if max_restarts_setting:
        max_restarts_setting.setting_value = str(settings.max_restarts)
    else:
        max_restarts_setting = GlobalSettings(
            setting_key="auto_restart_max_restarts",
            setting_value=str(settings.max_restarts),
            description="Maximum number of restarts within the time window",
        )
        db.add(max_restarts_setting)

    # Update time_window_minutes
    result = await db.execute(
        select(GlobalSettings).filter(
            GlobalSettings.setting_key == "auto_restart_time_window_minutes"
        )
    )
    time_window_setting = result.scalar_one_or_none()
    if time_window_setting:
        time_window_setting.setting_value = str(settings.time_window_minutes)
    else:
        time_window_setting = GlobalSettings(
            setting_key="auto_restart_time_window_minutes",
            setting_value=str(settings.time_window_minutes),
            description="Time window in minutes for counting restarts",
        )
        db.add(time_window_setting)

    # Update default_interval
    result = await db.execute(
        select(GlobalSettings).filter(GlobalSettings.setting_key == "auto_restart_default_interval")
    )
    default_interval_setting = result.scalar_one_or_none()
    if default_interval_setting:
        default_interval_setting.setting_value = str(settings.default_interval)
    else:
        default_interval_setting = GlobalSettings(
            setting_key="auto_restart_default_interval",
            setting_value=str(settings.default_interval),
            description="Default monitoring interval in seconds for new servers",
        )
        db.add(default_interval_setting)

    await db.commit()

    # Update server_monitor instance with new settings
    from datetime import timedelta

    server_monitor.max_restarts = settings.max_restarts
    server_monitor.time_window = timedelta(minutes=settings.time_window_minutes)

    return settings


@router.get("/all", response_model=List[GlobalSettingsResponse])
async def get_all_settings(db: DatabaseSession, current_user: AdminUser):
    """
    Get all global settings (Admin only)

    Returns all system-wide configuration settings.
    """
    result = await db.execute(select(GlobalSettings))
    settings = result.scalars().all()
    return settings


@router.get("/{setting_key}", response_model=GlobalSettingsResponse)
async def get_setting(
    setting_key: str,
    db: DatabaseSession,
    current_user: AdminUser,
):
    """
    Get a specific global setting by key (Admin only)
    """
    result = await db.execute(
        select(GlobalSettings).filter(GlobalSettings.setting_key == setting_key)
    )
    setting = result.scalar_one_or_none()

    if not setting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Setting '{setting_key}' not found"
        )

    return setting


@router.put("/{setting_key}", response_model=GlobalSettingsResponse)
async def update_setting(
    setting_key: str,
    setting_update: GlobalSettingsUpdate,
    db: DatabaseSession,
    current_user: AdminUser,
):
    """
    Update a specific global setting (Admin only)
    """
    result = await db.execute(
        select(GlobalSettings).filter(GlobalSettings.setting_key == setting_key)
    )
    setting = result.scalar_one_or_none()

    if not setting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Setting '{setting_key}' not found"
        )

    setting.setting_value = setting_update.setting_value
    await db.commit()
    await db.refresh(setting)

    return setting
