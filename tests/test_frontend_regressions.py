"""Regression checks that still apply after the Jinja console was removed."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_console_starts_server_operations_with_the_action_only():
    """The operations request contract forbids extras; the console must match it.

    ``ServerOperationRequest`` carries ``action`` alone since the per-run
    patchelf switch moved to the Additional fixes page. A console that still
    appends ``clear_execstack`` turns every restart into a 422
    ("Extra inputs are not permitted").
    """
    source = (PROJECT_ROOT / "frontend/src/modules/servers/operations-api.ts").read_text(
        encoding="utf-8"
    )
    body = source.split("export async function startServerOperation(", 1)[1].split("\n}", 1)[0]

    assert "JSON.stringify({ action })" in body
    assert "clear_execstack" not in body

    route = (PROJECT_ROOT / "frontend/src/app/server-ops/servers/[serverId]/route.ts").read_text(
        encoding="utf-8"
    )
    assert "clearExecstack" not in route
