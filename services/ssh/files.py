"""Remote file operations facade for SSHManager."""

from .file_archive import ArchiveOperationsMixin
from .file_basic import BasicFileOperationsMixin
from .file_download_extract import DownloadExtractMixin
from .file_transfer import FileTransferMixin


class RemoteFileMixin(
    ArchiveOperationsMixin,
    BasicFileOperationsMixin,
    DownloadExtractMixin,
    FileTransferMixin,
):
    """Composed file capabilities kept at the legacy import path."""

    pass
