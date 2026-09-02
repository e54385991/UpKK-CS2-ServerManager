"""Backward-compatible facade for modular SSH server operations."""

# ruff: noqa: F401,F403,F405

from typing import TYPE_CHECKING

from .ssh import (
    ConnectionMixin,
    GameLifecycleMixin,
    PluginOperationsMixin,
    RemoteFileMixin,
)
from .ssh import common as _common
from .ssh import connection as _connection
from .ssh import files as _files
from .ssh import game as _game
from .ssh import plugins as _plugins
from .ssh.common import *
from .ssh.runtime import SSHRuntimeState
from .steam_inf_service import configure_ssh_manager_factory
from .steamcmd_retry import (
    STEAMCMD_DEFAULT_MAX_RETRIES,
    STEAMCMD_RETRY_DELAY_SECONDS,
)
from .steamcmd_retry import (
    STEAMCMD_RETRYABLE_ERRORS as STEAMCMD_RETRYABLE_ERROR_TOKENS,
)

if TYPE_CHECKING:

    class _SSHCapabilities(
        ConnectionMixin,
        GameLifecycleMixin,
        PluginOperationsMixin,
        RemoteFileMixin,
    ): ...

else:

    class _SSHCapabilities: ...


def _compose_capabilities(*capabilities):
    """Copy capability descriptors onto a facade without multiple inheritance."""

    def decorate(facade):
        for capability in capabilities:
            # Capabilities may themselves be composed from focused mixins.
            # Walk the MRO so the legacy facade exposes every public method
            # without requiring multiple inheritance at runtime.
            for owner in reversed(capability.__mro__):
                if owner is object:
                    continue
                for name, value in vars(owner).items():
                    if name.startswith("__") or hasattr(facade, name):
                        continue
                    setattr(facade, name, value)
        return facade

    return decorate


@_compose_capabilities(
    ConnectionMixin,
    GameLifecycleMixin,
    PluginOperationsMixin,
    RemoteFileMixin,
)
class SSHManager(_SSHCapabilities):
    """Async SSH manager retaining the long-standing public API."""

    MIN_EXPECTED_FILE_SIZE = 1000  # Minimum file size in bytes (1KB) for downloaded packages

    UPLOAD_CHUNK_SIZE = 32768

    DOWNLOAD_CHUNK_SIZE = 262144

    ARCHIVE_MAX_ENTRIES = 20000

    ARCHIVE_MAX_FOLDERS = 20000

    ARCHIVE_MAX_MEMBER_PATH_BYTES = 1024

    ARCHIVE_INSPECT_TIMEOUT = 3600

    ARCHIVE_EXTRACT_TIMEOUT = 3600

    ARCHIVE_LISTING_READ_BYTES = 64 * 1024

    ARCHIVE_LISTING_MAX_LINE_BYTES = (ARCHIVE_MAX_MEMBER_PATH_BYTES * 4) + 4096

    ARCHIVE_LISTING_ERROR_BYTES = 8192

    ARCHIVE_LISTING_STOP_TIMEOUT = 5

    REMOTE_DOWNLOAD_TIMEOUT = 1800

    REMOTE_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024 * 1024

    REMOTE_DOWNLOAD_METADATA_MAX_BYTES = 256 * 1024

    REMOTE_DOWNLOAD_FILENAME_MAX_BYTES = 255

    REMOTE_DOWNLOAD_URL_MAX_LENGTH = 4096

    REMOTE_DOWNLOAD_MAX_REDIRECTS = 10

    REMOTE_DOWNLOAD_REDIRECT_CODES = frozenset((301, 302, 303, 307, 308))

    STEAMCMD_MAX_RETRIES = STEAMCMD_DEFAULT_MAX_RETRIES

    STEAMCMD_RETRY_DELAY = STEAMCMD_RETRY_DELAY_SECONDS

    CS2_EXECUTABLE_RELATIVE_PATH = "cs2/game/bin/linuxsteamrt64/cs2"

    METAMOD_DOWNLOADS_URL = "https://www.sourcemm.net/downloads.php?branch=dev"

    METAMOD_GITHUB_RELEASES_API = (
        "https://api.github.com/repos/alliedmodders/metamod-source/releases?per_page=50"
    )

    METAMOD_LINUX_DOWNLOAD_PATTERN = (
        r"https://github\.com/alliedmodders/metamod-source/releases/download/"
        r"2\.0\.0\.[0-9]+/mmsource-2\.0\.0-git[0-9]+-linux\.tar\.gz"
    )

    STEAMCMD_RETRYABLE_ERRORS = list(STEAMCMD_RETRYABLE_ERROR_TOKENS)

    def __init__(self, use_pool: bool = True, runtime: Optional[SSHRuntimeState] = None):
        """
        Initialize SSH Manager

        Args:
            use_pool: Whether to use connection pooling (default: True)
        """
        self._runtime = runtime or SSHRuntimeState()
        self.use_pool = use_pool

    @property
    def conn(self) -> Optional[asyncssh.SSHClientConnection]:
        return self._runtime.connection

    @conn.setter
    def conn(self, value: Optional[asyncssh.SSHClientConnection]) -> None:
        self._runtime.connection = value

    @property
    def current_server(self) -> Optional[Server]:
        return self._runtime.server

    @current_server.setter
    def current_server(self, value: Optional[Server]) -> None:
        self._runtime.server = value

    @property
    def last_plugin_backup(self) -> Optional[Dict[str, Any]]:
        return self._runtime.last_plugin_backup

    @last_plugin_backup.setter
    def last_plugin_backup(self, value: Optional[Dict[str, Any]]) -> None:
        self._runtime.last_plugin_backup = value


# Register the compatibility facade as the default factory for services that
# depend on SSH capabilities. This keeps their module dependency pointing at a
# narrow port instead of importing the facade back through the mixin graph.
configure_ssh_manager_factory(SSHManager)
