"""Focused tests for generic plugin configuration parsing and path safety."""

from types import SimpleNamespace

import asyncssh
import pytest
from fastapi import HTTPException

from api.routes.plugin_configs import _file_for_source
from services.plugin_config_service import (
    MAX_CONFIG_BYTES,
    PluginConfigError,
    apply_visual_changes,
    normalize_relative_path,
    parse_config,
    scan_source,
    validate_raw_content,
)

MULTIADDON_CONFIG = """// Extra addon settings, this is only executed once on plugin load
mm_addon_mount_download 1
mm_extra_addons \t\t\t\t"3232287131,3658046927,3716458508"
mm_extra_addons_timeout\t\t\t120
mm_cache_clients_with_addons 0
mm_block_disconnect_messages 1
"""


def test_cfg_visual_fields_and_update_preserve_everything_except_value():
    parsed = parse_config(MULTIADDON_CONFIG, "multiaddonmanager.cfg")

    assert parsed.visual_supported is True
    assert [field.key for field in parsed.fields] == [
        "mm_addon_mount_download",
        "mm_extra_addons",
        "mm_extra_addons_timeout",
        "mm_cache_clients_with_addons",
        "mm_block_disconnect_messages",
    ]
    assert parsed.fields[0].kind == "integer"
    assert parsed.fields[1].kind == "string"
    assert "Extra addon settings" in parsed.fields[0].comment

    updated = apply_visual_changes(
        MULTIADDON_CONFIG,
        "multiaddonmanager.cfg",
        [{"id": parsed.fields[1].field_id, "value": "111,222"}],
    )

    assert updated == MULTIADDON_CONFIG.replace('"3232287131,3658046927,3716458508"', '"111,222"')


def test_cfg_keeps_double_slash_inside_quotes_and_handles_duplicate_commands():
    content = 'endpoint "https://example.com/a//b" // help\nretry 1\nretry 2\n'
    parsed = parse_config(content, "example.cfg")

    assert parsed.fields[0].value == "https://example.com/a//b"
    assert parsed.fields[0].comment == "help"
    assert parsed.fields[1].field_id != parsed.fields[2].field_id


def test_jsonc_nested_scalar_update_preserves_comments_trailing_commas_and_bom():
    content = '\ufeff{\r\n  // keep\r\n  "items": [{"enabled": true, "name": "Old",}],\r\n}\r\n'
    parsed = parse_config(content, "advertisements.jsonc")

    assert parsed.visual_supported is True
    assert [field.field_id for field in parsed.fields] == [
        "json:/items/0/enabled",
        "json:/items/0/name",
    ]

    updated = apply_visual_changes(
        content,
        "advertisements.jsonc",
        [
            {"id": "json:/items/0/enabled", "value": False},
            {"id": "json:/items/0/name", "value": "新公告"},
        ],
    )

    assert updated.startswith("\ufeff")
    assert "// keep\r\n" in updated
    assert '"enabled": false' in updated
    assert '"name": "新公告"' in updated
    assert ",}],\r\n}" in updated


def test_ini_sections_comments_and_spacing_are_preserved():
    content = "[network]\n; seconds\ntimeout  =  30 ; keep\nenabled=yes\n"
    parsed = parse_config(content, "plugin.ini")

    assert [field.group for field in parsed.fields] == ["network", "network"]
    assert parsed.fields[0].comment == "seconds\nkeep"
    assert parsed.fields[1].kind == "boolean"

    updated = apply_visual_changes(
        content,
        "plugin.ini",
        [
            {"id": parsed.fields[0].field_id, "value": 45},
            {"id": parsed.fields[1].field_id, "value": False},
        ],
    )
    assert updated == "[network]\n; seconds\ntimeout  =  45 ; keep\nenabled=no\n"


def test_unknown_formats_use_raw_mode_and_invalid_json_raw_save_is_rejected():
    parsed = parse_config("name: value\n", "plugin.yaml")
    assert parsed.format == "raw"
    assert parsed.visual_supported is False

    with pytest.raises(PluginConfigError):
        validate_raw_content('{"broken": }', "plugin.json")


def test_raw_configuration_size_limit_is_ten_mib():
    validate_raw_content(" " * (1024 * 1024 + 1), "plugin.cfg")

    with pytest.raises(PluginConfigError, match="10 MiB"):
        validate_raw_content(" " * (MAX_CONFIG_BYTES + 1), "plugin.cfg")


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("cs2/game/csgo/cfg/test.cfg", "cs2/game/csgo/cfg/test.cfg"),
        ("/home/cs2/cs2/game/csgo/cfg/test.cfg", "cs2/game/csgo/cfg/test.cfg"),
        (".", "."),
    ],
)
def test_path_normalization_accepts_relative_and_in_root_absolute_paths(requested, expected):
    assert normalize_relative_path("/home/cs2", requested) == expected


@pytest.mark.parametrize(
    "requested", ["../outside", "/etc/passwd", "cfg\\test.cfg", "cfg/\x00test"]
)
def test_path_normalization_rejects_unsafe_paths(requested):
    with pytest.raises(PluginConfigError):
        normalize_relative_path("/home/cs2", requested)


def test_registered_directory_and_file_scope_are_enforced():
    server = SimpleNamespace(game_directory="/home/cs2")
    directory = SimpleNamespace(source_type="directory", relative_path="cs2/game/csgo/cfg")
    exact_file = SimpleNamespace(source_type="file", relative_path="custom/plugin.data")

    assert _file_for_source(server, directory, "cs2/game/csgo/cfg/test.cfg").endswith("test.cfg")
    assert (
        _file_for_source(server, exact_file, "/home/cs2/custom/plugin.data") == "custom/plugin.data"
    )

    with pytest.raises(HTTPException) as outside:
        _file_for_source(server, directory, "cs2/game/csgo/server.dll")
    assert outside.value.status_code == 403

    with pytest.raises(HTTPException) as unsupported:
        _file_for_source(server, directory, "cs2/game/csgo/cfg/plugin.dll")
    assert unsupported.value.status_code == 415


class _FakeSFTP:
    def __init__(self, directories):
        self.directories = directories
        self.closed = False

    async def lstat(self, path):
        if path not in self.directories:
            raise AssertionError(f"Unexpected lstat path: {path}")
        return SimpleNamespace(type=asyncssh.FILEXFER_TYPE_DIRECTORY, size=0, mtime=1)

    async def scandir(self, path):
        raise AssertionError("Directory scans should use one remote find process")
        yield path

    def exit(self):
        self.closed = True

    async def wait_closed(self):
        return None


class _FakeByteStream:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


class _FakeFindProcess:
    def __init__(self, chunks, exit_status=0):
        self.stdout = _FakeByteStream(chunks)
        self.stderr = _FakeByteStream([])
        self.exit_status = exit_status
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    async def wait(self):
        return SimpleNamespace(exit_status=self.exit_status)


class _FakeConnection:
    def __init__(self, sftp, process):
        self.sftp = sftp
        self.process = process
        self.commands = []

    async def start_sftp_client(self):
        return self.sftp

    async def create_process(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return self.process


class _FakeSSHManager:
    def __init__(self, sftp, process):
        self.conn = _FakeConnection(sftp, process)

    async def validate_path_within_base(self, *_args, **_kwargs):
        return True, ""


def _entry(name, entry_type, size=0, mtime=1):
    return SimpleNamespace(
        filename=name,
        attrs=SimpleNamespace(type=entry_type, size=size, mtime=mtime),
    )


@pytest.mark.asyncio
async def test_recursive_scan_filters_extensions_and_ignores_symlinks():
    base = "/home/cs2/configs"
    sftp = _FakeSFTP({base: []})
    output = b"\0".join(
        [
            b"D",
            b"",
            b"D",
            b"Nested",
            b"F",
            b"root.json",
            b"20",
            b"10.0",
            b"F",
            b"Nested/server.cfg",
            b"40",
            b"12.0",
            b"F",
            b"Nested/notes.txt",
            b"50",
            b"13.0",
            b"",
        ]
    )
    process = _FakeFindProcess([output[:19], output[19:47], output[47:]])
    manager = _FakeSSHManager(sftp, process)

    result = await scan_source(
        manager,
        SimpleNamespace(game_directory="/home/cs2"),
        "configs",
        "directory",
    )

    assert result["truncated"] is False
    assert [item["tree_path"] for item in result["files"]] == [
        "Nested/notes.txt",
        "Nested/server.cfg",
        "root.json",
    ]
    assert sftp.closed is True
    assert len(manager.conn.commands) == 1
    command, kwargs = manager.conn.commands[0]
    assert command.startswith("LC_ALL=C find -P /home/cs2/configs")
    assert "*.dll" not in command
    assert kwargs == {"encoding": None}
