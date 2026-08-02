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
from modules.models import AIMessage, AIToolRun, ManagedPluginFile, MarketPlugin
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
from services.plugin_inventory_service import installation_evidence


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
    ("addons/plugin/install.sh", "addons/plugin/build.vcxproj", "src/plugin.csproj"),
)
def test_release_content_rejects_scripts_builds_and_debug_artifacts(path):
    with pytest.raises(GitHubPlanError, match="source-build"):
        _validate_release_contents([{"path": path, "is_dir": False}])


def test_release_content_allows_pdb_files_common_in_cs2_plugin_releases():
    # .pdb files are commonly shipped in CS2 plugin releases as debug symbols
    _validate_release_contents(
        [{"path": "addons/counterstrikesharp/plugins/Plugin/Plugin.pdb", "is_dir": False}]
    )


def test_metamod_layout_detection_maps_root_metamod_dir_to_addons():
    entries = [
        {"path": "cleanercs2/", "size": 0, "is_dir": True},
        {"path": "cleanercs2/cleanercs2.so", "size": 100, "is_dir": False},
        {"path": "cleanercs2/config.cfg", "size": 50, "is_dir": False},
        {"path": "metamod/", "size": 0, "is_dir": True},
        {"path": "metamod/cleanercs2.vdf", "size": 80, "is_dir": False},
    ]
    prefix, mapping, required = _detect_mapping(entries, "CleanerCS2")
    assert required is False
    assert mapping == [{"source": ".", "target": "addons"}]
    mapped = _mapped_files(entries, mapping)
    targets = {item["target_path"] for item in mapped}
    assert "addons/metamod/cleanercs2.vdf" in targets
    assert "addons/cleanercs2/cleanercs2.so" in targets
    assert "addons/cleanercs2/config.cfg" in targets


def test_plugins_root_dir_maps_to_counterstrikesharp_plugins():
    entries = [
        {"path": "plugins/", "size": 0, "is_dir": True},
        {"path": "plugins/Killfeed_Icons/", "size": 0, "is_dir": True},
        {"path": "plugins/Killfeed_Icons/Killfeed_Icons.dll", "size": 12288, "is_dir": False},
    ]
    prefix, mapping, required = _detect_mapping(entries, "killfeed-icons")
    assert required is False
    assert prefix == "plugins"
    assert mapping == [{"source": "plugins", "target": "addons/counterstrikesharp/plugins"}]
    mapped = _mapped_files(entries, mapping)
    assert len(mapped) == 1
    assert (
        mapped[0]["target_path"]
        == "addons/counterstrikesharp/plugins/Killfeed_Icons/Killfeed_Icons.dll"
    )


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
    assert "at most one write tool" in CORE_RULES


def test_remote_plugin_evidence_does_not_trust_tracking_metadata_alone():
    inventory = {
        "frameworks": {"metamod": False, "counterstrikesharp": False},
        "plugins": [
            {
                "key": "counterstrikesharp:mapchooser",
                "kind": "counterstrikesharp",
                "name": "MapChooser",
                "relative_path": ("cs2/game/csgo/addons/counterstrikesharp/plugins/MapChooser"),
            },
            {
                "key": "metamod:cs2kz.vdf",
                "kind": "metamod",
                "name": "cs2kz.vdf",
                "relative_path": "cs2/game/csgo/addons/metamod/cs2kz.vdf",
            },
        ],
        "truncated": False,
    }

    mapchooser = SimpleNamespace(
        display_name="CS2-Upkk-PanelPLG-Mapchooser",
        repo_url="https://github.com/UpKK-Xnet-Cloud/CS2-Upkk-PanelPLG-Mapchooser",
        framework_key=None,
        custom_install_path=None,
    )
    cs2kz = SimpleNamespace(
        display_name="cs2kz-metamod",
        repo_url="https://github.com/KZGlobalTeam/cs2kz-metamod",
        framework_key=None,
        custom_install_path=None,
    )
    stale = SimpleNamespace(
        display_name="MultiAddonManager",
        repo_url="https://github.com/Source2ZE/MultiAddonManager",
        framework_key=None,
        custom_install_path=None,
    )

    assert installation_evidence(mapchooser, inventory)[0]["name"] == "MapChooser"
    assert installation_evidence(cs2kz, inventory)[0]["name"] == "cs2kz.vdf"
    assert installation_evidence(stale, inventory) == []


@pytest.mark.asyncio
async def test_plugin_plan_does_not_skip_stale_tracking_record(monkeypatch):
    from services import plugin_conflict_service

    target = MarketPlugin(
        id=7,
        github_url="https://github.com/example/MapChooser",
        title="MapChooser",
    )
    tracked = SimpleNamespace(
        market_plugin_id=7,
        display_name="MapChooser",
        repo_url=target.github_url,
        framework_key=None,
        custom_install_path=None,
    )

    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class DB:
        def __init__(self):
            self.results = [Result([tracked]), Result([])]

        async def execute(self, _statement):
            return self.results.pop(0)

    monkeypatch.setattr(
        plugin_conflict_service,
        "_resolve_dependency_order",
        AsyncMock(return_value=([], target)),
    )
    monkeypatch.setattr(
        plugin_conflict_service,
        "inspect_remote_plugin_inventory",
        AsyncMock(
            return_value={
                "frameworks": {"metamod": False, "counterstrikesharp": False},
                "plugins": [],
                "truncated": False,
            }
        ),
    )

    plan = await plugin_conflict_service.build_plugin_install_plan(
        DB(), 4, 7, server=SimpleNamespace(id=4)
    )

    assert plan["already_installed"] == []
    assert plan["steps"][0]["status"] == "install"
    assert plan["steps"][0]["reason"] == "tracking_record_without_remote_evidence"


def test_multiple_write_tools_are_rejected_before_approval_rows_are_created():
    with pytest.raises(ai_orchestrator.AIProviderError, match="multiple write tools"):
        ai_orchestrator._validate_write_tool_batch(["apply_workshop_map", "apply_plugin_plan"])

    ai_orchestrator._validate_write_tool_batch(["list_installed_plugins", "apply_workshop_map"])


@pytest.mark.asyncio
async def test_legacy_multi_write_approval_batch_is_cancelled(monkeypatch):
    now = get_current_time()
    run = SimpleNamespace(
        id="run-legacy",
        conversation_id="conversation-legacy",
        status="waiting_approval",
        error=None,
        completed_at=None,
    )
    tools = [
        SimpleNamespace(
            id="tool-plugin",
            run_id=run.id,
            tool_call_id="call-plugin",
            tool_name="apply_plugin_plan",
            risk="write",
            status="queued",
            approval_expires_at=now + timedelta(minutes=5),
            error=None,
            result=None,
            completed_at=None,
        ),
        SimpleNamespace(
            id="tool-workshop",
            run_id=run.id,
            tool_call_id="call-workshop",
            tool_name="apply_workshop_map",
            risk="write",
            status="pending_approval",
            approval_expires_at=now + timedelta(minutes=5),
            error=None,
            result=None,
            completed_at=None,
        ),
    ]

    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class DB:
        def __init__(self):
            self.results = [Result([run]), Result(tools)]
            self.added = []
            self.commits = 0

        async def execute(self, _statement):
            return self.results.pop(0)

        def add(self, item):
            self.added.append(item)

        async def commit(self):
            self.commits += 1

    db = DB()
    terminal = await ai_orchestrator.reconcile_waiting_approval_runs(db, user_id=8)

    assert terminal == {run.id}
    assert run.status == "cancelled"
    assert {item.status for item in tools} == {"cancelled"}
    messages = [item for item in db.added if isinstance(item, AIMessage)]
    assert len(messages) == 3
    assert messages[-1].tool_name == ai_orchestrator.RUN_ERROR_TOOL_NAME
    assert messages[-1].visible is True
    assert db.commits == 1


@pytest.mark.asyncio
async def test_expired_approval_closes_run_and_tool():
    run = SimpleNamespace(
        id="run-expired",
        conversation_id="conversation-expired",
        status="waiting_approval",
        error=None,
        completed_at=None,
    )
    tool = SimpleNamespace(
        id="tool-expired",
        run_id=run.id,
        tool_call_id="call-expired",
        tool_name="apply_plugin_plan",
        risk="write",
        status="pending_approval",
        approval_expires_at=get_current_time() - timedelta(seconds=1),
        progress_snapshot={
            "steps": [
                {
                    "id": "plugin:17",
                    "label": "Install Plugin",
                    "status": "pending",
                }
            ]
        },
        progress_updated_at=None,
        error=None,
        result=None,
        completed_at=None,
    )

    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class DB:
        def __init__(self):
            self.results = [Result([run]), Result([tool])]

        async def execute(self, _statement):
            return self.results.pop(0)

        def add(self, _item):
            pass

        async def commit(self):
            pass

    terminal = await ai_orchestrator.reconcile_waiting_approval_runs(DB(), run_id=run.id)

    assert terminal == {run.id}
    assert run.status == "expired"
    assert tool.status == "expired"
    assert tool.completed_at is not None
    assert tool.progress_snapshot["steps"][0]["status"] == "interrupted"
    assert tool.progress_snapshot["current_step"] is None


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
    monkeypatch.setattr(
        ai_routes,
        "reconcile_waiting_approval_runs",
        AsyncMock(return_value=set()),
    )
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
                "ttl": ai_orchestrator.AI_WRITE_LOCK_TTL,
            },
        )
    ]
    assert events == ["tool_queued", "tool_started", "tool_completed"]
    assert tool.status == "completed"


def test_approval_plan_snapshots_have_stable_workshop_and_plugin_step_ids():
    workshop_plan, workshop_progress = ai_orchestrator._build_plan_snapshots(
        "apply_workshop_map",
        {
            "target": {"name": "kz_variety_x"},
            "steps": [
                {"action": "install_framework", "framework": "metamod"},
                {"action": "install_framework", "framework": "counterstrikesharp"},
                {"action": "install_market_plugin", "title": "MapChooser"},
                {"action": "patch_plugin_config"},
                {"action": "append_map", "name": "kz_variety_x"},
                {"action": "verify"},
            ],
        },
    )

    assert [step["id"] for step in workshop_plan["steps"]] == [
        "install_metamod",
        "install_counterstrikesharp",
        "install_mapchooser",
        "patch_plugin_config",
        "append_map",
        "verify",
    ]
    assert workshop_progress["total"] == 6
    assert workshop_progress["message"] == "Waiting for approval"

    plugin_plan, plugin_progress = ai_orchestrator._build_plan_snapshots(
        "apply_plugin_plan",
        {
            "steps": [
                {"plugin_id": 17, "title": "Dependency", "status": "already_installed"},
                {"plugin_id": 24, "title": "Target", "status": "install"},
            ]
        },
    )
    assert [step["id"] for step in plugin_plan["steps"]] == ["plugin:17", "plugin:24"]
    assert plugin_progress["steps"][0]["status"] == "skipped"
    assert plugin_progress["completed"] == 1


@pytest.mark.asyncio
async def test_structured_tool_progress_is_persisted_before_completion(monkeypatch):
    user = SimpleNamespace(id=8, is_active=True)
    run = SimpleNamespace(id="run-progress", conversation_id="conversation-progress")
    _plan, progress = ai_orchestrator._build_plan_snapshots(
        "apply_plugin_plan",
        {"steps": [{"plugin_id": 17, "title": "Plugin", "status": "install"}]},
    )
    tool = SimpleNamespace(
        id="tool-progress",
        tool_name="apply_plugin_plan",
        arguments={},
        arguments_hash="a" * 64,
        risk="read",
        requires_approval=False,
        status="pending",
        result=None,
        error=None,
        completed_at=None,
        tool_call_id="call-progress",
        progress_snapshot=progress,
        progress_updated_at=None,
    )
    events = []

    class DB:
        def __init__(self):
            self.commits = 0

        async def get(self, _model, _id):
            return user

        def add(self, _item):
            pass

        async def commit(self):
            self.commits += 1

    async def execute(_name, _arguments, context):
        await context.emit(
            "tool_progress",
            {
                "message": "Installing Plugin",
                "step_id": "plugin:17",
                "step_status": "running",
            },
        )
        return {"success": True}

    async def emit(_run_id, event_type, payload):
        events.append((event_type, payload))

    db = DB()
    monkeypatch.setattr(ai_orchestrator, "execute_tool", execute)
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)

    await ai_orchestrator._execute_tool_run(db, run, tool, user, None)

    assert db.commits >= 3
    assert [step["status"] for step in tool.progress_snapshot["steps"]] == ["completed"]
    assert tool.progress_snapshot["message"] == "Operation completed"
    assert any(event_type == "tool_progress" for event_type, _payload in events)


@pytest.mark.asyncio
async def test_unsuccessful_tool_result_is_not_marked_completed(monkeypatch):
    user = SimpleNamespace(id=8, is_active=True)
    run = SimpleNamespace(id="run-failed-result", conversation_id="conversation-failed-result")
    tool = SimpleNamespace(
        id="tool-failed-result",
        tool_name="apply_workshop_map",
        arguments={},
        arguments_hash="a" * 64,
        risk="read",
        requires_approval=False,
        status="pending",
        result=None,
        error=None,
        completed_at=None,
        tool_call_id="call-failed-result",
        progress_snapshot={"steps": []},
        progress_updated_at=None,
    )

    class DB:
        async def get(self, _model, _id):
            return user

        def add(self, _item):
            pass

        async def commit(self):
            pass

    events = []

    async def emit(_run_id, event_type, _payload):
        events.append(event_type)

    monkeypatch.setattr(ai_orchestrator, "_emit", emit)
    monkeypatch.setattr(
        ai_orchestrator,
        "execute_tool",
        AsyncMock(return_value={"success": False, "message": "Remote verification failed"}),
    )

    await ai_orchestrator._execute_tool_run(DB(), run, tool, user, None)

    assert tool.status == "failed"
    assert tool.error == "Remote verification failed"
    assert events[-1] == "tool_failed"


@pytest.mark.asyncio
async def test_failed_run_persists_a_visible_error_message(monkeypatch):
    run = SimpleNamespace(
        id="run-provider-failure",
        conversation_id="conversation-provider-failure",
        status="running",
        error=None,
        completed_at=None,
    )

    class DB:
        def __init__(self):
            self.added = []
            self.commits = 0

        def add(self, item):
            self.added.append(item)

        async def commit(self):
            self.commits += 1

    emit = AsyncMock()
    db = DB()
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)

    await ai_orchestrator._fail_run(db, run, "Provider response was malformed")

    error_messages = [item for item in db.added if isinstance(item, AIMessage)]
    assert run.status == "failed"
    assert db.commits == 1
    assert len(error_messages) == 1
    assert error_messages[0].content == "Provider response was malformed"
    assert error_messages[0].tool_name == ai_orchestrator.RUN_ERROR_TOOL_NAME
    assert error_messages[0].visible is True
    emit.assert_awaited_once_with(
        run.id,
        "run_failed",
        {"error": "Provider response was malformed"},
    )


@pytest.mark.asyncio
async def test_provider_failures_retry_five_times_with_exponential_backoff(monkeypatch):
    attempts = 0

    async def completion(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= ai_orchestrator.AI_RETRY_MAX_ATTEMPTS:
            raise ai_orchestrator.AIProviderError(f"temporary failure {attempts}")
        return {"content": "Recovered"}

    emit = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr(ai_orchestrator, "create_chat_completion", completion)
    monkeypatch.setattr(ai_orchestrator, "_emit", emit)
    monkeypatch.setattr(ai_orchestrator.asyncio, "sleep", sleep)

    result = await ai_orchestrator._create_provider_response_with_retry(
        SimpleNamespace(),
        [{"role": "user", "content": "status"}],
        run_id="run-retry",
        round_index=3,
        server_selected=True,
    )

    assert result == {"content": "Recovered"}
    assert attempts == 6
    assert [call.args[0] for call in sleep.await_args_list] == [15, 30, 60, 120, 240]
    retry_events = [call.args for call in emit.await_args_list if call.args[1] == "run_retrying"]
    assert [args[2]["attempt"] for args in retry_events] == [1, 2, 3, 4, 5]
    assert [args[2]["delay_seconds"] for args in retry_events] == [15, 30, 60, 120, 240]


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
        plan_snapshot={"steps": [{"id": "plugin:17", "label": "Install Plugin"}]},
        progress_snapshot={
            "steps": [{"id": "plugin:17", "label": "Install Plugin", "status": "running"}],
            "current_step": "plugin:17",
            "message": "Installing Plugin",
            "completed": 0,
            "total": 1,
        },
        progress_updated_at=None,
        error=None,
        created_at=None,
        completed_at=None,
    )
    read_tool = SimpleNamespace(
        id="tool-read",
        run_id=run.id,
        tool_name="search_plugin_market",
        risk="read",
        status="completed",
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
            self.results = [Result([]), Result([run]), Result([read_tool, tool])]

        async def execute(self, _statement):
            return self.results.pop(0)

    tasks = await ai_routes.list_ai_background_tasks(20, DB(), SimpleNamespace(id=8))

    assert len(tasks) == 1
    assert tasks[0].id == run.id
    assert tasks[0].tools[0].tool_name == tool.tool_name
    assert len(tasks[0].tools) == 1
    assert tasks[0].tools[0].progress_snapshot["current_step"] == "plugin:17"
    assert not hasattr(tasks[0].tools[0], "arguments")


def test_managed_plugin_file_unique_key_uses_fixed_size_path_digest():
    ddl = str(CreateTable(ManagedPluginFile.__table__).compile(dialect=mysql.dialect()))

    assert "UNIQUE (managed_plugin_id, path_hash)" in ddl
    assert "UNIQUE (managed_plugin_id, relative_path)" not in ddl
