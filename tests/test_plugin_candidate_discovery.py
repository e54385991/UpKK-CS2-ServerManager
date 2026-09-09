"""Discovery precision, bounded I/O and progress contracts without live services."""

import asyncio
import base64
import json
from collections import Counter
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from modules.models import PluginImportJob
from modules.plugin_ai import (
    DiscoveryProgress,
    GitHubVerification,
    ImportEvent,
    ImportOptions,
    RepositoryAnalysis,
)
from services.ai_provider import AIProviderError
from services.ai_security import AIProviderConfig
from services.plugins import ai_import_runner as runner
from services.plugins import ai_import_store as store
from services.plugins.ai_candidate_screening import (
    BATCH_SIZE,
    PLUGIN_SCOPE,
    ScreeningFormatError,
    parse_decisions,
    screen_candidates,
)
from services.plugins.github_ai_client import GitHubAIClient, GitHubRateLimitError

SHA = "a" * 40


def repo(name, description="CounterStrikeSharp server plugin"):
    return {
        "html_url": f"https://github.com/example/{name}",
        "full_name": name,
        "description": description,
        "default_branch": "main",
        "topics": [],
        "stargazers_count": 1,
        "owner": {"login": "example"},
    }


def response(*verdicts):
    return json.dumps(
        {
            "decisions": [
                {"id": i, "verdict": value, "reason": "Repository identity"}
                for i, value in enumerate(verdicts)
            ]
        }
    )


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(store, "check_job", AsyncMock())
    monkeypatch.setattr(store, "update_job", AsyncMock())
    monkeypatch.setattr(store, "existing_repositories", AsyncMock(return_value={}))
    monkeypatch.setattr(
        GitHubAIClient,
        "verify",
        AsyncMock(
            return_value=GitHubVerification(valid=True, core_remaining=1000, search_remaining=30)
        ),
    )
    job = store.snapshot(
        PluginImportJob(
            actor_user_id=1,
            request_key="test",
            command="import",
            options=ImportOptions(expand_search=False, max_plugins=4).model_dump(),
        )
    )
    config = AIProviderConfig(
        base_url="https://example.com/v1",
        api_key="test",
        model="test",
        timeout_seconds=10,
        allowlist=[],
        source="global",
    )
    return job, config


async def test_mixed_100_candidates_only_analyzes_four_plugins(environment, monkeypatch):
    job, config = environment
    rows = [repo(f"existing{i}") for i in range(80)]
    rows += [repo(f"unrelated{i}", "Management panel or plugin list") for i in range(16)]
    rows += [repo(f"plugin{i}") for i in range(4)]
    store.existing_repositories.return_value = {
        row["html_url"]: i + 1 for i, row in enumerate(rows[:80])
    }
    order = []

    async def search(_options, term, page):
        order.append(("search", page))
        return rows[(page - 1) * 50 : page * 50]

    async def complete(_config, messages, **kwargs):
        evidence = json.loads(messages[1]["content"])["repositories"]
        assert len(evidence) == BATCH_SIZE
        assert not any("existing" in item["name"] for item in evidence)
        return {"content": response(*["irrelevant"] * 16, *["eligible"] * 4)}

    async def insert(*args):
        order.append(("insert", args[1]))
        return store.PluginInsertResult(100 + len(order), True)

    monkeypatch.setattr(GitHubAIClient, "search", AsyncMock(side_effect=search))
    metadata = AsyncMock(side_effect=AssertionError("Search metadata must be reused"))
    monkeypatch.setattr(GitHubAIClient, "repository", metadata)
    docs = AsyncMock(return_value=([{"path": "README.md", "text": "Plugin installation"}], []))
    monkeypatch.setattr(GitHubAIClient, "documents", docs)
    readme = AsyncMock(side_effect=AssertionError("Clear identities need no screening README"))
    monkeypatch.setattr(GitHubAIClient, "readme", readme)
    release = AsyncMock(return_value=None)
    monkeypatch.setattr(GitHubAIClient, "release", release)
    archives = AsyncMock(return_value=([], []))
    monkeypatch.setattr(runner.archive_analysis, "inspect_archives", archives)
    analysis = AsyncMock(
        return_value=RepositoryAnalysis(
            is_plugin=True,
            title="Plugin",
            description="CS2 plugin",
            category="utility",
            framework="counterstrikesharp",
        )
    )
    monkeypatch.setattr(runner.ImportRunner, "analyze", analysis)
    monkeypatch.setattr(runner, "create_chat_completion", AsyncMock(side_effect=complete))
    monkeypatch.setattr(store, "insert_plugin", AsyncMock(side_effect=insert))
    instance = runner.ImportRunner(job, "token", config)
    await instance.run()
    assert metadata.await_count == readme.await_count == 0
    assert (
        docs.await_count == release.await_count == archives.await_count == analysis.await_count == 4
    )
    assert runner.create_chat_completion.await_count == 1
    assert store.existing_repositories.await_count == 3  # startup + before/after screening
    assert GitHubAIClient.search.await_count == 6  # one query/page per framework per round
    assert instance.discovery.stats == DiscoveryProgress(
        discovered=100, existing=80, irrelevant=16, eligible=4, deep_analyzed=4
    )
    assert instance.analyzed == 4
    assert store.update_job.call_args.kwargs["status"] == "completed"


async def test_imports_first_round_before_searching_next_query(environment, monkeypatch):
    job, config = environment
    job = replace(
        job,
        options=ImportOptions(framework="counterstrikesharp", expand_search=False, max_plugins=1),
    )
    search = AsyncMock(return_value=[repo("plugin")])
    monkeypatch.setattr(GitHubAIClient, "search", search)
    monkeypatch.setattr(
        runner, "create_chat_completion", AsyncMock(return_value={"content": response("eligible")})
    )

    async def visit(self, url):
        self.imported_roots += 1
        return 42

    monkeypatch.setattr(runner.ImportRunner, "visit", visit)
    instance = runner.ImportRunner(job, "token", config)
    await instance.run()
    assert search.await_count == 1
    assert instance.imported_roots == 1


async def test_concurrent_marketplace_addition_skips_deep_analysis(environment, monkeypatch):
    job, config = environment
    row = repo("plugin")
    store.existing_repositories.side_effect = [{}, {}, {row["html_url"]: 42}]
    monkeypatch.setattr(GitHubAIClient, "search", AsyncMock(return_value=[row]))
    monkeypatch.setattr(
        runner, "create_chat_completion", AsyncMock(return_value={"content": response("eligible")})
    )
    visit = AsyncMock()
    monkeypatch.setattr(runner.ImportRunner, "visit", visit)
    instance = runner.ImportRunner(job, "token", config)
    # Later rounds repeat the same URL and must not refresh or screen it again.
    await instance.run()
    visit.assert_not_awaited()
    assert instance.discovery.stats.existing == 1
    assert instance.discovery.stats.discovered == 1
    assert store.update_job.call_args.kwargs["message"].startswith("No new plugins")


async def test_url_variants_and_existing_dependencies_reuse_index(environment):
    job, config = environment
    store.existing_repositories.return_value = {"https://github.com/example/known": 17}
    instance = runner.ImportRunner(job, "token", config)
    try:
        assert await instance.visit("https://GitHub.com/EXAMPLE/KNOWN.git/", depth=1) == 17
        assert await instance.visit("https://github.com/example/known", depth=1) == 17
        assert instance.analyzed == 0
        assert store.existing_repositories.await_count == 1
        for url in ["https://GitHub.com/Example/Known.git/", "https://github.com/example/known"]:
            assert instance.discovery.collect({"html_url": url}) is None
        assert instance.discovery.stats.discovered == instance.discovery.stats.existing == 1
    finally:
        await instance.client.close()


# Concise identity fixtures based on the four supplied repositories, not a URL allowlist.
POSITIVE = [
    (
        "oqyh/cs2-Auto-Restart-Server-GoldKingZ",
        "Restarts CS2 servers after empty-server, uptime or schedule triggers",
    ),
    (
        "oqyh/cs2-Auto-Delete-GoldKingZ",
        "CounterStrikeSharp plugin for configured file and folder cleanup",
    ),
    (
        "Next-il/PanoramaManager",
        "Dedicated CounterStrikeSharp library connecting server logic with Panorama HUDs",
    ),
    (
        "Frostline-se/Frostline-Paintball-CS2",
        "CounterStrikeSharp plugin for colored bullet-impact paint decals",
    ),
]


async def test_example_plugins_low_stars_and_dedicated_library_survive_screening():
    rows = [{**repo(name, description), "full_name": name} for name, description in POSITIVE]
    client = AsyncMock()
    complete = AsyncMock(return_value=response(*["eligible"] * 4))
    decisions = await screen_candidates(rows, client, complete, "all")
    assert [item.verdict for item in decisions] == ["eligible"] * 4
    evidence = json.loads(complete.call_args.args[0][1]["content"])["repositories"]
    assert [item["name"] for item in evidence] == [name for name, _ in POSITIVE]
    assert "libraries that cannot run independently" in PLUGIN_SCOPE
    for word in [
        "tutorials",
        "templates",
        "plugin lists",
        "generic SDKs",
        "management panels",
        "configuration",
    ]:
        assert word in PLUGIN_SCOPE
    client.readme.assert_not_awaited()


async def test_uncertain_identity_reads_readme_once_and_reuses_commit():
    requests = []

    def handle(request):
        requests.append(request)
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": SHA})
        if "/readme" in request.url.path:
            assert request.url.params["ref"] == SHA
            return httpx.Response(
                200,
                json={
                    "path": "README.md",
                    "encoding": "base64",
                    "content": base64.b64encode(b"Dedicated CS2 plugin library").decode(),
                },
            )
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json={"tree": [{"path": "README.md", "type": "blob"}]})
        raise AssertionError(f"Must not probe absent directories: {request.url.path}")

    client = GitHubAIClient("test", interval=0, transport=httpx.MockTransport(handle))
    complete = AsyncMock(side_effect=[response("uncertain"), response("eligible")])
    try:
        assert (await screen_candidates([repo("unknown", "")], client, complete, "all"))[
            0
        ].verdict == "eligible"
        docs, sources = await client.documents(repo("unknown"))
        assert docs[0]["text"] == "Dedicated CS2 plugin library"
        assert sources[0].commit == SHA
        counts = Counter(request.url.path for request in requests)
        assert len(requests) == 3 and all(count == 1 for count in counts.values())
        second = json.loads(complete.call_args.args[0][1]["content"])
        assert second["repositories"][0]["readme"] == docs[0]["text"]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "content",
    ["invalid", "[]", '{"decisions":null}', '{"decisions":[{"id":0,"verdict":"eligible"}]}'],
)
async def test_invalid_screening_twice_is_failure_not_empty_success(content):
    client = AsyncMock()
    client.readme.return_value = (SHA, [], [])
    complete = AsyncMock(return_value=content)
    with pytest.raises(ScreeningFormatError):
        await screen_candidates([repo("unknown")], client, complete, "all")
    assert complete.await_count == 2


def test_screening_ignores_invented_ids_urls_and_duplicate_decisions():
    values = [
        {"id": key, "verdict": "eligible", "reason": "Declared plugin"} for key in [0, 0, 4, 99]
    ]
    values.append({"id": 1, "verdict": "eligible", "reason": "x", "url": "https://evil.test"})
    assert set(parse_decisions(json.dumps({"decisions": values}), {0, 1, 4})) == {4}
    assert parse_decisions(response("eligible"), {999}) == {}


async def test_missing_decisions_get_readme_and_remain_uncertain_without_blacklist():
    client = AsyncMock()
    client.readme.return_value = (SHA, [], [])
    complete = AsyncMock(
        side_effect=[
            response("irrelevant"),
            '{"decisions":[{"id":1,"verdict":"uncertain","reason":"No clear identity"}]}',
        ]
    )
    decisions = await screen_candidates([repo("list"), repo("unknown")], client, complete, "all")
    assert [item.verdict for item in decisions] == ["irrelevant", "uncertain"]
    assert client.readme.await_count == 1


@pytest.mark.parametrize(
    "error",
    [AIProviderError("Unavailable"), asyncio.CancelledError(), GitHubRateLimitError("Limited")],
)
async def test_screening_propagates_provider_failure_cancellation_and_rate_limits(error):
    client = AsyncMock()
    complete = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await screen_candidates([repo("plugin")], client, complete, "all")
    client.readme.assert_not_awaited()


async def test_search_timeout_keeps_collected_candidates(environment, monkeypatch):
    job, config = environment
    instance = runner.ImportRunner(job, "token", config)
    search = AsyncMock(side_effect=[[repo("first")], TimeoutError()])
    monkeypatch.setattr(GitHubAIClient, "search", search)
    try:
        batches = [batch async for batch in instance.candidates()]
        assert batches == [[repo("first")["html_url"]]]
        assert instance.discovery.search_exhausted
    finally:
        await instance.client.close()


def test_progress_is_optional_in_older_events_and_persists_in_snapshots():
    old = ImportEvent(sequence=1, phase="searching", message="Searching")
    assert old.discovery is None
    job = PluginImportJob(actor_user_id=1, request_key="test", command="test", options={})
    stats = DiscoveryProgress(discovered=100, existing=80, irrelevant=16, eligible=4)
    store.append_event(job, "filtering", "Screened", discovery=stats)
    assert store.snapshot(job).events[-1].discovery == stats
    with pytest.raises(ValueError):
        DiscoveryProgress(discovered=-1)


async def test_oversized_batch_is_rejected_without_io():
    client, complete = AsyncMock(), AsyncMock()
    with pytest.raises(ValueError):
        await screen_candidates([repo("x")] * 21, client, complete, "all")
    complete.assert_not_awaited()


async def test_zero_search_budget_and_invalid_urls_do_not_start_requests(environment):
    job, config = environment
    instance = runner.ImportRunner(job, "token", config)
    try:
        instance.discovery.search_remaining = 0
        assert await instance.discovery.search_page("test", 1) == []
        for row in [{}, {"html_url": "https://evil.test/a/b"}]:
            assert instance.discovery.collect(row) is None
    finally:
        await instance.client.close()


async def test_concurrent_insert_reuses_id_without_consuming_import_quota(environment, monkeypatch):
    job, config = environment
    row = repo("raced")
    monkeypatch.setattr(GitHubAIClient, "documents", AsyncMock(return_value=([], [])))
    monkeypatch.setattr(GitHubAIClient, "release", AsyncMock(return_value=None))
    monkeypatch.setattr(
        runner.archive_analysis, "inspect_archives", AsyncMock(return_value=([], []))
    )
    monkeypatch.setattr(
        runner.ImportRunner,
        "analyze",
        AsyncMock(
            return_value=RepositoryAnalysis(
                is_plugin=True,
                title="Plugin",
                description="CS2 server plugin",
                framework="counterstrikesharp",
                category="utility",
            )
        ),
    )
    monkeypatch.setattr(
        store, "insert_plugin", AsyncMock(return_value=store.PluginInsertResult(42, False))
    )
    instance = runner.ImportRunner(job, "token", config)
    try:
        instance.discovery.collect(row)
        assert await instance.visit(row["html_url"]) == 42
        assert instance.imported_roots == 0
        assert instance.discovery.stats.existing == 1
    finally:
        await instance.client.close()


async def test_bad_screening_is_reported_as_failed_job(environment, monkeypatch):
    job, config = environment
    monkeypatch.setattr(store, "credentials", AsyncMock(return_value=("token", config)))
    monkeypatch.setattr(runner.ImportRunner, "run", AsyncMock(side_effect=ScreeningFormatError()))
    await runner.run_job(job)
    assert store.update_job.call_args.kwargs["status"] == "failed"
    assert store.update_job.call_args.kwargs["reason"] == "ai_screening_format"
