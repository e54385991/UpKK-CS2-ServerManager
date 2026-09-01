# Repository layout

This repository hosts two applications:

- **Backend** (repo root): a FastAPI + PostgreSQL + Redis management panel
  (`main.py`, `api/`, `modules/`, `services/`, `alembic/`).
- **Frontend** (`frontend/`): a dedicated **Next.js 16.3.3** console that
  replaces the legacy Jinja/Bootstrap UI and talks to the backend through a
  same-origin proxy. See `frontend/AGENTS.md` for its rules — read it before
  working under `frontend/`, and read `frontend/node_modules/next/dist/docs/`
  before writing Next.js code (Next 16 has breaking changes).

# Task Completion Checks

Before reporting any task that changes repository files complete, run the
applicable baseline checks at least once and report the result.

For every task that changes **Python** code or Python tooling, run the full
quality baseline:

```bash
uv run python scripts/check_baseline.py
```

For every task that changes **frontend** code (`frontend/`), the frontend gates
must pass:

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

Do not report the task as complete until the applicable checks pass. If a check
cannot be run, report the exact command, failure, and remaining risk.

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

# Delivery queue (plugins and long-running tasks)

Plugin installs, GitHub installs, archive extract, URL download to the host,
cleanup delete / system apply, plugin auto-update (run / test / cron), plugin
diagnostics execute / restore / resume, scheduled lifecycle and
`backup_plugins`, batch restart / stop / update / framework install, and other
long SSH jobs are **submitted to a per-server FIFO**, not run inline in the
HTTP request.

- The client **POSTs and leaves**. The API returns **202** with `operation_id`
  immediately. Do not hold the browser on the install form waiting for SSH.
- `services.server_operation_hub` is the queue. One worker runs at a time
  **per game server**. A second submit on the same host is **queued behind**
  the current job (it is not a 409 unless the pending cap is hit, or a lock
  is stuck with no active hub operation). Sequential execution avoids SSH
  lock conflicts and overlapping plugin extracts.
- Persist the **original command** (or a faithful command summary) on the
  operation record so the console can show what was submitted.
- Progress is the existing **replayable SSE** stream
  (`GET /api/v1/servers/{id}/operations/{operation_id}/events`). Do not add a
  second WebSocket just for panel jobs. `EventSource` cannot set
  `Authorization`; the Next console uses
  `/ops-stream/servers/{id}/operations/{operationId}`.
- **Do not attach the activity tray to tmux.** `tmux` / `screen` is the game
  or SteamCMD pane (`/live-console/{id}`). Plugin market installs and most
  panel actions run over SSH through the hub and never enter that session.
  The tray may offer “open live terminal” only for actions that actually use
  the deploy/game pane (`deploy`, `update`, `validate`, `start`).
- The global inbox is `GET /api/v1/operations/inbox`: queued + running jobs
  for servers the caller can access, plus **failed** jobs retained for **7
  days** (`failed_items`). Each item includes `server_name`, `command`, and
  `latest_message`. Operators can clear one failure with
  `DELETE /api/v1/operations/inbox/failed/{operation_id}` or all visible
  failures with `DELETE /api/v1/operations/inbox/failed`.
- After a process restart, in-memory runners for **pending** (not yet
  started) jobs are gone; those records must fail cleanly instead of hanging
  as “queued” forever.
