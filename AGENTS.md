# Repository layout

This repository hosts two applications:

- **Backend** (repo root): a FastAPI + PostgreSQL + Redis management panel
  (`main.py`, `api/`, `modules/`, `services/`, `alembic/`).
- **Frontend** (`frontend/`): a dedicated **Next.js 16.3.4** console that
  replaces the legacy Jinja/Bootstrap UI and talks to the backend through a
  same-origin proxy. See `frontend/AGENTS.md` for its rules — read it before
  working under `frontend/`. Before changing Next.js behavior, read the
  relevant version-matched sections of `frontend/node_modules/next/dist/docs/`
  (Next 16 has breaking changes); do not read the entire manual for every edit.

# Task Completion Checks

This section is the single source of truth for completion checks. For any
code, dependency, build, CI, or runtime configuration change, run the full
repository quality baseline at least once before reporting completion:

```bash
uv run python scripts/check_baseline.py
```

The baseline already runs the frontend unit tests, lint, typecheck, production
build, and bundle budget. A successful baseline satisfies those gates; do not
run them again solely because `frontend/AGENTS.md` lists them. Reuse results
only while the checked content, dependencies, and relevant environment remain
unchanged. After further edits, rerun the affected checks.

For documentation or instruction-only changes (including `AGENTS.md` and
`SKILL.md`), run `git diff --check` and validate affected references, examples,
and instruction consistency; validate Skill frontmatter when applicable. Run
additional checks if executable examples or changed instructions alter an
actual build/runtime contract. Read-only reviews do not require a build or
test run. These exceptions do not waive checks for accompanying code changes.

Fix failures caused by this task and continue other independent work while
investigating blockers. If a required check cannot run or remains failing,
report the exact command, observed failure, and remaining risk; distinguish
implementation progress from verified completion. Do not claim completion,
weaken a gate, or make unrelated repairs merely to turn the baseline green.

# Git 提交约定

- 每次完成文件修改并通过适用检查后，自动创建本地 commit；没有文件改动时不创建空提交。
- 本地 commit 的授权不包含推送、发布、合并 PR 或重启线上服务；用户明确要求“先审阅后修改”时，先遵守该阶段边界。
- 默认不执行 `git push`。只有用户明确要求本次任务推送时才推送。
- commit message 必须说明问题现象、根因、具体改动、验证结果和剩余限制；最终回复也要给出提交哈希及同等详细说明。
- 只暂存本次任务的文件或修改片段，暂存前检查差异。同一文件混有用户改动时按片段暂存；无法可靠分离时保留原状并说明阻塞。已有的用户改动（例如 `dump.rdb`）不得加入提交。
- 发现并发提交时创建新 commit，不 amend、不重写历史、不强制推送。

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
- Ensure the next application startup upgrades to the checked-in head; do not
  introduce a path that silently leaves an old schema in use. Repository edits
  alone do not authorize restarting a live panel or accessing its database.
  Keep startup auto-migration and do not require a manual migration step.

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
- Every CounterStrikeSharp install **and** upgrade — the framework action, a
  game-mode recipe, a marketplace/GitHub install of the framework itself, and
  auto-update — must leave `addons/counterstrikesharp/configs/core.json` with
  `FollowCS2ServerGuidelines: false`, seeding the file from the shipped
  `core.example.json` when the install has not created one yet
  (`services/plugins/counterstrikesharp_core.py`). The step runs after the
  install succeeds, never fails it, and must not fire for plugins that merely
  install *into* `addons/counterstrikesharp/plugins`.

# 插件中心（marketplace）

- 插件市场按运行框架分成两个一级分区：CounterStrikeSharp 与 SwiftlyS2
  （`MarketPlugin.framework`，取值 `counterstrikesharp` / `swiftly`，与面板其余
  地方的 framework key 一致）。新增插件默认落在 CounterStrikeSharp；控制台
  `/plugins` 默认打开该分区，列表通过 `GET /api/v1/plugins/market?framework=`
  过滤，可移植目录（导入/导出）也带上该字段。第三个取值 `other` 表示插件不属于
  任何一套运行时：它同时出现在两个分区（`search_plugins(include_framework_agnostic=True)`），
  也不受下面的运行时校验限制。
- **安装防呆（运行时校验）**：`build_plugin_install_plan` 会把插件的 framework 与
  远端实际检测到的运行时（`inspect_remote_plugin_inventory` 的
  `frameworks.counterstrikesharp` / `frameworks.swiftly`）比对，结果放在 plan 的
  `framework` 字段（`services/plugins/framework_compatibility.py`）。当插件所需运行时
  缺失、而另一套运行时已安装时 `mismatch=True`——例如 SwiftlyS2 服务器装
  CounterStrikeSharp 插件、或 CounterStrikeSharp 服务器装 SwiftlyS2 插件。此时
  `validate_plugin_plan_acknowledgements` 直接拒绝（409），除非调用方显式传
  `acknowledge_framework_mismatch`；该确认必须一路带到队列执行时的复核。控制台在
  预检结果里高亮该警告，并在安装前弹出二次确认。两套运行时都没装只是 `missing`，
  提示先去装框架，不阻止安装；Metamod 不算冲突运行时。
- 管理员编辑走 `PATCH /api/v1/plugins/market/{id}`：只应用请求体里出现的字段，
  省略或 `null` 表示保持原值，空字符串表示清空可选文本字段。分类（`category`）与
  运行框架（`framework`）都可以在这里改，控制台的「编辑」对话框同样提供这两个下拉。
- 添加插件时的「从 GitHub 自动填充」会顺带猜测分类：
  `services/plugins/repo_classification.py` 依据仓库名、描述、topics 和 README 前
  `README_SCAN_CHARS` 个字符推断 `framework` 与 `category`，随
  `GET/POST .../market/repo-info` 一起返回。两套运行时都提到、或只是 Metamod 插件时
  归为 `other`；识别不出时返回 `null`，表单保持原选择。这只是预填，管理员可覆盖，
  且不会覆盖用户已手动改过的下拉。
- 批量描述同步是 `POST /api/v1/plugins/market/descriptions/sync`（管理员）：
  用仓库 README 覆盖 marketplace 描述。它只访问 GitHub、不做任何 SSH 操作，
  因此**不进入**每服务器 FIFO，而是有界的同步 HTTP 调用——单次最多
  `MAX_DESCRIPTION_SYNC_PLUGINS` 个插件、并发 `SYNC_CONCURRENCY`
  （`services/plugins/description_sync.py`），剩余数量通过响应的 `remaining`
  返回，由管理员再次触发。外部请求前必须先提交读事务，不得在 GitHub I/O 期间
  持有请求数据库 session。

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
- AI 上下文窗口默认 256K tokens；管理员可按模型能力选择经过校验的 8K、16K、32K、64K、128K、
  256K、384K 或 1M 预设。这是本地历史预算，供应商 HTTP body 仍受独立的安全上限约束。请求过大时
  保留 system 前缀和完整的 assistant tool-call/tool-result 组，丢弃最旧历史并对单条工具输出做有界截断。
  上游返回 413 时只允许一次自适应重试（紧凑工具 schema、短历史、512 输出预留），禁止原样指数重试，
  最终错误必须包含请求字节数、消息/工具字节数和估算 token 数。
- 助手 SSE 的 `token_usage` 事件可展示输入/输出/合计 token 的实时估算或最终计量；前端只显示当前
  处理阶段和安全进度，不展示模型私有 chain-of-thought 或未脱敏工具参数。

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

执行范围、去重和受阻时的报告规则统一见上方 **Task Completion Checks**；不要维护第二套完成条件。

基线包含 uv lock、pre-commit、Ruff、basedpyright（零错误/警告）、import-linter、循环依赖、
复杂度 ≤15、文件规模、API response_model/敏感字段、OpenAPI/路由/公开导出快照、全量 pytest、
覆盖率、依赖审计，以及 Next.js lint/typecheck/build/bundle budget。数据库集成、Compose 健康检查和
稳定的 Next.js Playwright 公共页面 smoke 按 CI job 执行。新增或重构的领域必须覆盖单元、集成、
契约、安全和性能回归风险；先复用已有测试，仅为尚未覆盖的行为补充测试。某类测试不适用时，
说明依据，不为每次局部修改机械新增五套测试；已有质量门禁和覆盖率要求保持不变。
