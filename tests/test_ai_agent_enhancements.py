"""Security and planning coverage for the enhanced CS2 agent tools."""

from __future__ import annotations

import stat
import zipfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from api.routes import ai as ai_routes
from modules.models import AIToolRun, ManagedPluginFile, MarketPlugin
from modules.schemas.ai import AIToolDecisionRequest
from modules.utils import get_current_time
from services import ai_orchestrator
from services.ai_prompt import CORE_RULES
from services.ai_tools import TOOLS_BY_NAME, _safe_css_log_name
from services.github_plugin_plan_service import (
    GitHubPlanError,
    _archive_entries,
    _detect_mapping,
    _is_linux_archive,
    _mapped_files,
    _safe_entry_name,
    _validate_release_contents,
    normalize_public_repo_url,
)
from services.plugin_diagnostic_service import (
    ACTIVE_DIAGNOSTIC_STATUSES,
    _group_candidates,
    get_diagnostic_recommendation,
)
from services.plugin_installation import _build_backup_command, _build_rollback_command


@pytest.mark.parametrize(
    "value",
    (
        "http://github.com/owner/repo",
        "https://user@github.com/owner/repo",
        "https://github.com/owner/repo?asset=1",
        "https://127.0.0.1/owner/repo",
        "https://github.com/owner/repo/releases/latest",
        "https://github.com/owner/%2e%2e",
    ),
)
def test_github_repository_normalization_rejects_noncanonical_urls(value):
    with pytest.raises(GitHubPlanError):
        normalize_public_repo_url(value)


def test_github_repository_normalization_returns_canonical_identity():
    assert normalize_public_repo_url("https://github.com/KZGlobalTeam/cs2kz-metamod.git") == (
        "KZGlobalTeam",
        "cs2kz-metamod",
        "https://github.com/KZGlobalTeam/cs2kz-metamod",
    )


@pytest.mark.parametrize(
    "name",
    ("../escape", "/etc/passwd", "C:/Windows/file", "safe/../../escape", "safe\nfile"),
)
def test_archive_entry_names_reject_escape_and_control_characters(name):
    with pytest.raises(GitHubPlanError):
        _safe_entry_name(name)


def test_zip_archive_rejects_links_case_collisions_and_bombs(tmp_path):
    link_archive = tmp_path / "link.zip"
    with zipfile.ZipFile(link_archive, "w") as archive:
        link = zipfile.ZipInfo("addons/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/etc/passwd")
    with pytest.raises(GitHubPlanError, match="links"):
        _archive_entries(str(link_archive), "plugin-linux.zip", link_archive.stat().st_size)

    collision_archive = tmp_path / "collision.zip"
    with zipfile.ZipFile(collision_archive, "w") as archive:
        archive.writestr("addons/Plugin.dll", "one")
        archive.writestr("addons/plugin.dll", "two")
    with pytest.raises(GitHubPlanError, match="case-colliding"):
        _archive_entries(
            str(collision_archive), "plugin-linux.zip", collision_archive.stat().st_size
        )

    bomb_archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("addons/repeated.bin", b"0" * (1024 * 1024))
    with pytest.raises(GitHubPlanError, match="compression ratio"):
        _archive_entries(str(bomb_archive), "plugin-linux.zip", bomb_archive.stat().st_size)


def test_csgo_wrappers_flat_css_and_config_mapping_are_deterministic():
    wrapped = [
        {"path": "package/csgo/addons/metamod/plugin.vdf", "size": 4, "is_dir": False},
        {"path": "package/csgo/cfg/plugin.cfg", "size": 4, "is_dir": False},
    ]
    prefix, mapping, required = _detect_mapping(wrapped, "plugin")
    assert prefix == "package/csgo"
    assert required is False
    mapped = _mapped_files(wrapped, mapping)
    assert [item["target_path"] for item in mapped] == [
        "addons/metamod/plugin.vdf",
        "cfg/plugin.cfg",
    ]
    assert mapped[1]["file_role"] == "config"

    flat = [
        {"path": "Example.dll", "size": 4, "is_dir": False},
        {"path": "Example.deps.json", "size": 4, "is_dir": False},
    ]
    prefix, mapping, required = _detect_mapping(flat, "Example")
    assert prefix is None
    assert required is False
    assert mapping[0]["target"] == "addons/counterstrikesharp/plugins/Example"


def test_stable_linux_asset_filter_prefers_installable_release_archives():
    assert _is_linux_archive("cs2kz-linux-master.tar.gz") is True
    assert _is_linux_archive("cs2kz-linux-master-upgrade.tar.gz") is True
    assert _is_linux_archive("plugin-windows.zip") is False
    assert _is_linux_archive("plugin-debug-linux.zip") is False
    assert _is_linux_archive("Source code.zip") is False


@pytest.mark.parametrize(
    "path",
    ("addons/plugin/install.sh", "addons/plugin/debug.pdb", "src/plugin.csproj"),
)
def test_release_content_rejects_scripts_builds_and_debug_artifacts(path):
    with pytest.raises(GitHubPlanError, match="source-build"):
        _validate_release_contents([{"path": path, "is_dir": False}])


def test_new_agent_tool_schemas_cannot_select_identity_server_or_paths():
    tool_names = {
        "list_css_error_logs",
        "read_css_error_log",
        "plan_plugin_crash_isolation",
        "execute_plugin_crash_isolation",
        "get_plugin_crash_isolation",
        "restore_plugin_quarantine",
        "search_github_cs2_plugins",
        "inspect_github_plugin",
        "plan_github_plugin_install",
        "apply_github_plugin_install",
    }
    for name in tool_names:
        properties = TOOLS_BY_NAME[name].input_model.model_json_schema().get("properties", {})
        assert "user_id" not in properties
        assert "server_id" not in properties
        assert "ssh_host" not in properties
        assert "game_directory" not in properties
        assert "relative_path" not in properties


def test_css_log_reader_accepts_only_fixed_directory_basenames():
    assert _safe_css_log_name("errors_2026-08-01.log") == "errors_2026-08-01.log"
    for value in ("../console.log", "subdir/error.log", ".hidden.log", "plugin.dll"):
        with pytest.raises(ValueError):
            _safe_css_log_name(value)


@pytest.mark.asyncio
async def test_declared_market_dependencies_form_one_diagnostic_group(monkeypatch):
    managed = [
        SimpleNamespace(
            id=1,
            display_name="RootPlugin",
            repo_url="https://github.com/example/RootPlugin",
            custom_install_path=None,
            market_plugin_id=10,
        ),
        SimpleNamespace(
            id=2,
            display_name="SharedDependency",
            repo_url="https://github.com/example/SharedDependency",
            custom_install_path=None,
            market_plugin_id=20,
        ),
    ]

    class Result:
        def scalars(self):
            return self

        def all(self):
            return managed

    class DB:
        async def execute(self, _statement):
            return Result()

    market = [
        SimpleNamespace(id=10, dependencies="20"),
        SimpleNamespace(id=20, dependencies=None),
    ]
    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=market))
    candidates = [
        {"key": "metamod:rootplugin", "name": "RootPlugin.vdf"},
        {"key": "metamod:shareddependency", "name": "SharedDependency.vdf"},
    ]

    groups = await _group_candidates(DB(), 7, candidates)

    assert len(groups) == 1
    assert groups[0]["reason"] == "declared_dependency"
    assert groups[0]["candidate_keys"] == [
        "metamod:rootplugin",
        "metamod:shareddependency",
    ]


def test_quarantine_states_block_automation_until_explicit_restore():
    assert "interrupted" in ACTIVE_DIAGNOSTIC_STATUSES
    assert "inconclusive" in ACTIVE_DIAGNOSTIC_STATUSES
    assert "completed_with_quarantine" in ACTIVE_DIAGNOSTIC_STATUSES


@pytest.mark.asyncio
async def test_post_update_restart_failures_only_recommend_diagnosis(monkeypatch):
    from services import plugin_diagnostic_service
    from services.server_monitor import server_monitor

    now = get_current_time()
    server = SimpleNamespace(id=817, last_update_time=now - timedelta(minutes=10))
    monkeypatch.setattr(
        plugin_diagnostic_service,
        "authorized_server",
        AsyncMock(return_value=server),
    )
    server_monitor.restart_history[server.id] = [
        now - timedelta(minutes=3),
        now - timedelta(minutes=1),
    ]
    try:
        result = await get_diagnostic_recommendation(
            SimpleNamespace(), SimpleNamespace(id=12), server.id
        )
    finally:
        server_monitor.restart_history.pop(server.id, None)

    assert result["recommended"] is True
    assert result["reason"] == "post_update_start_failures"
    assert result["restart_count"] == 2


def test_write_approval_and_rollback_are_revision_bound():
    tool_run = AIToolRun(
        run_id="run",
        tool_call_id="call",
        tool_name="apply_github_plugin_install",
        arguments={"repo_url": "https://github.com/owner/repo"},
        arguments_hash="a" * 64,
        risk="write",
        requires_approval=True,
    )
    assert tool_run.approval_expires_at is None
    backup = _build_backup_command("/tmp/source", "/srv/csgo", "/srv/.upkk/backup")
    rollback = _build_rollback_command("/srv/csgo", "/srv/.upkk/backup")
    assert "manifest.tsv" in backup
    assert "--no-dereference" in backup
    assert "manifest.tsv" in rollback
    assert "--remove-destination" in rollback


def test_requested_changes_create_a_panel_approval_instead_of_only_text():
    assert "call the apply tool in the same run" in CORE_RULES
    assert "Never replace that tool call with text" in CORE_RULES


@pytest.mark.asyncio
async def test_write_approval_is_queued_instead_of_rejected_while_another_runs(monkeypatch):
    run = SimpleNamespace(id="run-1", server_id=None)
    item = SimpleNamespace(
        id="tool-1",
        run_id=run.id,
        tool_name="apply_plugin_plan",
        arguments={},
        arguments_hash="a" * 64,
        risk="write",
        status="pending_approval",
        requires_approval=True,
        approval_expires_at=get_current_time() + timedelta(minutes=5),
        approved_by=None,
        approved_at=None,
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalar_one(self):
            return self.value

    class DB:
        def __init__(self):
            self.results = [Result(item), Result(0)]

        async def execute(self, _statement):
            return self.results.pop(0)

        def add(self, _item):
            pass

        async def commit(self):
            pass

    async def run_for_user(_db, _user, _run_id):
        return run

    def schedule(coroutine):
        coroutine.close()

    monkeypatch.setattr(ai_routes, "_run_for_user", run_for_user)
    monkeypatch.setattr(ai_routes.ai_task_registry, "create", schedule)

    result = await ai_routes.decide_ai_tool(
        run.id,
        item.id,
        AIToolDecisionRequest(decision="approve", arguments_hash=item.arguments_hash),
        DB(),
        SimpleNamespace(id=8),
    )

    assert result == {"status": "queued"}
    assert item.status == "queued"


@pytest.mark.asyncio
async def test_queued_write_emits_queue_then_execution_status(monkeypatch):
    _serialized, arguments_hash = ai_orchestrator.canonical_arguments({})
    user = SimpleNamespace(id=8, is_active=True)
    run = SimpleNamespace(id="run-2", conversation_id="conversation-2")
    tool = SimpleNamespace(
        id="tool-2",
        tool_name="apply_plugin_plan",
        arguments={},
        arguments_hash=arguments_hash,
        risk="write",
        requires_approval=True,
        approved_by=user.id,
        approved_at=get_current_time() - timedelta(minutes=20),
        approval_expires_at=get_current_time() - timedelta(minutes=5),
        status="queued",
        result=None,
        error=None,
        completed_at=None,
        tool_call_id="call-2",
    )
    events = []
    lock_calls = []

    class DB:
        async def get(self, _model, _id):
            return user

        def add(self, _item):
            pass

        async def commit(self):
            pass

    class Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

    class LockService:
        def get(self, *args, **kwargs):
            lock_calls.append((args, kwargs))
            return Lock()

    async def emit(_run_id, event_type, _payload):
        events.append(event_type)

    monkeypatch.setattr(ai_orchestrator, "maintenance_lock_service", LockService())
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)
    monkeypatch.setattr(ai_orchestrator, "execute_tool", AsyncMock(return_value={"success": True}))

    await ai_orchestrator._execute_tool_run(DB(), run, tool, user, None)

    assert lock_calls == [
        (
            (-(user.id + 1),),
            {
                "operation": "ai_user_write",
                "wait": True,
                "wait_timeout": ai_orchestrator.AI_WRITE_QUEUE_WAIT_SECONDS,
                "ttl": 30 * 60,
            },
        )
    ]
    assert events == ["tool_queued", "tool_started", "tool_completed"]
    assert tool.status == "completed"


@pytest.mark.asyncio
async def test_background_task_view_returns_only_non_sensitive_task_progress():
    run = SimpleNamespace(
        id="run-3",
        conversation_id="conversation-3",
        server_id=4,
        status="running",
        error=None,
        created_at=None,
        updated_at=None,
        completed_at=None,
    )
    tool = SimpleNamespace(
        id="tool-3",
        run_id=run.id,
        tool_name="apply_plugin_plan",
        risk="write",
        status="running",
        error=None,
        created_at=None,
        completed_at=None,
    )

    class Result:
        def __init__(self, items):
            self.items = items

        def scalars(self):
            return self

        def all(self):
            return self.items

    class DB:
        def __init__(self):
            self.results = [Result([run]), Result([tool])]

        async def execute(self, _statement):
            return self.results.pop(0)

    tasks = await ai_routes.list_ai_background_tasks(30, DB(), SimpleNamespace(id=8))

    assert len(tasks) == 1
    assert tasks[0].id == run.id
    assert tasks[0].tools[0].tool_name == tool.tool_name
    assert not hasattr(tasks[0].tools[0], "arguments")


def test_managed_plugin_file_unique_key_uses_fixed_size_path_digest():
    ddl = str(CreateTable(ManagedPluginFile.__table__).compile(dialect=mysql.dialect()))

    assert "UNIQUE (managed_plugin_id, path_hash)" in ddl
    assert "UNIQUE (managed_plugin_id, relative_path)" not in ddl
