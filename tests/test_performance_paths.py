import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.actions import batch as batch_routes
from api.routes.actions import common as action_routes
from api.routes.actions.batch import (
    _get_owned_server_ids,
    batch_server_actions,
    get_batch_action_status,
)
from api.routes.plugin_market import populate_dependency_details
from modules.models import AuthType, MarketPlugin, Server
from modules.schemas import BatchActionRequest
from services.a2s_cache_service import A2SCacheService
from services.deployment_progress import (
    DEPLOYMENT_WS_MAX_PENDING_OUTPUT,
    DeploymentProgressBuffer,
    DeploymentWebSocket,
)
from services.redis_manager import RedisManager, redis_manager
from services.ssh_health_monitor import SSHHealthMonitor
from services.steam_inf_service import SteamInfService
from services.system_info_helper import SystemInfoHelper


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _FakeSession:
    def __init__(self, values):
        self.values = values
        self.execute_count = 0
        self.commit_count = 0
        self.closed = True

    async def __aenter__(self):
        self.closed = False
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def execute(self, statement):
        self.execute_count += 1
        return _ScalarResult(self.values)

    async def commit(self):
        self.commit_count += 1


class _RecordingPipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []

    def rpush(self, key, *values):
        self.commands.append(("rpush", key, values))
        return self

    def ltrim(self, key, start, end):
        self.commands.append(("ltrim", key, start, end))
        return self

    def expire(self, key, ttl):
        self.commands.append(("expire", key, ttl))
        return self

    def hset(self, key, field=None, value=None, *, mapping=None):
        self.commands.append(("hset", key, field, value, mapping))
        return self

    async def execute(self):
        self.client.pipeline_executions += 1
        for command in self.commands:
            if command[0] == "rpush":
                _, key, values = command
                self.client.lists.setdefault(key, []).extend(values)
            elif command[0] == "ltrim":
                _, key, start, end = command
                values = self.client.lists.get(key, [])
                if start < 0:
                    start = max(0, len(values) + start)
                self.client.lists[key] = values[start:] if end == -1 else values[start : end + 1]
            elif command[0] == "hset":
                _, key, field, value, mapping = command
                target = self.client.hashes.setdefault(key, {})
                if mapping is not None:
                    target.update(mapping)
                else:
                    target[str(field)] = value
        return [True] * len(self.commands)


class _FakeRedisClient:
    def __init__(self):
        self.lists = {}
        self.hashes = {}
        self.strings = {}
        self.pipeline_executions = 0
        self.mget_calls = 0

    def pipeline(self, transaction=True):
        return _RecordingPipeline(self)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def eval(self, script, key_count, key, owner_field, user_id, field, data, expire):
        target = self.hashes.get(key, {})
        if target.get(owner_field) != user_id:
            return 0
        target[field] = data
        return 1

    async def scan(self, cursor, match, count):
        prefix = match.removesuffix("*")
        return 0, [key for key in self.strings if key.startswith(prefix)]

    async def mget(self, keys):
        self.mget_calls += 1
        return [self.strings.get(key) for key in keys]


def _manager_with_client(client):
    manager = object.__new__(RedisManager)
    manager.client = client
    manager._coordination_retry_after = 0.0
    return manager


@pytest.mark.asyncio
async def test_plugin_dependency_details_use_one_in_query():
    now = datetime.now()
    plugins = [
        MarketPlugin(
            id=1,
            github_url="https://github.com/acme/one",
            title="One",
            dependencies="2,3",
            created_at=now,
            updated_at=now,
        ),
        MarketPlugin(
            id=4,
            github_url="https://github.com/acme/four",
            title="Four",
            dependencies="3,999",
            created_at=now,
            updated_at=now,
        ),
        MarketPlugin(
            id=5,
            github_url="https://github.com/acme/five",
            title="Five",
            dependencies="invalid",
            created_at=now,
            updated_at=now,
        ),
    ]
    dependencies = [
        MarketPlugin(id=2, github_url="https://github.com/acme/two", title="Two"),
        MarketPlugin(id=3, github_url="https://github.com/acme/three", title="Three"),
    ]
    session = _FakeSession(dependencies)

    responses = await populate_dependency_details(session, plugins)

    assert session.execute_count == 1
    assert [detail.id for detail in responses[0].dependency_details] == [2, 3]
    assert [detail.id for detail in responses[1].dependency_details] == [3]
    assert responses[2].dependency_details is None


@pytest.mark.asyncio
async def test_batch_server_validation_uses_one_select():
    session = _FakeSession([1, 3])

    server_ids = await _get_owned_server_ids(session, [3, 2, 1, 3], user_id=7)

    assert session.execute_count == 1
    assert server_ids == [3, 1]


@pytest.mark.asyncio
async def test_batch_hash_is_initialized_once_and_bound_to_user():
    client = _FakeRedisClient()
    manager = _manager_with_client(client)

    assert await manager.initialize_batch_action("batch", 7, [11, 12], "pending", "Queued")
    assert client.pipeline_executions == 1
    assert set((await manager.get_batch_action_status("batch", user_id=7))) == {"11", "12"}
    assert await manager.get_batch_action_status("batch", user_id=8) == {}

    assert await manager.set_batch_action_status("batch", 11, "success", "Done", user_id=7)
    statuses = await manager.get_batch_action_status("batch", user_id=7)
    assert statuses["11"]["status"] == "success"


@pytest.mark.asyncio
async def test_40_server_batch_uses_one_select_and_one_redis_pipeline(monkeypatch):
    """The largest supported batch must not grow DB or Redis round trips."""

    class DiscardingSupervisor:
        def __init__(self):
            self.task_names = []

        def create(self, coroutine, *, name):
            self.task_names.append(name)
            coroutine.close()
            return None

    server_ids = list(range(1, 41))
    session = _FakeSession(server_ids)
    client = _FakeRedisClient()
    manager = _manager_with_client(client)
    supervisor = DiscardingSupervisor()
    http_resource = SimpleNamespace(
        get=AsyncMock(),
        post=AsyncMock(),
        borrow_client=lambda: None,
        download_file=AsyncMock(),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                task_supervisor=supervisor,
                container=SimpleNamespace(
                    database=SimpleNamespace(session_factory=lambda: None),
                    ssh_pool=object(),
                    http=http_resource,
                ),
            )
        )
    )
    current_user = SimpleNamespace(id=7, is_admin=False)
    batch_routes._pending_batch_counts.clear()
    monkeypatch.setattr(batch_routes, "redis_manager", manager)

    try:
        response = await batch_server_actions(
            BatchActionRequest(server_ids=server_ids, action="stop"),
            request,
            db=session,
            current_user=current_user,
        )
    finally:
        batch_routes._pending_batch_counts.clear()

    assert response.server_count == 40
    assert session.execute_count == 1
    assert session.commit_count == 1
    assert client.pipeline_executions == 1
    assert len(next(iter(client.hashes.values()))) == 41  # 40 targets plus the owner
    assert len(supervisor.task_names) == 40


@pytest.mark.asyncio
async def test_legacy_batch_status_uses_mget_compatibility_read():
    client = _FakeRedisClient()
    client.strings = {
        "batch_action:legacy:1": json.dumps({"status": "success"}),
        "batch_action:legacy:2": json.dumps({"status": "pending"}),
    }
    manager = _manager_with_client(client)

    statuses = await manager.get_batch_action_status("legacy")

    assert statuses["1"]["status"] == "success"
    assert statuses["2"]["status"] == "pending"
    assert client.mget_calls == 1


@pytest.mark.asyncio
async def test_legacy_batch_status_requires_database_ownership(monkeypatch):
    current_status = AsyncMock(return_value={})
    legacy_status = AsyncMock(return_value={"1": {"status": "success"}, "3": {"status": "pending"}})
    monkeypatch.setattr(redis_manager, "get_batch_action_status", current_status)
    monkeypatch.setattr(redis_manager, "get_legacy_batch_action_status", legacy_status)
    session = _FakeSession([1, 3])

    response = await get_batch_action_status(
        "legacy",
        db=session,
        current_user=SimpleNamespace(id=7),
    )

    assert set(response["servers"]) == {"1", "3"}
    assert session.execute_count == 1
    current_status.assert_awaited_once_with("legacy", user_id=7)


@pytest.mark.asyncio
async def test_deployment_progress_pipeline_caps_list_at_1000():
    client = _FakeRedisClient()
    manager = _manager_with_client(client)
    entries = [
        {"type": "output", "message": str(index), "timestamp": "now"} for index in range(1100)
    ]

    assert await manager.append_deployment_progress_batch(9, entries)

    stored = client.lists["deployment_progress:9"]
    assert client.pipeline_executions == 1
    assert len(stored) == 1000
    assert json.loads(stored[0])["message"] == "100"


@pytest.mark.asyncio
async def test_output_buffer_batches_10000_lines_and_flushes_complete(monkeypatch):
    append_batch = AsyncMock(return_value=True)
    monkeypatch.setattr(redis_manager, "append_deployment_progress_batch", append_batch)
    buffer = DeploymentProgressBuffer(flush_interval=3600, max_batch_bytes=32 * 1024)

    for index in range(10_000):
        await buffer.append(
            9,
            {"type": "output", "message": f"{index:05d}-" + "x" * 94, "timestamp": "now"},
        )
    await buffer.append(9, {"type": "complete", "message": "Done", "timestamp": "now"})
    await buffer.close()

    persisted = [entry for call in append_batch.await_args_list for entry in call.args[1]]
    assert len(append_batch.await_args_list) < 300
    assert len(persisted) == 10_001
    assert persisted[-1]["type"] == "complete"


@pytest.mark.asyncio
async def test_slow_websocket_drops_only_output_and_never_blocks_critical_messages():
    release_sender = asyncio.Event()

    class SlowWebSocket:
        def __init__(self):
            self.messages = []

        async def accept(self):
            return None

        async def send_json(self, message):
            await release_sender.wait()
            self.messages.append(message)

    websocket = SlowWebSocket()
    manager = DeploymentWebSocket()
    await manager.connect(websocket, 9)  # type: ignore[arg-type]

    await manager.send_message(9, {"type": "output", "message": "first"})
    await asyncio.sleep(0)
    for index in range(DEPLOYMENT_WS_MAX_PENDING_OUTPUT * 3):
        await manager.send_message(9, {"type": "output", "message": str(index)})
    await manager.send_message(9, {"type": "status", "message": "finishing"})
    await manager.send_message(9, {"type": "error", "message": "diagnostic"})
    await manager.send_message(9, {"type": "complete", "message": "done"})

    sender = manager._senders[websocket]  # noqa: SLF001
    assert sender.output_count <= DEPLOYMENT_WS_MAX_PENDING_OUTPUT
    assert [item["type"] for item in sender.queue] == ["status", "error", "complete"]

    release_sender.set()
    async with asyncio.timeout(1):
        while not websocket.messages or websocket.messages[-1]["type"] != "complete":
            await asyncio.sleep(0)
    delivered_types = [item["type"] for item in websocket.messages]
    assert delivered_types[-3:] == ["status", "error", "complete"]
    assert delivered_types.count("output") < DEPLOYMENT_WS_MAX_PENDING_OUTPUT * 3

    manager.disconnect(websocket, 9)  # type: ignore[arg-type]


async def _assert_bounded(work, limit: int, item_count: int = 12):
    active = 0
    maximum = 0

    async def tracked(item):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.005)
        active -= 1
        return await work(item)

    return tracked, lambda: maximum


@pytest.mark.asyncio
async def test_100_target_a2s_sweep_is_bounded_to_16_and_finishes_under_deadline(
    monkeypatch,
):
    servers = [
        SimpleNamespace(id=index, should_skip_background_checks=lambda: False)
        for index in range(100)
    ]
    session = _FakeSession(servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: session)
    service = A2SCacheService(max_query_concurrency=16)
    first_wave_ready = asyncio.Event()
    active = 0
    maximum = 0
    completed = []

    async def tracked_query(server):
        nonlocal active, maximum
        assert session.closed is True
        active += 1
        maximum = max(maximum, active)
        if active == service.max_query_concurrency:
            first_wave_ready.set()
        try:
            await first_wave_ready.wait()
            await asyncio.sleep(0)
            completed.append(server.id)
        finally:
            active -= 1

    monkeypatch.setattr(service, "_query_and_cache_server", tracked_query)

    # A deterministic synthetic sweep completes far inside the production
    # 9.5-second budget while still forcing a full 16-target first wave.
    async with asyncio.timeout(1):
        await service._query_all_servers()

    assert service.sweep_deadline < 10
    assert maximum == 16
    assert sorted(completed) == list(range(100))
    assert session.execute_count == 1


@pytest.mark.asyncio
async def test_100_target_a2s_sweep_enforces_its_total_deadline(monkeypatch):
    servers = [
        SimpleNamespace(id=index, should_skip_background_checks=lambda: False)
        for index in range(100)
    ]
    session = _FakeSession(servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: session)
    service = A2SCacheService(
        max_query_concurrency=16,
        server_deadline=0.05,
        sweep_deadline=0.1,
    )
    never_finishes = asyncio.Event()
    active = 0
    maximum = 0
    started = []

    async def blocked_query(server):
        nonlocal active, maximum
        assert session.closed is True
        active += 1
        maximum = max(maximum, active)
        started.append(server.id)
        try:
            await never_finishes.wait()
        finally:
            active -= 1

    monkeypatch.setattr(service, "_query_and_cache_server", blocked_query)
    monkeypatch.setattr(service, "_cache_query_error", AsyncMock())

    # The outer timeout is a regression guard: if the service loses its own
    # total deadline, this test fails instead of hanging the CI worker.
    async with asyncio.timeout(1):
        await service._query_all_servers()

    assert maximum == 16
    assert 16 <= len(started) < 100
    assert active == 0
    assert session.execute_count == 1


@pytest.mark.asyncio
async def test_30_slow_remote_commands_release_db_checkouts_before_io(monkeypatch):
    """A five-connection pool must admit all 30 slow SSH operations."""

    class PoolState:
        def __init__(self):
            self.available = asyncio.Semaphore(5)
            self.sessions = []
            self.pool_timeouts = 0
            self.checkout_durations = []
            self.remote_active = 0
            self.max_remote_active = 0
            self.all_remote_started = asyncio.Event()

        def session(self):
            session = PooledSession(self)
            self.sessions.append(session)
            return session

    class PooledSession:
        def __init__(self, pool):
            self.pool = pool
            self.closed = True
            self.checkout_started = 0.0

        async def __aenter__(self):
            try:
                async with asyncio.timeout(0.5):
                    await self.pool.available.acquire()
            except TimeoutError:
                self.pool.pool_timeouts += 1
                raise
            self.closed = False
            self.checkout_started = asyncio.get_running_loop().time()
            return self

        async def __aexit__(self, *_args):
            self.pool.checkout_durations.append(
                asyncio.get_running_loop().time() - self.checkout_started
            )
            self.closed = True
            self.pool.available.release()

        async def get(self, _model, server_id):
            return Server(
                id=server_id,
                user_id=7,
                name=f"server-{server_id}",
                host=f"server-{server_id}.example.com",
                ssh_user="cs2",
                auth_type=AuthType.PASSWORD,
                session_manager="tmux",
            )

    pool = PoolState()

    class SlowSSHManager:
        async def connect(self, server):
            assert isinstance(server, Server)
            assert all(session.closed for session in pool.sessions)
            pool.remote_active += 1
            pool.max_remote_active = max(pool.max_remote_active, pool.remote_active)
            if pool.remote_active == 30:
                pool.all_remote_started.set()
            try:
                await pool.all_remote_started.wait()
                await asyncio.sleep(0.01)
                return True, "connected"
            finally:
                pool.remote_active -= 1

        async def execute_command(self, _command, timeout=10):
            return True, "", ""

        async def disconnect(self):
            return None

    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "modules.database.async_session_maker",
        lambda: (_ for _ in ()).throw(AssertionError("global DB factory was used")),
    )
    monkeypatch.setattr(action_routes, "SSHManager", SlowSSHManager)
    monkeypatch.setattr(
        action_routes,
        "find_running_session_manager",
        AsyncMock(return_value="tmux"),
    )
    monkeypatch.setattr(action_routes.redis_manager, "set_batch_action_status", set_status)

    async with asyncio.timeout(2):
        await asyncio.gather(
            *(
                action_routes.execute_single_server_command(
                    server_id,
                    "status",
                    user_id=7,
                    is_admin=False,
                    batch_id="performance-acceptance",
                    session_factory=pool.session,
                )
                for server_id in range(1, 31)
            )
        )

    p95_checkout_seconds = sorted(pool.checkout_durations)[28]
    assert pool.pool_timeouts == 0
    assert pool.max_remote_active == 30
    assert len(pool.checkout_durations) == 30
    assert p95_checkout_seconds < 0.1


@pytest.mark.asyncio
async def test_a2s_sweep_is_single_flight(monkeypatch):
    server = SimpleNamespace(id=1, should_skip_background_checks=lambda: False)
    session = _FakeSession([server])
    monkeypatch.setattr("modules.database.async_session_maker", lambda: session)
    service = A2SCacheService()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def blocked_query(_server):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    monkeypatch.setattr(service, "_query_and_cache_server", blocked_query)
    first = asyncio.create_task(service._query_all_servers())
    await started.wait()

    await service._query_all_servers()
    assert calls == 1
    assert session.execute_count == 1

    release.set()
    await first


@pytest.mark.asyncio
async def test_a2s_per_server_deadline_finishes_every_target(monkeypatch):
    servers = [
        SimpleNamespace(id=index, should_skip_background_checks=lambda: False) for index in range(8)
    ]
    session = _FakeSession(servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: session)
    service = A2SCacheService(
        max_query_concurrency=2,
        server_deadline=0.01,
        sweep_deadline=0.25,
    )
    started: list[int] = []

    async def blocked_query(server):
        started.append(server.id)
        await asyncio.Event().wait()

    cache_error = AsyncMock()
    monkeypatch.setattr(service, "_query_and_cache_server", blocked_query)
    monkeypatch.setattr(service, "_cache_query_error", cache_error)

    await service._query_all_servers()

    assert started == list(range(8))
    assert {call.args[0] for call in cache_error.await_args_list} == set(range(8))


@pytest.mark.asyncio
async def test_a2s_cache_aggregation_uses_one_mget(monkeypatch):
    mget = AsyncMock(
        return_value=[
            {"success": True},
            json.dumps({"success": False, "error": "offline"}),
        ]
    )
    monkeypatch.setattr(redis_manager, "mget", mget)

    cached = await A2SCacheService().get_cached_info_many([3, 7, 3])

    mget.assert_awaited_once_with(["a2s:server:3", "a2s:server:7"])
    assert cached == {
        3: {"success": True},
        7: {"success": False, "error": "offline"},
    }


@pytest.mark.asyncio
async def test_disk_sweep_is_bounded_to_four(monkeypatch):
    helper = SystemInfoHelper(max_disk_concurrency=4)

    async def return_none(server):
        return None

    tracked, maximum = await _assert_bounded(return_none, 4)

    async def get_disk_space(server, force_refresh=False):
        return await tracked(server)

    monkeypatch.setattr(helper, "get_disk_space", get_disk_space)
    servers = [SimpleNamespace(id=index) for index in range(12)]

    await helper.get_all_servers_disk_space(servers)

    assert maximum() == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "method_name", "concurrency"),
    [
        (SteamInfService(max_refresh_concurrency=4), "_refresh_server_with_timeout", 4),
        (SSHHealthMonitor(max_health_concurrency=8), "_check_server_health", 8),
    ],
)
async def test_ssh_background_sweeps_are_bounded(monkeypatch, service, method_name, concurrency):
    servers = [SimpleNamespace(id=index) for index in range(16)]
    session = _FakeSession(servers)
    monkeypatch.setattr("modules.database.async_session_maker", lambda: session)

    async def assert_session_closed(server):
        assert session.closed is True

    tracked, maximum = await _assert_bounded(assert_session_closed, concurrency)
    monkeypatch.setattr(service, method_name, tracked)

    if isinstance(service, SteamInfService):
        await service._periodic_refresh_all()
    else:
        await service._check_all_servers()

    assert maximum() == concurrency
