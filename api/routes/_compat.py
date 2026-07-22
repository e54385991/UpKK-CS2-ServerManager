"""Small helpers for compatibility-preserving router packages."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

from fastapi import APIRouter


def compose_router(
    routers: tuple[APIRouter, ...],
    endpoint_order: tuple[str, ...],
) -> APIRouter:
    """Combine domain routers while retaining the legacy registration order."""
    order = {name: index for index, name in enumerate(endpoint_order)}
    routes = [route for router in routers for route in router.routes]
    unknown = [route.name for route in routes if route.name not in order]
    if unknown:
        raise RuntimeError(f"Router contains endpoints missing from its order: {unknown}")
    routes.sort(key=lambda route: order[route.name])

    combined = APIRouter()
    combined.routes.extend(routes)
    return combined


def install_patch_compatibility(
    module_name: str,
    targets: tuple[ModuleType, ...],
) -> None:
    """Propagate assignments on a legacy facade to defining submodules.

    Existing integrations and tests patch symbols such as
    ``api.routes.actions.SSHManager``.  Endpoint functions now live in domain
    modules, so facade assignments must remain visible in those globals.
    """

    module = sys.modules[module_name]

    class CompatibilityModule(type(module)):
        def __setattr__(self, name: str, value: Any) -> None:
            super().__setattr__(name, value)
            if name.startswith("__"):
                return
            for target in targets:
                if name in target.__dict__:
                    setattr(target, name, value)

    module.__class__ = CompatibilityModule
