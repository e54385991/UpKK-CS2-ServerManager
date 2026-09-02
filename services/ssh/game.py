"""Game operations facade for SSHManager."""

from services.steamcmd_guard import steamcmd_cancel_requested  # noqa: F401
from services.steamcmd_retry import resolve_steamcmd_max_retries  # noqa: F401

from .game_deployment import GameDeploymentMixin
from .game_selfcheck import GameSelfCheckMixin
from .game_start import GameStartMixin
from .game_steamcmd import GameSteamcmdMixin
from .game_update import GameUpdateMixin


class GameLifecycleMixin(
    GameDeploymentMixin,
    GameSelfCheckMixin,
    GameStartMixin,
    GameSteamcmdMixin,
    GameUpdateMixin,
):
    """Composed game capabilities kept at the legacy import path."""

    pass
