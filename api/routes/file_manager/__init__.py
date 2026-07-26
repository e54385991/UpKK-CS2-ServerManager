"""Compatibility facade and router assembly for file_manager."""

# ruff: noqa: F401,F403

from api.routes._compat import compose_router, install_patch_compatibility

from . import archives as _archives
from . import common as _common
from . import downloads as _downloads
from . import files as _files
from .archives import (
    extract_archive,
    get_extraction_status,
    inspect_archive,
)
from .common import *
from .downloads import (
    download_archive_from_url,
    get_download_url_status,
)
from .files import (
    create_directory,
    create_download_ticket,
    delete_path,
    download_file,
    get_file_content,
    list_directory,
    rename_file_or_directory,
    update_file_content,
    upload_file,
)

ENDPOINT_ORDER = (
    "list_directory",
    "get_file_content",
    "update_file_content",
    "upload_file",
    "download_file",
    "create_download_ticket",
    "download_archive_from_url",
    "get_download_url_status",
    "create_directory",
    "delete_path",
    "rename_file_or_directory",
    "inspect_archive",
    "extract_archive",
    "get_extraction_status",
)

router = compose_router(
    (
        _files.transfer_router,
        _downloads.router,
        _files.mutation_router,
        _archives.router,
    )
)

install_patch_compatibility(
    __name__,
    (_common, _files, _downloads, _archives),
)
