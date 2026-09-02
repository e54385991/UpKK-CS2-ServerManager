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

# 维护与质量基线

## FastAPI、Pydantic 与配置

- `/api/v1` 是维护中的 HTTP 契约。请求模型必须使用 Pydantic v2，并继承
  `api.contracts.base.ApiRequest`（`extra="forbid"`）；旧版 `/api/*` 请求保持兼容。
- 响应模型必须继承 `ApiResponse`，路由装饰器必须显式声明 `response_model`。HTML、重定向、
  文件、SSE 和 WebSocket 必须显式声明非 JSON 响应或 `response_model=None`。
- 路由不得返回 SQLModel/ORM 实例；使用 presenter 将 service result 转成独立 response DTO。
  密码、token、密钥和 webhook 只能通过写入接口、存在标志/前缀或精确的一次性 allowlist 暴露。
- 依赖统一使用 `Annotated[T, Depends(...)]`。禁止新增 `value: T = Depends(...)`。
- 配置统一使用 `modules.config.get_settings()`（`@lru_cache(maxsize=1)`），在应用启动时验证一次。
  `modules.config.settings` 仅是兼容导出；业务代码通过依赖注入或构造器传入配置。
- 默认运行模式必须是生产：`DEBUG=False` 且 `RUN_MODE=production`。Dockerfile、默认
  Compose/1Panel Compose 和 `.env.example` 均遵循该默认；开发模式只能通过显式调试 Compose
  覆盖（`docker-compose.debug.yml`）或环境变量开启。
- Docker 构建通过 `GIT_SHA` 与 `BUILD_TIME` 注入 `APP_GIT_SHA`、`APP_BUILD_TIME`；健康接口仅返回
  经过校验的 7 位短哈希和构建时间。控制台页脚展示前后端应用版本、短哈希和 UTC 构建时间，未知值
  必须显示为占位符，不能暴露任意环境变量内容。

## 模块与 I/O 边界

- `api/contracts/v1/<domain>.py` 及其 `requests.py`/`responses.py` 索引只放 HTTP DTO；
  `services/<domain>/types.py` 放 transport-independent command/result；ORM 只放在
  `modules/models/`。
- 路由只做鉴权、输入校验、用例调用和响应映射；数据库查询、事务、SSH/HTTP 远程 I/O 属于
  service/repository。长 I/O 期间不得持有请求数据库 session。
- 服务器长任务继续使用 `services.server_operation_hub` 的 202 + 每服务器 FIFO + 可回放 SSE；
  不使用 FastAPI `BackgroundTasks` 替代持久队列。生命周期后台循环由 `task_registry` 统一管理。
- 新生产 Python/TypeScript 文件不超过 800 行，测试文件不超过 1200 行；应按领域和职责拆分，
  而不是通过 `Any`、`cast()` 或无理由 `type: ignore` 绕过类型检查。

## 必须通过的检查

完成任何代码变更前，至少运行：

```bash
uv run python scripts/check_baseline.py
cd frontend && npm run lint && npm run typecheck && npm run build
```

基线包含 uv lock、pre-commit、Ruff、basedpyright（零错误/警告）、import-linter、循环依赖、
复杂度 ≤15、文件规模、API response_model/敏感字段、OpenAPI/路由/公开导出快照、全量 pytest、
覆盖率、依赖审计，以及 Next.js lint/typecheck/build/bundle budget。数据库集成、Compose 健康检查和
稳定的 Next.js Playwright 公共页面 smoke 按 CI job 执行。新增或重构的领域必须补充单元、集成、
契约、安全和性能回归测试。
