"""Compatibility facade for domain-split schemas."""

# ruff: noqa: F401,F403

from .auth import (
    ApiKeyGenerate,
    ApiKeyResponse,
    CleanupDeleteRequest,
    CleanupDeleteResponse,
    CleanupFailedItem,
    CleanupItem,
    CleanupScanResponse,
    CleanupWorkshopSummary,
    GenerateServerTokenRequest,
    GenerateServerTokenResponse,
    GitHubTokenStatusResponse,
    PasswordReset,
    S3BackupItem,
    S3RestoreRequest,
    S3SettingsResponse,
    S3SettingsUpdate,
    SteamApiKeyResponse,
    Token,
    TokenData,
    UserCreate,
    UserLogin,
    UserProfileUpdate,
    UserResponse,
)
from .common import *
from .plugins import (
    ArchiveAnalysisResponse,
    ArchiveContentItem,
    DependencyInfo,
    GitHubPluginInstallRequest,
    GitHubPluginInstallResponse,
    GitHubRelease,
    GitHubReleaseAsset,
    GitHubReleasesResponse,
    GitHubRepoInfo,
    InstalledPluginAnalysisResponse,
    InstalledPluginFile,
    ManagedPluginCreate,
    ManagedPluginResponse,
    ManagedPluginUpdate,
    MarketPluginCreate,
    MarketPluginInstallRequest,
    MarketPluginListResponse,
    MarketPluginResponse,
    MarketPluginUpdate,
    MetamodStatusResponse,
    PluginAutoUpdateResponse,
    PluginAutoUpdateSettings,
    PluginUninstallRequest,
    PluginUninstallResponse,
)
from .scheduled_tasks import (
    ScheduledTaskCreate,
    ScheduledTaskResponse,
    ScheduledTaskUpdate,
)
from .servers import (
    A2SCachedData,
    A2SCacheResponse,
    A2SPlayerInfo,
    A2SServerInfo,
    ActionResponse,
    BatchActionRequest,
    BatchActionResponse,
    BatchInstallPluginsRequest,
    BatchSendCommandRequest,
    CustomCommandCreate,
    CustomCommandExecuteRequest,
    CustomCommandResponse,
    CustomCommandUpdate,
    DeploymentLogResponse,
    InitializedServerCreate,
    InitializedServerListItem,
    InitializedServerResponse,
    ServerAction,
    ServerCreate,
    ServerResponse,
    ServerResponseWithUser,
    ServerUpdate,
)
from .system import (
    DiscordSettingsResponse,
    DiscordSettingsUpdate,
    DiscordTestRequest,
    EmailTestRequest,
    ForgotPasswordRequest,
    GmailCredentialsUploadRequest,
    GoogleOAuthRequest,
    ResetPasswordRequest,
    SystemSettingsResponse,
    SystemSettingsUpdate,
)

# Keep the public class identity stable even though definitions are grouped by
# domain internally.
for _value in tuple(globals().values()):
    if isinstance(_value, type) and _value.__module__.startswith(f"{__name__}."):
        _value.__module__ = __name__

del _value
