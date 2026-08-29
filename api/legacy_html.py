"""Send leftover Jinja console pages to the Next.js origin.

Public traffic is Caddy → Next. Hitting FastAPI directly used to serve the old
HTML console. The default is a 307 to ``CONSOLE_PUBLIC_URL``. Set
``LEGACY_HTML_CONSOLE=serve`` only when you still need the Jinja UI on this
listener. ``gone`` returns 404. ``/api`` and ``/api/v1`` are unchanged.
``/google-callback`` stays on FastAPI for direct ``:8000`` and
``LEGACY_HTML_CONSOLE=serve``. The public Caddy → Next origin serves the
Next.js callback page so Google can return to the console root.

Auth-gated Jinja routes declare ``WebUser`` / ``WebAdmin``, which 303 to
``/login`` *before* the route body can redirect. Middleware applies the Next
mapping first so leftover bookmarks never land on the FastAPI login page.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import parse_qs, urlencode

from fastapi import Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from modules import settings

LEGACY_HTML_MODES = frozenset({"redirect", "serve", "gone"})
GONE_DETAIL = "The HTML console has moved to the Next.js origin"

_RedirectBuilder = Callable[[re.Match[str], str], str]


def legacy_html_mode() -> str:
    mode = (settings.LEGACY_HTML_CONSOLE or "redirect").strip().lower()
    return mode if mode in LEGACY_HTML_MODES else "redirect"


def console_public_url(path: str, query: str = "") -> str:
    base = (settings.CONSOLE_PUBLIC_URL or "http://127.0.0.1:3000").rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    target = f"{base}{path}"
    if query:
        target = f"{target}?{query}"
    return target


def _files_popup_target(match: re.Match[str], query: str) -> str:
    file_path = (parse_qs(query).get("file_path") or [""])[0]
    path = f"/servers/{match.group(1)}/files"
    if file_path:
        return f"{path}?{urlencode({'path': file_path})}"
    return path


_HTML_REDIRECTS: tuple[tuple[re.Pattern[str], _RedirectBuilder], ...] = (
    (re.compile(r"^/$"), lambda _match, _query: "/overview"),
    (re.compile(r"^/deployment-tutorial$"), lambda _match, _query: "/deployment-tutorial"),
    (re.compile(r"^/login$"), lambda _match, _query: "/login"),
    (re.compile(r"^/register$"), lambda _match, _query: "/register"),
    (re.compile(r"^/forgot-password$"), lambda _match, _query: "/forgot-password"),
    (re.compile(r"^/reset-password$"), lambda _match, _query: "/reset-password"),
    (re.compile(r"^/servers-ui$"), lambda _match, _query: "/servers"),
    (re.compile(r"^/servers-ui/(\d+)$"), lambda match, _query: f"/servers/{match.group(1)}"),
    (re.compile(r"^/plugin-market$"), lambda _match, _query: "/plugins"),
    (re.compile(r"^/setup-wizard$"), lambda _match, _query: "/servers/new"),
    (re.compile(r"^/profile$"), lambda _match, _query: "/settings/profile"),
    (re.compile(r"^/system-settings$"), lambda _match, _query: "/settings"),
    (re.compile(r"^/audit-logs$"), lambda _match, _query: "/audit"),
    (
        re.compile(r"^/servers/(\d+)/console-popup/[^/]+$"),
        lambda match, _query: f"/live-console/{match.group(1)}",
    ),
    (
        re.compile(r"^/servers/(\d+)/ssh-console$"),
        lambda match, _query: f"/servers/{match.group(1)}/console",
    ),
    (
        re.compile(r"^/servers/(\d+)/game-console$"),
        lambda match, _query: f"/servers/{match.group(1)}/console",
    ),
    (re.compile(r"^/servers/(\d+)/file-editor-popup$"), _files_popup_target),
)


def resolve_legacy_html_target(path: str, query: str = "") -> str | None:
    """Map a leftover Jinja path to the Next.js equivalent, or ``None``."""
    for pattern, builder in _HTML_REDIRECTS:
        match = pattern.fullmatch(path)
        if match is not None:
            return builder(match, query)
    return None


def legacy_html_response(request: Request) -> Response | None:
    """Redirect, 404, or pass through according to ``LEGACY_HTML_CONSOLE``."""
    target = resolve_legacy_html_target(request.url.path, request.url.query)
    if target is None:
        return None
    mode = legacy_html_mode()
    if mode == "serve":
        return None
    if mode == "gone":
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": GONE_DETAIL},
        )
    path, separator, query = target.partition("?")
    if not separator:
        query = request.url.query
    return RedirectResponse(
        console_public_url(path, query),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


def maybe_redirect_legacy_html(request: Request) -> Response | None:
    """Return a Next.js redirect unless this listener should still serve Jinja."""
    return legacy_html_response(request)


class LegacyHtmlRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect leftover HTML paths before route dependencies can 303 to login."""

    async def dispatch(self, request: Request, call_next):
        if request.method in {"GET", "HEAD"}:
            redirected = legacy_html_response(request)
            if redirected is not None:
                return redirected
        return await call_next(request)
