"""Backward-compatible facade for modular SSH server operations."""

# ruff: noqa: F401,F403,F405

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


class SSHManager(
    ConnectionMixin,
    GameLifecycleMixin,
    PluginOperationsMixin,
    RemoteFileMixin,
):
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

    STEAMCMD_MAX_RETRIES = 5  # Maximum number of retry attempts (not counting the initial attempt)

    STEAMCMD_RETRY_DELAY = (
        5  # Initial delay in seconds between retries (will use exponential backoff)
    )

    CS2_EXECUTABLE_RELATIVE_PATH = "cs2/game/bin/linuxsteamrt64/cs2"

    METAMOD_DOWNLOADS_URL = "https://www.sourcemm.net/downloads.php?branch=dev"

    METAMOD_GITHUB_RELEASES_API = (
        "https://api.github.com/repos/alliedmodders/metamod-source/releases?per_page=50"
    )

    METAMOD_LINUX_DOWNLOAD_PATTERN = (
        r"https://github\.com/alliedmodders/metamod-source/releases/download/"
        r"2\.0\.0\.[0-9]+/mmsource-2\.0\.0-git[0-9]+-linux\.tar\.gz"
    )

    STEAMCMD_RETRYABLE_ERRORS = [
        "timeout",
        "timed out",
        "connection",
        "network",
        "failed to download",
        "download failed",
        "corrupt",
        "error downloading",
        "unable to download",
        "http error",
        "failed to install",
        "no connection",
    ]

    def __init__(
        self,
        use_pool: bool = True,
        *,
        connection_pool: Optional[Any] = None,
        http_resource: Optional[Any] = None,
    ):
        """
        Initialize SSH Manager

        Args:
            use_pool: Whether to use connection pooling (default: True)
            connection_pool: Pool owned by the current application.  Omitting
                it preserves the legacy process-global facade for non-request
                callers while FastAPI dependencies always pass an explicit
                application resource.
            http_resource: Outbound HTTP adapter owned by the current
                application. Omitting it preserves the legacy process-global
                facade for non-request callers; FastAPI dependencies always
                pass an explicit application resource.
        """
        if http_resource is None:
            from modules.http_helper import http_helper

            http_resource = http_helper
        self.conn: Optional[asyncssh.SSHClientConnection] = None
        self.connection_lease: Optional[ConnectionLease] = None
        self.connection_pool = (
            connection_pool if connection_pool is not None else ssh_connection_pool
        )
        self.http_resource = http_resource
        self.use_pool = use_pool
        self.current_server: Optional[Server] = None
        self.last_plugin_backup: Optional[Dict[str, Any]] = None
