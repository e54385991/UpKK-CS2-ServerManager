"""Static asset directory for plugin uploads and host setup libraries."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIRECTORY = PROJECT_ROOT / "static"
