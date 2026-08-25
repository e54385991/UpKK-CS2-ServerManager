# Task Completion Checks

Before reporting any task that changes repository files complete, run the
applicable baseline checks at least once and report the result. For every task
that changes Python code or Python tooling, run the full quality baseline:

```bash
uv run python scripts/check_baseline.py
```

Do not report the task as complete until the check passes. If the check cannot
be run, report the exact command, failure, and remaining risk.

# Database Schema Changes

Alembic revisions are the only schema authority. Application startup always
upgrades to the single checked-in head through `migrate_db()` /
`upgrade_database()` before any database session or background service starts.

- After adding a revision, rely on startup auto-migrate. Restarting the panel
  applies it.
- Do not tell the user to run `alembic upgrade`, `alembic upgrade head`,
  `python -m modules.db_admin upgrade`, or any revision by hand, including new
  revisions such as `0006_discord_channel_managers`.
- `modules.db_admin status|check|upgrade` are diagnostics and optional
  controlled-deploy tools, not a required user step.
- Never restore `SQLModel.metadata.create_all()` as a production startup path.
- Never leave the user on an old schema, and never instruct a manual migrate
  after a model or revision change.
