# Database migrations

Alembic is the only schema authority. Application startup acquires a
PostgreSQL advisory lock and upgrades to the single checked-in head before any
session or background service starts.

Normal application startup already upgrades to head. Do not ask operators to
run Alembic or `db_admin upgrade` by hand.

Optional diagnostic commands:

```bash
uv run python -m modules.db_admin status
uv run python -m modules.db_admin check
uv run python -m modules.db_admin upgrade
```

Developers create a reviewed revision for every model change:

```bash
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic check
```

Never restore `SQLModel.metadata.create_all()` to a production startup path.
