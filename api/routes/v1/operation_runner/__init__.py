"""Compatibility facade for the versioned operation queue workers.

The public import path remains stable while implementations are grouped by
operation domain. Workers receive only scalar operation data from the queue;
HTTP request objects and request-scoped sessions never cross this boundary.
"""

from services.operation_enqueue import bind_hub_enqueuers

from .cleanup import *  # noqa: F401,F403
from .diagnostics import *  # noqa: F401,F403
from .downloads import *  # noqa: F401,F403
from .game_mode import *  # noqa: F401,F403
from .host import *  # noqa: F401,F403
from .initialized_hosts import *  # noqa: F401,F403
from .maintenance import *  # noqa: F401,F403
from .maintenance import enqueue_plugin_auto_update
from .market import *  # noqa: F401,F403
from .server import *  # noqa: F401,F403
from .server import enqueue_server_operation
from .shared import *  # noqa: F401,F403

bind_hub_enqueuers(
    plugin_auto_update=enqueue_plugin_auto_update,
    server_operation=enqueue_server_operation,
)
