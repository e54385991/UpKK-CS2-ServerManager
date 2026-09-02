"""SSH connection facade for the legacy SSHManager import path."""

from .common import _schedule_status_update, ssh_connection_pool  # noqa: F401
from .connection_download import DownloadConnectionMixin
from .connection_runtime import ConnectionRuntimeMixin


class ConnectionMixin(DownloadConnectionMixin, ConnectionRuntimeMixin):
    """Composed connection capabilities."""

    STREAMING_OUTPUT_MAX_BYTES = 2 * 1024 * 1024
    SUPPORTED_ARCHIVE_FORMATS_LABEL = (
        ".zip, .7z, .rar, .tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tbz, "
        ".tar.xz, .txz, .tar.zst, .tzst, .tar.lzma, .tlz, .gz, .bz2, .xz, "
        ".zst, .zstd, .lzma"
    )
    TAR_ARCHIVE_TYPES = frozenset({"tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lzma"})
    SEVEN_ZIP_ARCHIVE_TYPES = frozenset({"7z", "rar"})
    SINGLE_FILE_ARCHIVE_TYPES = frozenset({"gz", "bz2", "xz", "zst", "lzma"})
    ARCHIVE_TYPES_ALLOW_BACKSLASH = TAR_ARCHIVE_TYPES | SEVEN_ZIP_ARCHIVE_TYPES
    REMOTE_DOWNLOAD_FILENAME_MAX_BYTES = 255
    REMOTE_DOWNLOAD_URL_MAX_LENGTH = 4096
    REMOTE_DOWNLOAD_REDIRECT_CODES = frozenset((301, 302, 303, 307, 308))
    METAMOD_DOWNLOADS_URL = "https://www.sourcemm.net/downloads.php?branch=dev"
    METAMOD_GITHUB_RELEASES_API = (
        "https://api.github.com/repos/alliedmodders/metamod-source/releases?per_page=50"
    )
    METAMOD_LINUX_DOWNLOAD_PATTERN = (
        r"https://github\.com/alliedmodders/metamod-source/releases/download/"
        r"2\.0\.0\.[0-9]+/mmsource-2\.0\.0-git[0-9]+-linux\.tar\.gz"
    )
    pass
