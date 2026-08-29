"""Versioned ``/api/v1`` surface for the Next.js console.

This package is the forward-looking, browser-facing HTTP contract. It returns
non-secret projections (see :mod:`api.routes.v1.schemas`) and reuses the shared
authorization dependencies so ownership and the legacy 404 policy are preserved.
Legacy ``/api/*`` routes remain untouched for existing clients.
"""

from fastapi import APIRouter

from . import auth, overview, servers

router = APIRouter()
router.include_router(auth.router)
router.include_router(servers.router)
router.include_router(overview.router)

__all__ = ["router"]
