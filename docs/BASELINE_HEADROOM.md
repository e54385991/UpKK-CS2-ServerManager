# 基线余量优化验收记录

对照提交：`8043630`。验证环境：macOS、Python 3.14.7、Node.js 26、Next.js 16.3.8。
实施期间已有拆分由本地提交 `0f8d452`、`0de62df` 保存，最终交付在其后创建新提交，不改写历史。

## 指标与范围

| 指标 | 拆分前 | 拆分后 | 验收目标 / 原硬门禁 |
| --- | ---: | ---: | --- |
| 文件额度使用率 ≥90% | 26 | 0 | 生产 ≤640 行、测试 ≤960 行 / 800、1200 行 |
| 复杂度 ≥13（含豁免） | 73（55 个可见、18 个被豁免） | 0 | ≤12 / ≤15 |
| 函数最大复杂度 | 74 | 12 | ≤12 / ≤15 |
| `C901` 豁免 | 18 | 0 | 全部移除 |
| 相关生产文件最大行数 | 791 | 640 | ≤640 / ≤800 |
| 相关测试文件最大行数 | 2082 | 710 | ≤960 / ≤1200 |
| 40 个路由首屏 gzip 最大值 | 252.88 KiB | 239.57 KiB | ≤240 KiB / ≤253 KiB |
| 最重路由相对硬门禁的余量 | 0.05% | 5.31% | 至少约 5% |

行数按原文件规模门禁的 `splitlines()` 测量；相关范围包含拆分产生的新文件及本次修改的代码。
复杂度使用与门禁相同的 Ruff C901 扫描目录，额外启用 `--ignore-noqa`，包括隐藏的函数。
新增代码不改变 800 / 1200 行、复杂度 15、首屏 253 KiB、单块 150 KiB 的硬阈值；三个历史行数豁免已移除。

原 26 个文件的对照：

| 文件 | 原行数 | 当前行数 |
| --- | ---: | ---: |
| `api/routes/github_plugins.py` | 720 | 583 |
| `api/routes/auth.py` | 724 | 567 |
| `api/routes/discord_bot.py` | 777 | 508 |
| `api/routes/ai.py` | 729 | 548 |
| `api/routes/plugin_market/routes.py` | 761 | 518 |
| `api/routes/actions/deployment.py` | 774 | 248 |
| `api/contracts/v1/plugins.py` | 781 | 184 |
| `modules/schemas/plugins.py` | 736 | 52 |
| `services/ai_provider.py` | 791 | 478 |
| `services/redis_manager.py` | 745 | 542 |
| `services/ssh_connection_pool.py` | 773 | 560 |
| `services/server_monitor.py` | 744 | 373 |
| `services/game_mode_install_service.py` | 740 | 592 |
| `services/server_operation_hub.py` | 759 | 639 |
| `services/map_management_service.py` | 773 | 273 |
| `services/ssh/file_archive.py` | 738 | 516 |
| `tests/test_ai_assistant_security.py` | 1154 | 479 |
| `tests/test_ai_agent_enhancements.py` | 2082 | 164 |
| `tests/test_file_manager_archive.py` | 1283 | 379 |
| `tests/test_discord_bot_agent_policy.py` | 1857 | 710 |
| `frontend/src/modules/settings/settings-form.tsx` | 784 | 597 |
| `frontend/src/modules/plugins/github-install-form.tsx` | 778 | 545 |
| `frontend/src/modules/updates/updates-console.tsx` | 720 | 310 |
| `frontend/src/modules/cleanup/cleanup-console.tsx` | 728 | 403 |
| `frontend/src/modules/servers/create-form.tsx` | 724 | 438 |
| `frontend/src/modules/servers/setup-wizard.tsx` | 731 | 448 |

包体对照使用原有 `frontend/scripts/bundle-budget.mjs`，在独立临时目录构建 `8043630` 的前端并与当前构建比较。
40 个路由均满足 240 KiB；以下列出最重的三个路由和设置页。这里的首屏 gzip 是门禁所定义的 runtime、layout、loading 和 page entry JS 之和。

| 路由构建入口 | 原 gzip KiB | 当前 gzip KiB | 相对 253 KiB 的余量 |
| --- | ---: | ---: | ---: |
| `(console)/servers/[id]/files/page` | 252.88 | 239.57 | 5.31% |
| `(console)/servers/[id]/updates/page` | 246.71 | 232.74 | 8.01% |
| `(console)/servers/new/page` | 246.97 | 232.17 | 8.23% |
| `(console)/settings/page` | 250.88 | 210.95 | 16.62% |

## 实现与兼容性

- API 按认证、AI、Discord、插件目录与安装职责分开；插件 DTO 按目录、计划和批量操作组织。原模块继续导出原入口，路由顺序和 HTTP 契约保留。
- AI 协议与请求预算、地图配置、Redis 缓存、SSH 池辅助逻辑、任务持久化与事件处理分别拆分。控制器继续持有可变状态、单例和锁；辅助逻辑接收控制器、具体依赖或阶段数据。
- 部署、启动、框架安装、自动更新、AI 编排及 Discord 交互按现有阶段拆分，保留命令顺序、事务边界、重试、进度事件、回滚及取消清理。
- 六个大型前端界面拆成 hooks、视图和表单部件。设置分区首次访问时加载，随后保持挂载；活动托盘面板、上传面板与反馈面板按需加载，计数和后台订阅常驻。设置 hash 由挂载后的 effect 读取，保证直接分区链接的 hydration 一致。
- 四个大型测试文件按协议、安全、审批执行、授权交互、归档解析和任务生命周期拆分，共保留 215 个原用例。
- 未修改 OpenAPI、路由、公开导出或 SSH 接口快照，未添加依赖、工作流框架或数据库 revision。

## 提前预警

```bash
uv run python scripts/report_baseline_headroom.py
uv run python scripts/report_baseline_headroom.py --format json --include-bundles
```

脚本复用现有文件预算和复杂度目录，报告文件使用率 ≥90%、复杂度 ≥13、包体使用率 ≥95% 的位置、值、阈值、余量和复杂度豁免状态。
`--include-bundles` 仅读取已有 `.next` 产物，不构建、不改源码。各采集器独立容错，失败记入 `unavailable`；完整基线最后执行预警，其退出结果不覆盖现有硬门禁成败。
本次最终报告的 `files`、`complexity`、`bundles`、`unavailable` 均为空。

## 验证

- 原四个测试文件与拆分后的测试收集结果按类名、函数名及参数 ID 比较，多重集合完全一致（215 →215）。排序后 JSON 的 SHA-256 均为 `76f453b519a4097de2fc231cceb166d4f725a3ca5df7f3bb81867182c72dc269`。
- 完整基线：`uv run python scripts/check_baseline.py`。2350 个 Python 用例通过、126 个 subtests 通过、1 个 PostgreSQL 集成用例按环境跳过；总覆盖率 87.46%（门槛 86.70%）、拆分领域覆盖率 91.25%（门槛 90%）。前端源契约 3 个、模块测试 143 个通过；静态检查、兼容快照、生产构建、40 路由包体门禁及依赖审计通过。
- 重点用例覆盖每服务器 FIFO、SSE 重放、SSH 租约与重连、取消释放、失败回滚、AI 413 单次恢复及授权复核。
- 浏览器使用真实 Chromium 与本地 mock，分别串行执行以下两个生产模式配置：

```bash
cd frontend
PERF_PRODUCTION=1 npx playwright test --config=playwright.performance.config.ts
PERF_PRODUCTION=1 npx playwright test --config=playwright.monitor.config.ts
```

性能及导航配置 50 个通过、1 个 dev-only Next MCP 检查跳过；设置及监控配置 14 个通过。
新增回归验证中英文、390px / 1440px、首次加载、设置分区草稿与挂载保留、直接 hash 链接、初始化受控字段、文件上传中止与页面恢复操作；已有用例验证文件编辑、重命名、未保存确认、解压、GitHub 安装和托盘自动打开、后台订阅及终态 SSE 停止。
`git diff --check` 通过。

## 剩余限制

- PostgreSQL 实库集成、Compose 健康检查和 CI 公共页面 smoke 未在本次本地环境运行；此处浏览器结果来自 mock，不代表线上 SSH 或数据库验证。未访问线上数据库、执行迁移或重启服务。
- 完整 pytest 留有原 Discord mock 的 `Client.start` 未 await 警告，测试仍通过。
- 保留拆分前的两项 UI 行为：初始化模式切换会重建未受控主机输入；取消上传会保留原进度条目。受控用户名草稿保留，上传请求确已中止且页面重新可操作。未将这两项原有行为扩展为产品修复。
- 前端验证使用生产构建，因此未提供 `/_next/mcp` 的 dev 运行时证据；已有 dev-only 检查按配置跳过。
- 所有本轮余量目标均达成。只创建本地提交，不推送或发布。
