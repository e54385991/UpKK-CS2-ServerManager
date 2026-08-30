"""Coverage for relative-path validation used by plugin uninstall."""

from __future__ import annotations

import pytest

from services.plugin_uninstall import normalize_uninstall_paths, validate_uninstall_path


def test_validate_uninstall_path_accepts_relative_plugin_files():
    assert validate_uninstall_path("addons/demo/plugin.dll") == "addons/demo/plugin.dll"
    assert validate_uninstall_path("./addons/demo") == "addons/demo"


def test_normalize_uninstall_paths_deduplicates_and_rejects_empty():
    assert normalize_uninstall_paths(["addons/a", "addons/a", "addons/b"]) == [
        "addons/a",
        "addons/b",
    ]
    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_uninstall_paths(["", "   "])
    with pytest.raises(ValueError, match="at least one"):
        normalize_uninstall_paths([])


@pytest.mark.parametrize(
    "path",
    ["../etc/passwd", "/etc/passwd", "addons/../../secret", "addons/\x00x"],
)
def test_validate_uninstall_path_rejects_traversal(path: str):
    with pytest.raises(ValueError):
        validate_uninstall_path(path)
