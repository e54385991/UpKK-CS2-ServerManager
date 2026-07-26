"""Backward-compatible facade for plugin configuration services.

Document parsing is implemented in :mod:`services.plugin_configs.parser`;
SSH/SFTP access is implemented in :mod:`services.plugin_configs.remote`.
Existing callers may continue importing every supported symbol from this
module.
"""

# ruff: noqa: F401

import sys
from typing import Any

from services.plugin_configs import parser as _parser
from services.plugin_configs import remote as _remote
from services.plugin_configs.parser import (
    MAX_CONFIG_BYTES,
    SUPPORTED_DIRECTORY_EXTENSIONS,
    ConfigField,
    ParsedConfig,
    PluginConfigError,
    _decode_line_value,
    _extension,
    _find_inline_comment,
    _JsoncParser,
    _JsonToken,
    _line_offsets,
    _match_case,
    _parse_cfg,
    _parse_ini,
    _serialize_field,
    apply_visual_changes,
    content_revision,
    format_for_filename,
    normalize_relative_path,
    parse_config,
    path_hash,
    validate_raw_content,
)
from services.plugin_configs.remote import (
    MAX_SOURCE_FILES,
    SCAN_ERROR_BYTES,
    SCAN_IDLE_TIMEOUT,
    SCAN_MAX_TOKEN_BYTES,
    SCAN_READ_BYTES,
    SCAN_STOP_TIMEOUT,
    _sftp_root,
    absolute_path,
    atomic_write_text_file,
    browse_directory,
    inspect_source,
    iter_source_scan,
    read_text_file,
    scan_source,
)

# Preserve exception/dataclass identity metadata for integrations which inspect
# or pickle objects imported from the historical module path.
for _compat_type in (
    PluginConfigError,
    ConfigField,
    ParsedConfig,
    _JsonToken,
    _JsoncParser,
):
    _compat_type.__module__ = __name__

del _compat_type


class _CompatibilityModule(type(sys.modules[__name__])):
    """Keep assignment-based patches on the historical module effective."""

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name.startswith("__"):
            return
        for target in (_parser, _remote):
            if name in target.__dict__:
                setattr(target, name, value)


sys.modules[__name__].__class__ = _CompatibilityModule

__all__ = [
    "MAX_CONFIG_BYTES",
    "MAX_SOURCE_FILES",
    "SCAN_ERROR_BYTES",
    "SCAN_IDLE_TIMEOUT",
    "SCAN_MAX_TOKEN_BYTES",
    "SCAN_READ_BYTES",
    "SCAN_STOP_TIMEOUT",
    "SUPPORTED_DIRECTORY_EXTENSIONS",
    "ConfigField",
    "ParsedConfig",
    "PluginConfigError",
    "absolute_path",
    "apply_visual_changes",
    "atomic_write_text_file",
    "browse_directory",
    "content_revision",
    "format_for_filename",
    "inspect_source",
    "iter_source_scan",
    "normalize_relative_path",
    "parse_config",
    "path_hash",
    "read_text_file",
    "scan_source",
    "validate_raw_content",
]
