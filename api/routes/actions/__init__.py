"""Compatibility facade and router assembly for actions."""

# ruff: noqa: F401,F403

from importlib import import_module as _import_module

from api.routes._compat import compose_router, install_patch_compatibility

from . import batch as _batch
from . import common as _common
from . import console as _console
from . import deployment as _deployment
from .batch import (
    batch_install_plugins,
    batch_send_command,
    batch_server_actions,
    get_batch_action_status,
)
from .common import *
from .console import (
    game_console_websocket,
    ssh_console_websocket,
)
from .deployment import (
    cancel_deployment,
    check_deployment_lock,
    deployment_status_websocket,
    get_deployment_progress,
    get_server_logs,
    server_action,
)
from .status import (
    get_metamod_status,
    get_ssh_connection_info,
    reconnect_ssh,
    reset_reconnect_counter,
)

_status_routes = _import_module(f"{__name__}.status")

ENDPOINT_ORDER = (
    "deployment_status_websocket",
    "check_deployment_lock",
    "cancel_deployment",
    "server_action",
    "get_deployment_progress",
    "get_server_logs",
    "batch_server_actions",
    "get_batch_action_status",
    "batch_install_plugins",
    "batch_send_command",
    "ssh_console_websocket",
    "game_console_websocket",
    "get_ssh_connection_info",
    "reconnect_ssh",
    "reset_reconnect_counter",
    "get_metamod_status",
)

router = compose_router((_deployment.router, _batch.router, _console.router, _status_routes.router))

install_patch_compatibility(
    __name__,
    (_common, _deployment, _batch, _console, _status_routes),
)
