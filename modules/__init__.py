"""
Core modules for CS2 Server Manager
"""
from .config import settings, Settings
from .models import Base, Server, DeploymentLog, MonitoringLog, ServerStatus, AuthType, User, InitializedServer, ScheduledTask
from .schemas import (
    ServerCreate, ServerUpdate, ServerResponse,
    ServerAction, ActionResponse, DeploymentLogResponse,
    BatchActionRequest, BatchActionResponse, BatchInstallPluginsRequest,
    UserCreate, UserLogin, UserResponse, Token, TokenData,
    PasswordReset, UserProfileUpdate,
    ApiKeyResponse, ApiKeyGenerate,
    SteamApiKeyResponse, GenerateServerTokenRequest, GenerateServerTokenResponse,
    A2SServerInfo, A2SPlayerInfo, A2SCachedData, A2SCacheResponse,
    InitializedServerCreate, InitializedServerResponse, InitializedServerListItem,
    ScheduledTaskCreate, ScheduledTaskUpdate, ScheduledTaskResponse
)
from .database import get_db, init_db, engine, async_session_maker, migrate_db
from .auth import (
    get_password_hash, verify_password, create_access_token,
    get_current_user, get_current_active_user, get_current_admin_user,
    get_optional_current_user, get_user_from_api_key, get_current_user_flexible
)
from .utils import generate_api_key, verify_api_key_format, get_current_time
from .logging_config import setup_logging

__all__ = [
    'settings',
    'Settings',
    'Base',
    'Server',
    'DeploymentLog',
    'MonitoringLog',
    'User',
    'InitializedServer',
    'ScheduledTask',
    'ServerStatus',
    'AuthType',
    'ServerCreate',
    'ServerUpdate',
    'ServerResponse',
    'ServerAction',
    'ActionResponse',
    'DeploymentLogResponse',
    'BatchActionRequest',
    'BatchActionResponse',
    'BatchInstallPluginsRequest',
    'UserCreate',
    'UserLogin',
    'UserResponse',
    'Token',
    'TokenData',
    'PasswordReset',
    'UserProfileUpdate',
    'ApiKeyResponse',
    'ApiKeyGenerate',
    'SteamApiKeyResponse',
    'GenerateServerTokenRequest',
    'GenerateServerTokenResponse',
    'A2SServerInfo',
    'A2SPlayerInfo',
    'A2SCachedData',
    'A2SCacheResponse',
    'InitializedServerCreate',
    'InitializedServerResponse',
    'InitializedServerListItem',
    'ScheduledTaskCreate',
    'ScheduledTaskUpdate',
    'ScheduledTaskResponse',
    'get_db',
    'init_db',
    'migrate_db',
    'engine',
    'async_session_maker',
    'get_password_hash',
    'verify_password',
    'create_access_token',
    'get_current_user',
    'get_current_active_user',
    'get_current_admin_user',
    'get_optional_current_user',
    'get_user_from_api_key',
    'get_current_user_flexible',
    'generate_api_key',
    'verify_api_key_format',
    'get_current_time',
    'setup_logging',
]
