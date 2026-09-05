"""Deep, fully isolated coverage for game-directory cleanup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.game_cleanup_service import GameCleanupService


def _server(**overrides):
    values = {"game_directory": "/srv/cs2"}
    values.update(overrides)
    return SimpleNamespace(**values)


def _record(path, *, kind="f", size="10", modified="2"):
    return {
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "type": "directory" if kind == "d" else "file",
        "size": int(size),
        "modified": float(modified),
    }


def test_cleanup_paths_parsers_and_commands_cover_safety_edges():
    service = GameCleanupService()
    server = _server()
    assert service.game_dir(server) == "/srv/cs2"
    assert service.csgo_logs_dir(server).endswith("game/csgo/logs")
    assert service.css_logs_dir(server).endswith("counterstrikesharp/logs")
    assert service.workshop_temp_dir(server).endswith("workshop/temp")
    assert len(service.safe_roots(server)) == 3
    assert service.is_archive_path("/tmp/A.ZIP")
    assert not service.is_archive_path("/tmp/readme")
    assert service.is_workshop_temp_path(server, service.workshop_temp_dir(server) + "/x")
    assert not service.is_path_safe(server, "/srv/cs20/evil")
    assert not service.is_path_safe(_server(game_directory="/"), "/etc")
    assert not service.is_path_safe(server, "/srv/cs2/a\x00b")
    assert not service.is_path_safe(server, "/srv/cs2/a\nb")
    assert service._record_from_parts(server, "f", "bad", "bad", "/srv/cs2/a.log")["size"] == 0
    assert service._record_from_parts(server, "l", "1", "1", "/srv/cs2/link") is None
    assert service._record_from_parts(server, "f", "1", "1", "/etc/no") is None
    assert service._parse_find_output(server, "f\t2\t3\t/srv/cs2/a\0broken")
    assert "find" in service._find_named_files_command(server, ("*.log",))
    assert "mindepth" in service._find_children_command("/srv/cs2")
    assert (
        service._filter_nested_items([_record("/srv/cs2/a/b"), _record("/srv/cs2/a", kind="d")])[0][
            "path"
        ]
        == "/srv/cs2/a"
    )
    assert service._empty_scan(server)["safe_item_count"] == 0


@pytest.mark.asyncio
async def test_cleanup_command_text_fallback_process_and_find_events(monkeypatch):
    service = GameCleanupService()
    server = _server()

    fallback = SimpleNamespace(conn=None, execute_command=AsyncMock(return_value=(True, "out", "")))
    chunks = [b"f\t2\t3\t/srv/cs2/a.log\0", b""]

    class _Stdout:
        async def read(self, _size):
            return chunks.pop(0)

    class _Process:
        stdout = _Stdout()
        wait = AsyncMock()

        def close(self):
            self.closed = True

    process = _Process()

    async def create_process(command, **kwargs):
        assert command == "scan"
        return process

    fallback.conn = SimpleNamespace(create_process=create_process)
    output = [part async for part in service._iter_command_text(fallback, "scan", 3)]
    assert output == ["f\t2\t3\t/srv/cs2/a.log\0"]
    fallback.conn = None
    assert [part async for part in service._iter_command_text(fallback, "scan", 3)] == ["out"]

    ssh = SimpleNamespace(conn=None, execute_command=AsyncMock(return_value=(False, "", "offline")))
    events = [
        event async for event in service._iter_find_records(ssh, server, "scan", parse_limit=1)
    ]
    assert events == [{"type": "error", "message": "offline"}]

    async def fake_text(*_args, **_kwargs):
        yield ""
        yield "f\t4\t1\t/srv/cs2/a.log\0"

    monkeypatch.setattr(service, "_iter_command_text", fake_text)
    events = [
        event
        async for event in service._iter_find_records(
            ssh, server, "scan", list_limit=1, parse_limit=1
        )
    ]
    assert [event["type"] for event in events] == ["heartbeat", "progress", "complete"]
    assert events[-1]["truncated"] is True


@pytest.mark.asyncio
async def test_cleanup_iter_scan_projects_all_phases(monkeypatch):
    service = GameCleanupService()
    server = _server()
    ssh = SimpleNamespace(conn=object(), connect=AsyncMock(return_value=(True, "")))
    safe = [
        [_record(service.csgo_logs_dir(server) + "/old.log")],
        [_record(service.css_logs_dir(server) + "/sharp.log")],
        [_record(service.workshop_temp_dir(server) + "/temp.log")],
        [_record("/srv/cs2/other.log")],
        [_record("/srv/cs2/backups/a.zip")],
        [_record(service.workshop_dir(server) + "/123", kind="d", size="20")],
    ]
    calls = 0

    async def fake_find(*_args, **_kwargs):
        nonlocal calls
        records = safe[min(calls, len(safe) - 1)]
        calls += 1
        yield {"type": "heartbeat"}
        yield {"type": "progress", "found": len(records), "size": sum(x["size"] for x in records)}
        yield {
            "type": "complete",
            "listed": records,
            "found": len(records),
            "size": 20,
            "truncated": False,
        }

    async def fake_size(*_args, **_kwargs):
        return 100

    monkeypatch.setattr(service, "_iter_find_records", fake_find)
    monkeypatch.setattr(service, "_directory_size", fake_size)
    events = [event async for event in service.iter_scan(ssh, server)]
    assert events[0]["phase"] == "safe_roots"
    assert any(event.get("phase") == "archives" for event in events)
    done = events[-1]["data"]
    assert len(done["safe_items"]) == 4
    assert len(done["archive_items"]) == 1
    assert done["workshop_summary"]["item_count"] == 1
    assert done["total_size"] > 0

    bad = SimpleNamespace(conn=None, connect=AsyncMock(return_value=(False, "down")))
    assert [event async for event in service.iter_scan(bad, server)] == [
        {"type": "error", "message": "Connection failed: down"}
    ]
    assert [event async for event in service.iter_scan(ssh, _server(game_directory="/"))][0][
        "type"
    ] == "error"


@pytest.mark.asyncio
async def test_cleanup_delete_and_purge_cover_modes_failures_and_limits(monkeypatch):
    service = GameCleanupService()
    server = _server()
    ssh = SimpleNamespace(
        conn=object(),
        connect=AsyncMock(return_value=(True, "")),
        delete_path=AsyncMock(side_effect=[(True, ""), (False, "read-only")]),
    )
    monkeypatch.setattr(
        service,
        "_collect_safe_items",
        AsyncMock(return_value=(True, [_record("/srv/cs2/a.log"), _record("/srv/cs2/b.log")], "")),
    )
    ok, result, error = await service.delete(ssh, server, "safe")
    assert not ok and not error and result["deleted_count"] == 1 and result["failed_items"]

    ok, result, error = await service.delete(ssh, server, "archives", paths=[])
    assert not ok and "select" in error
    ok, result, error = await service.delete(ssh, server, "archives", paths=["/etc/a.zip"])
    assert not ok and "valid cleanup candidates" in error
    ok, result, error = await service.delete(ssh, server, "workshop", confirmation_text="no")
    assert not ok and "DELETE WORKSHOP" in error
    ok, result, error = await service.delete(ssh, server, "invalid")
    assert not ok and "Invalid" in error

    monkeypatch.setattr(
        service,
        "_scan_direct_children",
        AsyncMock(
            return_value=(True, [_record(service.workshop_dir(server) + "/1", kind="d")], "")
        ),
    )
    ssh.delete_path.side_effect = None
    ssh.delete_path.return_value = (True, "")
    ok, result, error = await service.delete(
        ssh, server, "workshop", confirmation_text="DELETE WORKSHOP"
    )
    assert ok and result["deleted_count"] >= 0 and not error

    monkeypatch.setattr(
        service,
        "_find_records",
        AsyncMock(return_value=(True, [_record(service.csgo_logs_dir(server) + "/old.log")], "")),
    )
    ssh.delete_path.return_value = (True, "")
    ok, result, _ = await service.purge_old_logs(ssh, server, 0)
    assert ok and result["deleted_count"] == 1
    assert (await service.purge_old_logs(ssh, _server(game_directory="/"), 7))[0] is False
