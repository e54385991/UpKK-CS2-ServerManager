"""Shared template and static asset configuration."""

import uuid
from pathlib import Path

from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIRECTORY = PROJECT_ROOT / "static"
TEMPLATE_DIRECTORY = PROJECT_ROOT / "templates"
STATIC_ASSET_VERSION = uuid.uuid4().hex


def static_url(path: str) -> str:
    """Return a cache-busted URL for a static asset."""
    normalized_path = path.lstrip("/")
    separator = "&" if "?" in normalized_path else "?"
    return f"/static/{normalized_path}{separator}v={STATIC_ASSET_VERSION}"


templates = Jinja2Templates(directory=str(TEMPLATE_DIRECTORY))
templates.env.globals.update(
    static_url=static_url,
    static_version=STATIC_ASSET_VERSION,
)
