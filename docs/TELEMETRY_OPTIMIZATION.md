# 遥测、概览与质量基线优化记录

本轮保持 HTTP DTO、数据库结构、权限、FIFO/SSE、缓存 key 和 TTL 兼容。所有实现与验证使用本地隔离环境，未访问线上数据库、执行真实游戏服务器操作、推送或发布。

## 已落实的改变

- Redis `get_many()` 统一前缀、JSON 解码、缺失及连接故障处理。磁盘、主机信息、A2S 的批量命中路径只发一次 MGET；空列表不发请求，磁盘/A2S 概览默认缺失仍不探测。A2S 普通空对象与历史双重 JSON 编码继续保持单项读取的区别。
- 概览和旧版批量磁盘读取在 service 中选取授权服务器，提交读事务后才访问缓存或远端；原概览 1000 条及旧版 100 条上限保留。后台扫描退出数据库 session 后派发任务；子任务只使用已加载的服务器字段。
- 磁盘、主机信息和 steam.inf 共用进程内 SSH 上限 4、同 host/SSH port 上限 1；A2S 单例的前台、后台和单项刷新共用 8/服务器 1 上限。保留后台跳过、单项超时和主机信息 Redis 去重锁；steam.inf 超时从取得槽位后开始计算。
- 批次保持输入顺序；发生异常或取消时取消并等待子任务退出，释放 SSH 租约、锁和并发槽。DEBUG 批次日志提供数量、命中、失败及耗时，不记录凭据、主机地址或原始命令。
- 概览同时启动统计及主机信息请求，主机信息在独立异步 Server Component/Suspense 中加载和降级。统计与最近服务器列表可以先显示；ID Map 代替逐项 `find`。布局和中英文内容保留。
- 12 个 Python 文件的 19 处 `asyncio.iscoroutinefunction` 改为 `inspect.iscoroutinefunction`；修正 SSH 超时测试桩丢弃协程的问题。同步、异步和 partial 回调各执行一次，原异常和取消继续传播。
- 全量 pytest 与全量覆盖率合并执行，独立领域覆盖率继续执行；基线输出阶段耗时，并审计 Python、旧前端和 Next.js 的生产依赖。两个 AGENTS.md 原位补充约定，架构文档区分实际门槛与历史目标。

## 可复现的受控性能对比

从仓库根目录执行：

```bash
uv run pytest -q -s tests/test_telemetry_batches.py -k controlled
```

同一次运行对比串行调用原单项接口与新批量接口。20 台模拟服务器，每次模拟 Redis 或远端 I/O 等待 10 ms；SSH 主机互不相同。缓存为预置数据，远端调用全部替换为内存测试桩，无真实 Redis、SSH、UDP 或数据库。以下为一次本机样本，数值会受调度和环境影响，测试只对结果、调用次数、并发和清理作确定性断言，不使用脆弱的毫秒阈值。

| 路径 | 串行耗时 | 批量耗时 | I/O 调用次数 | 峰值并发 |
| --- | ---: | ---: | --- | --- |
| 磁盘缓存 | 239.3 ms | 12.1 ms | 20 GET → 1 MGET | 缓存单次往返 |
| 主机信息缓存 | 237.5 ms | 12.9 ms | 20 GET → 1 MGET | 缓存单次往返 |
| A2S 缓存 | 236.7 ms | 11.9 ms | 20 GET → 1 MGET | 缓存单次往返 |
| 磁盘显式探测 | 242.1 ms | 63.3 ms | 20 → 20 | 1 → 4 |
| 主机信息显式探测 | 241.0 ms | 62.3 ms | 20 → 20 | 1 → 4 |
| A2S 显式探测 | 238.5 ms | 37.1 ms | 20 → 20 | 1 → 8 |
| steam.inf 探测 | 239.9 ms | 61.5 ms | 20 → 20 | 1 → 4 |

收益来自减少 Redis 往返及对不同主机的等待进行并发，不代表线上速度提升比例。SSH 同主机仍串行；大批次、真实超时或多进程环境需单独测量。

## 验证范围

- `tests/test_telemetry_batches.py`：0、1、20、100、1000 台的顺序、一次 MGET、空值、损坏缓存、Redis 故障、授权 scope 及远端工作前 commit。覆盖跨请求共享上限、同主机/同服务器互斥、取消后的连接/锁/槽位清理、子任务失败收尾和缓存 TTL。
- `tests/test_progress_callback_compatibility.py`：同步/异步、普通/partial 回调的成功、异常及取消，共 12 项；原有 SSH、S3 和插件流程测试继续覆盖实际调用路径。
- 独立浏览器测试使用 `frontend/playwright.overview.config.ts` 启动模拟后端和 Next dev。16 个场景覆盖中英文、390/1440 宽度、硬刷新/客户端导航、成功/失败；门控关闭时先断言统计可见，再放行主机信息，并检查 ID 关联、页面异常与横向溢出。
- 第 17 个浏览器场景验证空数据降级，并通过 Next `/_next/mcp` 检查编译及运行时错误均为空。它使用 Playwright 和随版本提供的 Next MCP；未声称执行 React DevTools 检查。全部 17 项通过（本次 14.0 秒），并接入现有 `frontend-playwright-smoke` CI job。

浏览器复验（从 `frontend/` 执行）：

```bash
npx playwright test --config=playwright.overview.config.ts
```

该配置只监听 loopback，使用虚拟登录态；端口通过 `OVERVIEW_MOCK_PORT` / `OVERVIEW_TEST_PORT` 覆盖。不要与同 checkout 的 Next build 同时运行。

## 质量结果与限制

最终执行 `uv run python scripts/check_baseline.py` 全部通过：

- 全量 Python：1888 passed、2 skipped、126 subtests，覆盖率 87.03%，门槛保持 86.70%。
- 独立领域覆盖率：91.61%，门槛保持 90%。新增遥测批次矩阵 74 项、回调兼容性 12 项全部通过。
- basedpyright 零错误/警告；锁文件、pre-commit、Ruff、依赖边界、循环依赖、复杂度、文件规模、API 安全、OpenAPI/路由/公开导出快照及前后端单元测试均通过。
- Next.js lint/typecheck/build 通过，56 个路由全部满足既有 bundle budget。
- Python、根目录旧前端及 Next.js 生产依赖审计均未发现已知漏洞；这是本次审计结果。
- `git diff --check` 通过；CI YAML、认证路径、类型生成命令和新增文档引用均已核对。

规划阶段的普通全量 pytest 为 48.31 秒、重复的覆盖率全量为 54.12 秒。本次合并后全量覆盖率运行 55.06 秒，仍保留全部测试，且新增 86 项；独立领域检查继续执行。优化消除一轮重复全量运行，未削弱门禁。前后样本并非同一时刻、同一测试数量，依赖审计还受网络影响，因此不承诺整套基线固定减少多少秒。

第三方 Starlette TestClient 仍触发一条 anyio `BlockingPortal` 别名弃用警告；本轮未全局屏蔽警告或升级无关依赖。浏览器在作为客户端导航起点的部署教程上仍提示首屏图片可设置 eager，这是既有页面的加载提示，不是概览运行时错误。

数据库集成、Compose 健康检查继续由既有 CI job 执行，本轮未在本机运行。SSH 清理使用隔离替身验证；A2S 底层仍由 `asyncio.to_thread` 执行同步 UDP 查询，取消 asyncio 任务不能强制终止已经进入系统调用的线程，其退出仍依赖既有 socket 超时。进程内限流不能当作多 worker 集群的总上限；仅主机信息继续有 Redis 跨进程去重锁。

## 后续问题清单

| 优先级 | 证据及问题 | 后续行动与边界 |
| --- | --- | --- |
| P1（超过 1000 台时） | `api/routes/v1/overview.py` 的 summary 使用 `limit=1000` 后 `len(servers)` 聚合；本轮保持兼容 | 独立设计数据库聚合和分页契约，验证权限与总数；不要在性能修补中悄悄改变列表范围 |
| P2 | `discord_bot_manager.py` 2758 行、`ai_tools.py` 2191 行、`plugin_auto_update_service.py` 1302 行、前端 files console 1318 行，处于固定遗留上限 | 按领域拆分并保持兼容 facade，逐步移除行数例外；不在本轮做大规模重构 |
| P2 | `Server.user_id` 已声明索引，当前无实际慢 SQL / EXPLAIN 证据支持新增索引 | 在授权的隔离数据库和真实量级数据上测量，再通过 Alembic 提案；不凭猜测增加索引 |
| P2 | SSH 上限是进程内 4；主机信息 900 秒、失败 60 秒，磁盘 3600 秒，A2S 60 秒，steam.inf 365 天 TTL 沿用现有行为 | 取得实际命中率、批次时长及多 worker 部署数据后，再评估缓存/刷新策略和集群协调 |
| P2 | A2S 同步 UDP 线程受原 socket 超时约束 | 如果需要取消后立即终止真实探测，单独评估异步 UDP 适配及协议/超时回归，不能声称现有协程清理测试覆盖线程终止 |
