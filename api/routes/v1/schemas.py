"""Response schemas for the versioned ``/api/v1`` surface.

These models are the stable, browser-facing projections. They deliberately
exclude every secret held on the underlying ORM models (SSH/RCON passwords,
Steam GSLT, API keys). Detail views expose only operational, non-sensitive
fields; secret mutation happens through dedicated, explicit actions.
"""

from __future__ import annotations

from api.contracts.v1.assistant import *  # noqa: F401,F403
from api.contracts.v1.cleanup import *  # noqa: F401,F403
from api.contracts.v1.gamemode import *  # noqa: F401,F403
from api.contracts.v1.identity import *  # noqa: F401,F403
from api.contracts.v1.maps_files import *  # noqa: F401,F403
from api.contracts.v1.operations import *  # noqa: F401,F403
from api.contracts.v1.overview import *  # noqa: F401,F403
from api.contracts.v1.plugins import *  # noqa: F401,F403
from api.contracts.v1.server import *  # noqa: F401,F403
from api.contracts.v1.settings import *  # noqa: F401,F403
from api.contracts.v1.updates import *  # noqa: F401,F403
