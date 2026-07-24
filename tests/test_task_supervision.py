"""Request-created background work belongs to the application lifecycle."""

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from api.routes.actions import batch
from api.routes.actions.common import _spawn_action_task
from api.routes.plugin_auto_update import _spawn_plugin_update_task
from cs2_manager.runtime import TaskSupervisor
from services.deployment_progress import DeploymentWebSocket


def _request(supervisor: TaskSupervisor):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(task_supervisor=supervisor)))


@pytest.mark.asyncio
async def test_action_and_plugin_tasks_are_owned_by_app_supervisor():
    supervisor = TaskSupervisor("request-tasks")
    completed: list[str] = []

    async def work(name: str) -> None:
        await asyncio.sleep(0)
        completed.append(name)

    action_task = _spawn_action_task(
        _request(supervisor),
        work("action"),
        name="action-task",
    )
    plugin_task = _spawn_plugin_update_task(
        _request(supervisor),
        work("plugin"),
        name="plugin-task",
    )

    assert {task.get_name() for task in supervisor.tasks} == {
        "action-task",
        "plugin-task",
    }
    await asyncio.gather(action_task, plugin_task)
    await asyncio.sleep(0)
    assert completed == ["action", "plugin"]
    assert supervisor.tasks == set()


@pytest.mark.asyncio
async def test_deployment_websocket_sender_is_owned_and_removed():
    supervisor = TaskSupervisor("websocket-tasks")

    class WebSocket:
        async def accept(self) -> None:
            return None

        async def send_json(self, _message) -> None:
            return None

    websocket = WebSocket()
    manager = DeploymentWebSocket()
    await manager.connect(  # type: ignore[arg-type]
        websocket,
        42,
        task_supervisor=supervisor,
    )

    sender = manager._senders[websocket]  # type: ignore[index]  # noqa: SLF001
    assert sender.task in supervisor.tasks
    assert sender.task is not None
    assert sender.task.get_name() == "deployment-ws-42"

    manager.disconnect(websocket, 42)  # type: ignore[arg-type]
    await asyncio.gather(sender.task, return_exceptions=True)
    await asyncio.sleep(0)

    assert websocket not in manager._senders  # type: ignore[operator]  # noqa: SLF001
    assert 42 not in manager.active_connections
    assert supervisor.tasks == set()


def test_batch_routes_do_not_create_unowned_asyncio_tasks():
    assert "asyncio.create_task" not in inspect.getsource(batch)
