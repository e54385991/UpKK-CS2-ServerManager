"""Auth schemas."""

# ruff: noqa: F403,F405

from .common import *


class UserCreate(SQLModel):
    """Schema for user registration"""

    username: str = Field(..., min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=100)
    captcha_token: Optional[str] = Field(
        None, description="CAPTCHA token from /api/captcha/generate (optional when disabled)"
    )
    captcha_code: Optional[str] = Field(
        None, min_length=4, max_length=4, description="User-entered CAPTCHA code"
    )


class UserLogin(SQLModel):
    """Schema for user login"""

    username: str
    password: str
    captcha_token: Optional[str] = Field(
        None, description="CAPTCHA token from /api/captcha/generate (optional when disabled)"
    )
    captcha_code: Optional[str] = Field(
        None, min_length=4, max_length=4, description="User-entered CAPTCHA code"
    )


class UserResponse(SQLModel):
    """Schema for user response"""

    id: int
    username: str
    email: str
    is_active: bool
    is_admin: bool
    created_at: datetime


class Token(SQLModel):
    """Schema for JWT token response"""

    access_token: str
    token_type: str


class TokenData(SQLModel):
    """Schema for token data"""

    user_id: Optional[int] = None
    username: Optional[str] = None


class PasswordReset(SQLModel):
    """Schema for password reset"""

    current_password: str = Field(..., min_length=6, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100)
    confirm_password: str = Field(..., min_length=6, max_length=100)
    captcha_token: Optional[str] = Field(
        None, description="CAPTCHA token from /api/captcha/generate (optional when disabled)"
    )
    captcha_code: Optional[str] = Field(
        None, min_length=4, max_length=4, description="User-entered CAPTCHA code"
    )


class UserProfileUpdate(SQLModel):
    """Schema for updating user profile"""

    email: Optional[EmailStr] = None
    steam_api_key: Optional[str] = Field(
        None, max_length=64, description="Steam Web API key for game server management"
    )
    github_token: Optional[str] = Field(
        None,
        max_length=255,
        description="GitHub Fine-grained personal access token for accessing private repositories and better rate limits",
    )
    captcha_token: Optional[str] = Field(
        None, description="CAPTCHA token from /api/captcha/generate (optional when disabled)"
    )
    captcha_code: Optional[str] = Field(
        None, min_length=4, max_length=4, description="User-entered CAPTCHA code"
    )

    @field_validator("steam_api_key")
    @classmethod
    def validate_steam_api_key(cls, v):
        """Validate Steam API key format"""
        if v is None or v.strip() == "":
            return v
        # Steam API keys are 32-character hexadecimal strings
        v = v.strip()
        if not re.match(r"^[A-Fa-f0-9]{32}$", v):
            raise ValueError("Steam API key must be a 32-character hexadecimal string")
        return v

    @field_validator("github_token")
    @classmethod
    def validate_github_token(cls, v):
        """Validate GitHub token format"""
        if v is None or v.strip() == "":
            return v
        # GitHub Fine-grained tokens start with 'github_pat_' followed by alphanumeric characters
        # Example: [REDACTED]
        # Classic tokens start with 'ghp_', 'gho_', 'ghu_', 'ghs_', or 'ghr_' followed by alphanumeric characters
        v = v.strip()
        # More flexible pattern to match real GitHub tokens
        # Fine-grained: github_pat_ + base62-like characters (letters, numbers, underscore)
        # Classic: gh[poushр]_ + base62-like characters
        if not re.match(r"^(github_pat_[A-Za-z0-9_]+|gh[poushр]_[A-Za-z0-9_]+)$", v):
            raise ValueError(
                "GitHub token must be a valid Fine-grained or Classic personal access token"
            )
        return v


class SteamApiKeyResponse(SQLModel):
    """Schema for Steam API key response"""

    steam_api_key: Optional[str] = None


class GitHubTokenStatusResponse(SQLModel):
    """Schema for GitHub token status response"""

    has_token: bool
    token_prefix: Optional[str] = (
        None  # Shows first part like "github_pat_11..." without revealing full token
    )


class S3SettingsResponse(SQLModel):
    """Schema for S3 backup settings without exposing the secret key"""

    enabled: bool
    endpoint_url: Optional[str] = None
    region: Optional[str] = None
    bucket: Optional[str] = None
    access_key_id: Optional[str] = None
    prefix: Optional[str] = None
    use_ssl: bool = True
    retention_count: int = 10
    has_secret: bool = False
    is_configured: bool = False


class S3SettingsUpdate(SQLModel):
    """Schema for updating S3 backup settings"""

    enabled: Optional[bool] = None
    endpoint_url: Optional[str] = Field(None, max_length=500)
    region: Optional[str] = Field(None, max_length=100)
    bucket: Optional[str] = Field(None, max_length=255)
    access_key_id: Optional[str] = Field(None, max_length=255)
    secret_access_key: Optional[str] = Field(None, max_length=255)
    prefix: Optional[str] = Field(None, max_length=255)
    use_ssl: Optional[bool] = None
    retention_count: Optional[int] = Field(None, ge=1, le=10000)
    clear_secret: bool = False
    captcha_token: Optional[str] = Field(
        None, description="CAPTCHA token from /api/captcha/generate (optional when disabled)"
    )
    captcha_code: Optional[str] = Field(
        None, min_length=4, max_length=4, description="User-entered CAPTCHA code"
    )

    @field_validator(
        "endpoint_url", "region", "bucket", "access_key_id", "secret_access_key", "prefix"
    )
    @classmethod
    def strip_optional_strings(cls, v):
        if v is None:
            return v
        return v.strip()

    @field_validator("bucket")
    @classmethod
    def validate_bucket(cls, v):
        if v is None or v == "":
            return v
        if "/" in v or "\\" in v:
            raise ValueError("S3 bucket name cannot contain slashes")
        return v

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, v):
        if v is None or v == "":
            return v
        if any(char in v for char in ["\\", "\n", "\r"]):
            raise ValueError("S3 prefix contains invalid characters")
        return v.strip("/")


class S3BackupItem(SQLModel):
    """Schema for a listed S3 backup object"""

    key: str
    filename: str
    size: int
    last_modified: Optional[datetime] = None
    etag: Optional[str] = None


class S3RestoreRequest(SQLModel):
    """Schema for restoring a selected S3 backup"""

    object_key: str = Field(..., min_length=1, max_length=1024)

    @field_validator("object_key")
    @classmethod
    def validate_object_key(cls, v):
        key = v.strip()
        if not key or key.startswith("/") or "\\" in key:
            raise ValueError("Invalid S3 object key")
        if any(char in key for char in ["\n", "\r", "\x00"]):
            raise ValueError("S3 object key contains invalid characters")
        return key


class CleanupItem(SQLModel):
    """Schema for a game directory cleanup candidate"""

    path: str
    name: str
    type: str
    size: int = 0
    modified: Optional[float] = None
    category: str
    reason: str
    danger_level: str


class CleanupWorkshopSummary(SQLModel):
    """Schema for Steam Workshop cleanup summary"""

    path: str
    item_count: int = 0
    size: int = 0
    items: List[CleanupItem] = Field(default_factory=list)


class CleanupScanResponse(SQLModel):
    """Schema for game directory cleanup scan response"""

    safe_items: List[CleanupItem] = Field(default_factory=list)
    archive_items: List[CleanupItem] = Field(default_factory=list)
    workshop_summary: CleanupWorkshopSummary
    total_size: int = 0


class CleanupDeleteRequest(SQLModel):
    """Schema for deleting cleanup candidates"""

    mode: str = Field(..., description="Cleanup mode: safe, archives, or workshop")
    paths: List[str] = Field(default_factory=list)
    confirmation_text: Optional[str] = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v):
        mode = v.strip()
        allowed_modes = ["safe", "archives", "workshop"]
        if mode not in allowed_modes:
            raise ValueError(f"Cleanup mode must be one of: {', '.join(allowed_modes)}")
        return mode

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, v):
        clean_paths = []
        for path in v:
            path = path.strip()
            if not path or "\x00" in path or "\n" in path or "\r" in path:
                raise ValueError("Cleanup paths contain invalid characters")
            clean_paths.append(path)
        return clean_paths


class CleanupFailedItem(SQLModel):
    """Schema for a cleanup deletion failure"""

    path: str
    error: str


class CleanupDeleteResponse(SQLModel):
    """Schema for cleanup delete response"""

    success: bool
    message: str
    deleted_count: int = 0
    freed_bytes_estimate: int = 0
    failed_items: List[CleanupFailedItem] = Field(default_factory=list)


class GenerateServerTokenRequest(SQLModel):
    """Schema for generating game server login token"""

    server_name: Optional[str] = Field(
        None, max_length=255, description="Optional memo/description for the server"
    )
    captcha_token: Optional[str] = Field(
        None, description="CAPTCHA token (optional when CAPTCHA is disabled)"
    )
    captcha_code: Optional[str] = Field(
        None, min_length=4, max_length=4, description="CAPTCHA code (optional when disabled)"
    )


class GenerateServerTokenResponse(SQLModel):
    """Schema for game server login token response"""

    success: bool
    login_token: Optional[str] = None
    error: Optional[str] = None


class ApiKeyResponse(SQLModel):
    """Schema for API key response"""

    api_key: str
    created_at: datetime


class ApiKeyGenerate(SQLModel):
    """Schema for generating API key"""

    captcha_token: Optional[str] = Field(
        None, description="CAPTCHA token from /api/captcha/generate (optional)"
    )
    captcha_code: Optional[str] = Field(
        None, min_length=4, max_length=4, description="User-entered CAPTCHA code (optional)"
    )
