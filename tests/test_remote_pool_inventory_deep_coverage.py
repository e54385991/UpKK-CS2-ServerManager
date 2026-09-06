"""覆盖远端地图池和插件清单的验证、解析与原子写入。"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import plugin_inventory_service as inventory
from services import remote_map_pool_service as pool
from services.map_management_service import DEFAULT_MAPS_CONFIG, append_map_to_config


def _server(**overrides):
    values = {"id": 3, "game_directory": "/srv/cs2"}
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_remote_map_url_validation_and_fetch(monkeypatch):
    assert pool._validate_remote_map_url_syntax(" https://example.com:8443/maps.txt ") == (
        "https://example.com:8443/maps.txt",
        "example.com",
        8443,
    )
    for value in (
        "",
        "ftp://example.com/x",
        "http://localhost/x",
        "https://u:p@example.com/x",
        "https://example.com/x#f",
    ):
        with pytest.raises(pool.RemoteMapPoolError):
            pool._validate_remote_map_url_syntax(value)
    monkeypatch.setattr(pool, "_resolve_hostname", lambda *_args: {"8.8.8.8"})
    assert await pool.validate_remote_map_url("https://example.com/maps.txt")
    monkeypatch.setattr(pool, "_resolve_hostname", lambda *_args: set())
    with pytest.raises(pool.RemoteMapPoolError, match="did not resolve"):
        await pool.validate_remote_map_url("https://example.com/maps.txt")
    monkeypatch.setattr(pool, "_resolve_hostname", lambda *_args: {"127.0.0.1"})
    with pytest.raises(pool.RemoteMapPoolError, match="public"):
        await pool.validate_remote_map_url("https://example.com/maps.txt")
    monkeypatch.setattr(pool, "_resolve_hostname", lambda *_args: {"not-an-ip"})
    with pytest.raises(pool.RemoteMapPoolError, match="resolved incorrectly"):
        await pool.validate_remote_map_url("https://example.com/maps.txt")

    class _Response:
        def __init__(self, status=200, headers=None, chunks=()):
            self.status_code = status
            self.headers = headers or {}
            self.is_redirect = status in {301, 302, 303, 307, 308}
            self.chunks = list(chunks)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_bytes(self):
            for chunk in self.chunks:
                yield chunk

    class _Client:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args):
            return self.response

    monkeypatch.setattr(pool, "_resolve_hostname", lambda *_args: {"8.8.8.8"})
    content = append_map_to_config(DEFAULT_MAPS_CONFIG, name="de_dust2", workshop_id="123")
    monkeypatch.setattr(
        pool.httpx, "AsyncClient", lambda **_kwargs: _Client(_Response(chunks=[content.encode()]))
    )
    assert await pool.fetch_remote_map_pool("https://example.com/maps.txt") == content
    monkeypatch.setattr(pool.httpx, "AsyncClient", lambda **_kwargs: _Client(_Response(404)))
    with pytest.raises(pool.RemoteMapPoolError, match="HTTP 404"):
        await pool.fetch_remote_map_pool("https://example.com/maps.txt")
    monkeypatch.setattr(
        pool.httpx, "AsyncClient", lambda **_kwargs: _Client(_Response(200, chunks=[b"\xff"]))
    )
    with pytest.raises(pool.RemoteMapPoolError, match="UTF-8"):
        await pool.fetch_remote_map_pool("https://example.com/maps.txt")
    monkeypatch.setattr(
        pool, "validate_remote_map_url", AsyncMock(side_effect=pool.RemoteMapPoolError("bad url"))
    )
    with pytest.raises(pool.RemoteMapPoolError, match="bad url"):
        await pool.fetch_remote_map_pool("https://example.com/maps.txt")


@pytest.mark.asyncio
async def test_remote_map_redirect_replace_and_sync(monkeypatch):
    server = _server()
    content = append_map_to_config(DEFAULT_MAPS_CONFIG, name="de_dust2", workshop_id="123")

    class _Response:
        is_redirect = True
        status_code = 302
        headers = {"location": "/maps.txt"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_bytes(self):
            yield b""

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args):
            return _Response()

    monkeypatch.setattr(pool.httpx, "AsyncClient", lambda **_kwargs: _Client())
    monkeypatch.setattr(
        pool,
        "validate_remote_map_url",
        AsyncMock(
            side_effect=[
                "https://example.com/one",
                "https://example.com/two",
                "https://example.com/three",
                "https://example.com/four",
                "https://example.com/five",
                "https://example.com/six",
            ]
        ),
    )
    with pytest.raises(pool.RemoteMapPoolError, match="redirected too many"):
        await pool.fetch_remote_map_pool("https://example.com/one")

    ssh = SimpleNamespace(
        execute_command=AsyncMock(return_value=(True, "", "")),
        write_file=AsyncMock(return_value=(True, "")),
    )
    assert pool.mapchooser_remote_paths(server)["plugin_dll"].endswith("MapChooser.dll")
    await pool.replace_remote_map_pool(ssh, server, content)
    assert ssh.write_file.await_count == 1
    ssh.execute_command = AsyncMock(return_value=(False, "", "mkdir failed"))
    with pytest.raises(pool.RemoteMapPoolError, match="mkdir failed"):
        await pool.replace_remote_map_pool(ssh, server, content)
    ssh.execute_command = AsyncMock(
        side_effect=[(True, "", ""), (False, "", "mv failed"), (True, "", "")]
    )
    with pytest.raises(pool.RemoteMapPoolError, match="mv failed"):
        await pool.replace_remote_map_pool(ssh, server, content)
    ssh.execute_command = AsyncMock(return_value=(False, "", "missing"))
    with pytest.raises(pool.RemoteMapPoolError, match="not installed"):
        await pool.synchronize_remote_map_pool(ssh, server, "https://example.com/maps.txt")

    ssh.execute_command = AsyncMock(return_value=(True, "", ""))
    monkeypatch.setattr(pool, "fetch_remote_map_pool", AsyncMock(return_value=content))
    assert await pool.synchronize_remote_map_pool(ssh, server, "https://example.com/maps.txt") == (
        content,
        1,
    )


def test_inventory_decoding_aliases_and_evidence():
    encoded = base64.b64encode(b"a.vdf\0a.vdf\0../bad.vdf\0x.dll\0").decode()
    names, truncated = inventory._decode_names(encoded)
    assert names == ["a.vdf", "x.dll"] and not truncated
    with pytest.raises(inventory.PluginInventoryError):
        inventory._decode_names("not base64")
    assert inventory._alias_variants("My-Plugin.CounterStrikeSharp.dll")
    assert inventory._alias_variants(None) == set()
    assert inventory._aliases_match({"xmyplugin"}, {"myplugin"})
    assert not inventory._aliases_match({"short"}, {"other"})

    item = SimpleNamespace(framework_key="metamod", title="Metamod:Source")
    assert (
        inventory.installation_evidence(item, {"frameworks": {"metamod": True}})[0]["kind"]
        == "framework"
    )
    item = SimpleNamespace(title="CounterStrikeSharp")
    assert inventory.installation_evidence(item, {"frameworks": {"counterstrikesharp": True}})
    item = SimpleNamespace(title="My Plugin", custom_install_path="addons/MyPlugin")
    remote = {
        "plugins": [{"kind": "css", "name": "MyPlugin.dll", "relative_path": "x", "key": "k"}]
    }
    assert inventory.installation_evidence(item, remote)[0]["name"] == "MyPlugin.dll"
    managed = [SimpleNamespace(market_plugin_id=1, title="My Plugin")]
    planned = [SimpleNamespace(id=2, title="My Plugin")]
    assert inventory.verified_market_plugin_ids(managed, planned, remote) == {1, 2}


@pytest.mark.asyncio
async def test_inventory_remote_inspection_paths(monkeypatch):
    class _Manager:
        def __init__(self, success=True, stdout=""):
            self.success = success
            self.stdout = stdout
            self.disconnect = AsyncMock()

        async def connect(self, _server):
            return True, "ok"

        async def execute_command(self, _command, **_kwargs):
            return self.success, self.stdout, "remote error"

    server = _server()
    monkeypatch.setattr(inventory, "SSHManager", lambda: _Manager(False))
    with pytest.raises(inventory.PluginInventoryError, match="remote error"):
        await inventory.inspect_remote_plugin_inventory(server)
    monkeypatch.setattr(inventory, "SSHManager", lambda: _Manager(True, "metamod=1\n"))
    with pytest.raises(inventory.PluginInventoryError, match="incomplete"):
        await inventory.inspect_remote_plugin_inventory(server)
    metas = base64.b64encode(b"one.vdf\0two.vdf\0").decode()
    css = base64.b64encode(b"PluginA/PluginA.dll\0").decode()
    output = (
        f"metamod=1\nswiftly=1\ncounterstrikesharp=1\n"
        f"metamod_plugins={metas}\ncounterstrikesharp_plugins={css}\n"
    )
    manager = _Manager(True, output)
    monkeypatch.setattr(inventory, "SSHManager", lambda: manager)
    result = await inventory.inspect_remote_plugin_inventory(server)
    assert result["frameworks"] == {
        "metamod": True,
        "counterstrikesharp": True,
        "swiftly": True,
    }
    assert len(result["plugins"]) == 3
    manager.disconnect.assert_awaited_once()
