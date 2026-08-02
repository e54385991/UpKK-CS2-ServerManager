"""Create AI assistant and plugin compatibility metadata."""

# ruff: noqa: F403,F405

from .common import *


async def _add_column(conn: AsyncConnection, table: str, column: str, definition: str) -> None:
    if await table_exists(conn, table) and not await column_exists(conn, table, column):
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


async def migrate_ai(conn: AsyncConnection) -> None:
    """Create the current AI tables idempotently for existing installations."""
    await conn.run_sync(SQLModel.metadata.create_all)
    definitions = {
        "reasoning_effort": "VARCHAR(16) NULL",
        "temperature": "DOUBLE NULL",
        "top_p": "DOUBLE NULL",
        "max_completion_tokens": "INT NOT NULL DEFAULT 2048",
        # Preserve the request shape used before this column existed. New rows
        # created by the application default to the current OpenAI field.
        "token_limit_parameter": "VARCHAR(32) NOT NULL DEFAULT 'max_tokens'",
        "frequency_penalty": "DOUBLE NULL",
        "presence_penalty": "DOUBLE NULL",
        "verbosity": "VARCHAR(16) NULL",
        "parallel_tool_calls": "BOOLEAN NULL",
        "streaming_tested": "BOOLEAN NOT NULL DEFAULT FALSE",
    }
    for table in ("ai_system_settings", "user_ai_settings"):
        had_streaming_test = await column_exists(conn, table, "streaming_tested")
        for column, definition in definitions.items():
            await _add_column(conn, table, column, definition)
        if table == "ai_system_settings" and not had_streaming_test:
            await conn.execute(text("UPDATE ai_system_settings SET enabled = FALSE"))
    had_tool_call_limit = await column_exists(
        conn, "ai_system_settings", "max_tool_calls_per_round"
    )
    await _add_column(conn, "ai_system_settings", "max_provider_rounds", "INT NOT NULL DEFAULT 200")
    await _add_column(
        conn,
        "ai_system_settings",
        "max_tool_calls_per_round",
        "INT NOT NULL DEFAULT 200",
    )
    if not had_tool_call_limit:
        # The previous application default was 30 rounds. Migrate that old
        # default once while preserving other custom limits.
        await conn.execute(
            text(
                "UPDATE ai_system_settings "
                "SET max_provider_rounds = 200 "
                "WHERE max_provider_rounds = 30"
            )
        )
    await conn.execute(
        text(
            "UPDATE ai_system_settings "
            "SET max_provider_rounds = 1000 "
            "WHERE max_provider_rounds > 1000"
        )
    )
    await conn.execute(
        text(
            "UPDATE ai_system_settings "
            "SET history_retention_days = 7 "
            "WHERE history_retention_days > 7"
        )
    )
