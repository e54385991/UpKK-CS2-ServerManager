"""Prepare safe, isolated server copies from an existing server record."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models import Server
from modules.schemas.servers import ServerCreate


class CloneConflictError(ValueError):
    """The requested clone target conflicts with an existing server."""


class CloneSourceError(ValueError):
    """The source server cannot provide the required connection credential."""


@dataclass(frozen=True)
class ServerCloneInput:
    """Transport-independent editable values supplied by the clone form."""

    name: str
    game_port: int
    game_directory: str
    description: str | None
    server_name: str
    default_map: str
    max_players: int
    game_mode: str
    game_type: str
    session_manager: str | None
    apt_mirror: str | None
    sudo_password: str | None
    rcon_password: str | None
    steam_account_token: str | None
    additional_parameters: str | None
    captcha_token: str | None
    captcha_code: str | None
    additional_parameters_override: bool = False


@dataclass(frozen=True)
class ServerCloneTemplateData:
    """Non-secret data used to render the clone form."""

    source_server_id: int
    source_name: str
    host: str
    ssh_port: int
    ssh_user: str
    source_game_port: int
    source_game_directory: str
    has_sudo_password: bool
    apt_mirror: str | None
    use_panel_proxy: bool
    github_proxy: str | None
    name: str
    game_port: int
    game_directory: str
    server_name: str
    default_map: str
    max_players: int
    game_mode: str
    game_type: str
    session_manager: str
    additional_parameters: str | None


def normalize_game_directory(value: str) -> str:
    """Return a safe absolute game directory for comparisons and storage."""

    raw = str(value or "").strip()
    if not raw.startswith("/"):
        raise ValueError("Game directory must be an absolute path")
    raw = "/" + raw.lstrip("/")
    normalized = posixpath.normpath(raw)
    if normalized == "/":
        raise ValueError("Game directory cannot be the filesystem root")
    if len(normalized) > 500:
        raise ValueError("Game directory must be at most 500 characters")
    return normalized


async def _servers_for_user(db: AsyncSession, user_id: int) -> list[Server]:
    result = await db.execute(select(Server).where(Server.user_id == user_id))
    return list(result.scalars().all())


async def _servers_on_host(db: AsyncSession, host: str) -> list[Server]:
    result = await db.execute(select(Server).where(Server.host == host))
    return list(result.scalars().all())


def _server_name_candidate(base: str, suffix: int) -> str:
    suffix_text = f" ({suffix})"
    return f"{base[: 255 - len(suffix_text)]}{suffix_text}"


def _directory_candidate(base: str, suffix: int) -> str:
    suffix_text = f"-{suffix}"
    base_limit = 500 - len(suffix_text)
    clipped = base[:base_limit].rstrip("/")
    if not clipped:
        clipped = "/clone"
    return normalize_game_directory(f"{clipped}{suffix_text}")


def _occupied_ports(servers: list[Server], host: str) -> set[int]:
    occupied: set[int] = set()
    for server in servers:
        if server.host != host:
            continue
        if isinstance(server.game_port, int):
            occupied.add(server.game_port)
            client_port = getattr(server, "client_port", None) or server.game_port + 1
            if 1 <= client_port <= 65535:
                occupied.add(client_port)
        tv_port = getattr(server, "tv_port", None)
        if getattr(server, "tv_enable", False) and isinstance(tv_port, int):
            occupied.add(tv_port)
    return occupied


def _port_available(candidate: int, occupied: set[int]) -> bool:
    if not 1 <= candidate <= 65534:
        return False
    return candidate not in occupied and candidate + 1 not in occupied


def _next_available_port(start: int, occupied: set[int]) -> int:
    candidate = start
    while candidate <= 65534:
        if _port_available(candidate, occupied):
            return candidate
        candidate += 10

    candidate = 27015
    while candidate <= 65534:
        if _port_available(candidate, occupied):
            return candidate
        candidate += 1
    raise CloneConflictError("No available game port remains on this host")


async def build_clone_template(
    db: AsyncSession,
    source: Server,
    owner_id: int,
) -> ServerCloneTemplateData:
    """Build deterministic, currently available defaults without exposing secrets."""

    if not source.ssh_password:
        raise CloneSourceError("The source server has no saved SSH password")
    servers = await _servers_for_user(db, owner_id)
    names = {server.name for server in servers}
    directories = {
        normalize_game_directory(server.game_directory)
        for server in servers
        if server.host == source.host and server.game_directory
    }
    source_directory = normalize_game_directory(source.game_directory)

    suffix = 2
    name = _server_name_candidate(source.name, suffix)
    while name in names:
        suffix += 1
        name = _server_name_candidate(source.name, suffix)

    directory_suffix = 2
    game_directory = _directory_candidate(source_directory, directory_suffix)
    while game_directory in directories:
        directory_suffix += 1
        game_directory = _directory_candidate(source_directory, directory_suffix)

    occupied = _occupied_ports(await _servers_on_host(db, source.host), source.host)
    game_port = _next_available_port(source.game_port + 10, occupied)
    server_name = name
    raw_session_manager = getattr(source, "session_manager", "tmux")
    session_manager = "screen" if raw_session_manager == "screen" else "tmux"
    use_panel_proxy = bool(getattr(source, "use_panel_proxy", False))

    if source.id is None:
        raise CloneSourceError("The source server has no database id")

    return ServerCloneTemplateData(
        source_server_id=source.id,
        source_name=source.name,
        host=source.host,
        ssh_port=source.ssh_port,
        ssh_user=source.ssh_user,
        source_game_port=source.game_port,
        source_game_directory=source_directory,
        has_sudo_password=bool(getattr(source, "sudo_password", None)),
        apt_mirror=getattr(source, "apt_mirror", None) or None,
        use_panel_proxy=use_panel_proxy,
        github_proxy=None if use_panel_proxy else getattr(source, "github_proxy", None) or None,
        name=name,
        game_port=game_port,
        game_directory=game_directory,
        server_name=server_name,
        default_map=source.default_map,
        max_players=source.max_players,
        game_mode=source.game_mode,
        game_type=source.game_type,
        session_manager=session_manager,
        additional_parameters=getattr(source, "additional_parameters", None) or None,
    )


async def prepare_clone_server(
    db: AsyncSession,
    source: Server,
    owner_id: int,
    values: ServerCloneInput,
) -> ServerCreate:
    """Validate uniqueness and assemble a normal ``ServerCreate`` command."""

    if not source.ssh_password:
        raise CloneSourceError("The source server has no saved SSH password")
    source_directory = normalize_game_directory(source.game_directory)

    servers = await _servers_for_user(db, owner_id)
    name = values.name.strip()
    if any(server.name == name for server in servers):
        raise CloneConflictError(f"Server with name '{name}' already exists")

    game_directory = normalize_game_directory(values.game_directory)
    if game_directory == source_directory:
        raise CloneConflictError("The clone game directory must differ from the source server")
    for server in servers:
        if (
            server.host == source.host
            and normalize_game_directory(server.game_directory) == game_directory
        ):
            raise CloneConflictError(
                f"A server with the same host ({source.host}) and game directory ({game_directory}) already exists"
            )

    occupied = _occupied_ports(await _servers_on_host(db, source.host), source.host)
    if not _port_available(values.game_port, occupied):
        raise CloneConflictError(
            f"Game port {values.game_port} conflicts with another server on host {source.host}"
        )

    raw_session_manager = values.session_manager or getattr(source, "session_manager", "tmux")
    session_manager = "screen" if raw_session_manager == "screen" else "tmux"
    use_panel_proxy = bool(getattr(source, "use_panel_proxy", False))
    github_proxy = None if use_panel_proxy else getattr(source, "github_proxy", None) or None
    return ServerCreate.model_validate(
        {
            "name": name,
            "host": source.host,
            "ssh_port": source.ssh_port,
            "ssh_user": source.ssh_user,
            "ssh_password": source.ssh_password,
            "sudo_password": values.sudo_password or getattr(source, "sudo_password", None),
            "apt_mirror": values.apt_mirror or getattr(source, "apt_mirror", None),
            "game_port": values.game_port,
            "game_directory": game_directory,
            "description": values.description,
            "captcha_token": values.captcha_token,
            "captcha_code": values.captcha_code,
            "server_name": values.server_name.strip(),
            "default_map": values.default_map.strip(),
            "max_players": values.max_players,
            "game_mode": values.game_mode.strip(),
            "game_type": values.game_type.strip(),
            "rcon_password": values.rcon_password,
            "steam_account_token": values.steam_account_token,
            "additional_parameters": (
                values.additional_parameters
                if values.additional_parameters_override
                else getattr(source, "additional_parameters", None)
            ),
            "session_manager": session_manager,
            "use_panel_proxy": use_panel_proxy,
            "github_proxy": github_proxy,
        }
    )


__all__ = [
    "CloneConflictError",
    "CloneSourceError",
    "ServerCloneInput",
    "ServerCloneTemplateData",
    "build_clone_template",
    "normalize_game_directory",
    "prepare_clone_server",
]
