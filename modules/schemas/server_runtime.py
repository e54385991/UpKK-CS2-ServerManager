"""Server runtime and A2S response contracts."""

# ruff: noqa: F403,F405

from .common import *


class A2SServerInfo(SQLModel):
    server_name: Optional[str] = None
    map_name: Optional[str] = None
    folder: Optional[str] = None
    game: Optional[str] = None
    player_count: Optional[int] = None
    max_players: Optional[int] = None
    bot_count: Optional[int] = None
    server_type: Optional[str] = None
    platform: Optional[str] = None
    password_protected: Optional[bool] = None
    vac_enabled: Optional[bool] = None
    version: Optional[str] = None
    ping: Optional[float] = None
    keywords: Optional[str] = None
    game_id: Optional[int] = None


class A2SPlayerInfo(SQLModel):
    name: str
    score: int
    duration: float


class A2SCachedData(SQLModel):
    query_host: str
    query_port: int
    success: bool
    server_info: Optional[A2SServerInfo] = None
    players: List[A2SPlayerInfo] = []
    response_time_ms: int
    timestamp: str
    last_updated: str
    error: Optional[str] = None


class A2SCacheResponse(SQLModel):
    servers: Dict[str, A2SCachedData]
    timestamp: str


class InitializedServerCreate(SQLModel):
    name: str = Field(..., min_length=1, max_length=255)
    host: str = Field(..., min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(..., min_length=1, max_length=100)
    ssh_password: str = Field(..., min_length=1, max_length=255)
    game_directory: str = Field(default="/home/cs2server/cs2")


class InitializedServerListItem(SQLModel):
    id: int
    user_id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    game_directory: str
    created_at: datetime
    updated_at: datetime


class InitializedServerResponse(SQLModel):
    id: int
    user_id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    ssh_password: str
    game_directory: str
    created_at: datetime
    updated_at: datetime
