"""Scheduled Tasks schemas."""

# ruff: noqa: F403,F405

from .common import *


class ScheduledTaskCreate(SQLModel):
    """Schema for creating a scheduled task"""

    name: str = Field(..., min_length=1, max_length=255, description="Task name/description")
    action: str = Field(
        ...,
        description="Action to perform (restart, start, stop, update, validate, backup_plugins)",
    )
    enabled: bool = Field(default=True, description="Whether the task is active")
    schedule_type: str = Field(..., description="Schedule type: daily, weekly, interval, cron")
    schedule_value: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Time (HH:MM), day+time (MON:14:30), interval (3600), or cron expression",
    )

    @field_validator("action")
    @classmethod
    def validate_action(cls, v):
        """Validate action matches allowed pattern"""
        if v not in ALLOWED_SCHEDULED_TASK_ACTIONS:
            raise ValueError(
                f"Invalid action: {v}. Allowed actions: {', '.join(ALLOWED_SCHEDULED_TASK_ACTIONS)}"
            )
        return v

    @field_validator("schedule_type")
    @classmethod
    def validate_schedule_type(cls, v):
        """Validate schedule type"""
        allowed_types = ["daily", "weekly", "interval", "cron"]
        if v not in allowed_types:
            raise ValueError(f"Schedule type must be one of: {', '.join(allowed_types)}")
        return v

    @field_validator("schedule_value")
    @classmethod
    def validate_schedule_value(cls, v, info):
        """Validate schedule value format based on schedule type"""
        if not v or len(v.strip()) == 0:
            raise ValueError("Schedule value cannot be empty")

        # Prevent command injection
        if any(char in v for char in [";", "&", "|", "$", "`", "\n", "\r"]):
            raise ValueError("Schedule value contains invalid characters")

        v_stripped = v.strip()

        # Get schedule_type from context if available
        schedule_type = info.data.get("schedule_type") if hasattr(info, "data") else None

        if schedule_type == "daily":
            # Validate HH:MM format
            if not re.match(r"^\d{1,2}:\d{2}$", v_stripped):
                raise ValueError("Daily schedule must be in HH:MM format (e.g., 14:30)")
            parts = v_stripped.split(":")
            hour, minute = int(parts[0]), int(parts[1])
            if hour < 0 or hour > 23:
                raise ValueError("Hour must be between 0 and 23")
            if minute < 0 or minute > 59:
                raise ValueError("Minute must be between 0 and 59")

        elif schedule_type == "weekly":
            # Validate DAY:HH:MM format
            if not re.match(r"^[A-Z]{3}:\d{1,2}:\d{2}$", v_stripped.upper()):
                raise ValueError("Weekly schedule must be in DAY:HH:MM format (e.g., MON:14:30)")
            parts = v_stripped.upper().split(":")
            day, hour, minute = parts[0], int(parts[1]), int(parts[2])
            valid_days = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
            if day not in valid_days:
                raise ValueError(f"Day must be one of: {', '.join(valid_days)}")
            if hour < 0 or hour > 23:
                raise ValueError("Hour must be between 0 and 23")
            if minute < 0 or minute > 59:
                raise ValueError("Minute must be between 0 and 59")

        elif schedule_type == "interval":
            # Validate positive integer
            try:
                interval = int(v_stripped)
                if interval <= 0:
                    raise ValueError("Interval must be a positive number")
                if interval < 60:
                    raise ValueError("Interval must be at least 60 seconds")
            except ValueError as e:
                if "positive" in str(e) or "at least" in str(e):
                    raise
                raise ValueError("Interval must be a valid integer (seconds)") from e

        return v_stripped


class ScheduledTaskUpdate(SQLModel):
    """Schema for updating a scheduled task"""

    name: Optional[str] = Field(
        None, min_length=1, max_length=255, description="Task name/description"
    )
    action: Optional[str] = Field(None, description="Action to perform")
    enabled: Optional[bool] = Field(None, description="Whether the task is active")
    schedule_type: Optional[str] = Field(
        None, description="Schedule type: daily, weekly, interval, cron"
    )
    schedule_value: Optional[str] = Field(
        None, min_length=1, max_length=255, description="Time or cron expression"
    )

    @field_validator("action")
    @classmethod
    def validate_action(cls, v):
        """Validate action matches allowed pattern"""
        if v is not None and v not in ALLOWED_SCHEDULED_TASK_ACTIONS:
            raise ValueError(
                f"Invalid action: {v}. Allowed actions: {', '.join(ALLOWED_SCHEDULED_TASK_ACTIONS)}"
            )
        return v

    @field_validator("schedule_type")
    @classmethod
    def validate_schedule_type(cls, v):
        """Validate schedule type"""
        if v is not None:
            allowed_types = ["daily", "weekly", "interval", "cron"]
            if v not in allowed_types:
                raise ValueError(f"Schedule type must be one of: {', '.join(allowed_types)}")
        return v

    @field_validator("schedule_value")
    @classmethod
    def validate_schedule_value(cls, v):
        """Validate schedule value format"""
        if v is not None:
            if len(v.strip()) == 0:
                raise ValueError("Schedule value cannot be empty")
            # Prevent command injection
            if any(char in v for char in [";", "&", "|", "$", "`", "\n", "\r"]):
                raise ValueError("Schedule value contains invalid characters")
        return v.strip() if v else v


class ScheduledTaskResponse(SQLModel):
    """Schema for scheduled task response"""

    id: int
    server_id: int
    name: str
    action: str
    enabled: bool
    schedule_type: str
    schedule_value: str
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    run_count: int
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduledTaskDeleteResponse(SQLModel):
    """Successful scheduled-task deletion response."""

    success: Literal[True]
    message: str
