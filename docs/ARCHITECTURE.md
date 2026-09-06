# 架构边界与资源生命周期

本项目采用渐进式拆分，不改变已有 HTTP、WebSocket、鉴权、模板上下文或兼容导出。

## 依赖方向

依赖方向固定为：

```text
api  →  services  →  modules
```

`modules` 只包含领域模型、数据库、配置和纯规则；它不能导入 `api` 或 `services`。
`services` 提供用例编排和外部资源适配；它不能导入 `api`。`api` 只做鉴权、输入校验、用例调用和响应映射。

边界由 `.importlinter` 和 `scripts/check_architecture.py` 自动检查。新增模块应优先依赖
`services.ports` 中的协议，而不是依赖另一个服务的可变全局对象。

## ServiceContainer 与生命周期

`api.application.create_app()` 接受可选的 `container_factory`。默认工厂创建
`ServiceContainer`，并由 lifespan 统一拥有以下资源：数据库引擎、Redis、共享 HTTP/AI
HTTP transport、SSH 连接池和后台服务。关闭时按创建逆序释放资源，避免路由或模块级单例
自行管理连接。

旧的模块级单例和 `services/*_service.py` 导出仍保留，它们是兼容 facade；新代码应通过
容器或协议注入依赖。这样既不破坏第三方导入，也能在测试中替换单个资源。

## 长事务与远程 I/O

服务器、插件和监控工作流遵循以下数据流：

1. 在短数据库事务中读取并复制快照；
2. 释放数据库连接后执行 SSH/HTTP 远程操作；
3. 在新的短事务中写入结果。

批量动作使用一次授权 SQL 查询和 Redis pipeline；概览遥测通过 `get_many()` 一次 MGET
读取授权服务器的缓存，保留顺序、前缀、TTL 和缓存缺失语义。磁盘/A2S 默认读取不探测远端。
概览和旧版批量磁盘读取用 `services/servers/telemetry.py` 在 I/O 前结束读事务；后台扫描
先退出 session 上下文，再派发探测。并发子任务不共享请求数据库 session。

磁盘、主机信息和 steam.inf 共用进程级 SSH 限流器（总量 4，同 host + SSH port 为 1）；
A2S 单例服务的前台刷新、单项刷新和后台扫描共用 8/服务器 1 的限流器，后台保留总截止时间。
这些是每进程上限，不能解释为多 worker 的集群总量。主机信息继续使用 Redis 去重锁；先取得
本地并发槽位，再申请锁，避免排队消耗锁的有效期。`collect_ordered()` 在失败或取消时取消
并等待所有子任务清理。WebSocket 广播对每个客户端设置独立发送超时，慢客户端会被移除。

概览统计和主机信息同时请求，主机信息在独立异步 Server Component / Suspense 中加载及降级，
不阻塞统计和最近服务器列表；服务器关联使用 ID Map。隔离 Playwright 测试使用后端响应门控
验证这个顺序，历史 Lighthouse 得分不作为当前性能承诺。

## 兼容性与演进规则

- 不删除旧路由、Redis key、响应字段或模板 DOM id。
- 新的领域服务放在 `services/<domain>/`，旧文件只做 facade 或兼容适配。
- 领域服务之间通过 `services.ports`、回调注册或容器注入通信，禁止新增反向导入。
- 纯解析器保持无 I/O，状态机输入和输出有明确上限。

完成检查的适用范围、去重和失败报告以根目录 `AGENTS.md` 的 Task Completion Checks 为准。
`uv run python scripts/check_baseline.py` 将全量 pytest 与全量覆盖率合并为一次执行，保留
独立领域覆盖率检查；每阶段输出耗时，同时审计 Python、根目录旧前端及 `frontend/` 生产依赖。

新拆分的 `services/ai`、`services/discord`、`services/servers` 和批量动作路由由复杂度
门禁限制在 15 以内；历史工作流也必须在本轮迁移中逐步拆分，不设置复杂度豁免。

## HTTP DTO 与模型隔离

维护中的 `/api/v1` 契约按 `api/contracts/v1/<domain>.py` 组织，并提供 `requests.py` 与
`responses.py` 索引。请求 DTO
继承 `ApiRequest`，响应 DTO 继承 `ApiResponse`；服务通过
`services/<domain>/types.py` 传递与 HTTP 无关的 command/result，ORM 只存在于
`modules/models/`。路由不得直接返回 ORM，必须通过 presenter 映射并使用 `response_model` 过滤
输出字段。旧 `modules.schemas` 和 `api.routes.v1.schemas` 仅作为兼容 facade。
`api.routes.v1.operation_runner` 同样只保留稳定导入路径，实际 worker 按服务器、插件、主机、
下载、清理和诊断职责拆分，并且 worker 只接收队列记录中的标量数据。
AI provider 请求使用 256K token 的默认上下文预算，管理员可按模型能力调为 8K、16K、32K、64K、128K、
256K、384K 或 1M；该预算与供应商 HTTP body 上限分离。发送前会按 system 前缀、完整工具调用组和最新
消息做有界压缩。若上游返回 413，只进行一次紧凑工具 schema、短历史和 512 输出预留的自适应重试，
并在最终错误中记录安全的请求字节、消息/工具字节及估算 token 诊断。助手 SSE 的 token_usage 只包含
估算/计量数字和安全阶段，绝不下发私有思维链文本。

进程配置通过 `modules.config.get_settings()` 缓存加载：端口、连接池、超时、日志级别、运行模式、
HTTP URL 和 JWT/应用密钥在构造时一次性校验；测试可清空缓存或覆盖 `SettingsDependency`。
生产是默认运行模式（`DEBUG=False`、`RUN_MODE=production`），镜像与默认 Compose 配置显式保持
该安全默认；开发环境必须通过专用 debug Compose 或环境变量显式开启。

镜像构建将 `GIT_SHA` 与 `BUILD_TIME` 写入 `APP_GIT_SHA`、`APP_BUILD_TIME`。后端 `/health` 通过
Pydantic 响应模型返回安全的 7 位短哈希和 UTC 构建时间，Next.js 根页脚展示前后端版本、短哈希和
构建时间；缺失或非法元数据统一显示为占位符。

Next.js 的 `deploymentId` 绑定镜像构建的不可变提交 SHA，用于在滚动发布时识别旧浏览器资源并
触发硬刷新。Server Action 加密密钥通过 BuildKit secret 仅在构建阶段提供；单一镜像扩容可直接
复用构建内置密钥，独立构建或多版本并行运行时必须设置同一个稳定的
`NEXT_SERVER_ACTIONS_ENCRYPTION_KEY`。该值不得写入 Compose 的运行时 `environment`、镜像
`ARG/ENV` 或仓库。升级期间旧页面偶发的 `Failed to find Server Action` 仍可能在刷新前出现，
但不应持续存在；SSE 客户端主动断开导致的 `context canceled` 属于正常取消，不应按业务异常告警。

## 质量与性能门禁

新生产 Python/TypeScript 模块最多 800 行、测试最多 1200 行，生成的 OpenAPI 类型等
排除项由 `scripts/check_file_sizes.py` 定义；遗留大文件使用其中的固定行数上限，禁止继续增长。
Python 生产及脚本函数复杂度上限 15；basedpyright 必须零错误/警告。JSON 路由显式声明
response model，敏感响应字段通过安全 allowlist。

实际执行的全量 Python 覆盖率门槛是 86.70%（遗留模块的不下降基线），独立检查的
`services.ai`、`services.discord`、`services.servers` 为 90%。全量 90% 和变更代码 95%
属于历史演进目标，当前脚本并未执行独立的变更覆盖率门禁；不能把目标写成已达到的结果。
批量授权、MGET/pipeline、有界并发、短事务和 SSE 慢客户端隔离属于性能契约，重构时必须
保留对应回归测试。数据库集成、Compose 健康和浏览器 smoke 按 `.github/workflows/quality.yml`
中的独立 CI job 执行；本地基线通过不代表这些集成检查已在本地运行。
