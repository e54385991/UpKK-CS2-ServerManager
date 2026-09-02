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

批量动作使用一次授权 SQL 查询和一次 Redis pipeline。A2S 扫描使用有界全局并发和总
截止时间；WebSocket 广播对每个客户端设置独立发送超时，慢客户端会被移除，不会阻塞
其他客户端。

## 兼容性与演进规则

- 不删除旧路由、Redis key、响应字段或模板 DOM id。
- 新的领域服务放在 `services/<domain>/`，旧文件只做 facade 或兼容适配。
- 领域服务之间通过 `services.ports`、回调注册或容器注入通信，禁止新增反向导入。
- 纯解析器保持无 I/O，状态机输入和输出有明确上限。

每次结构变更必须运行 `uv run python scripts/check_baseline.py`；它会同时执行导入边界、
循环依赖、Python/前端测试、模板校验和依赖审计。

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

进程配置通过 `modules.config.get_settings()` 缓存加载：端口、连接池、超时、日志级别、运行模式、
HTTP URL 和 JWT/应用密钥在构造时一次性校验；测试可清空缓存或覆盖 `SettingsDependency`。
生产是默认运行模式（`DEBUG=False`、`RUN_MODE=production`），镜像与默认 Compose 配置显式保持
该安全默认；开发环境必须通过专用 debug Compose 或环境变量显式开启。

## 质量与性能门禁

生产 Python/TypeScript 模块最多 800 行、测试最多 1200 行（生成的 OpenAPI 类型与 Alembic
revision 豁免）；所有函数复杂度不超过 15，basedpyright 不允许错误或警告。JSON 路由必须显式 response model，敏感响应字段必须通过安全 allowlist。全量测试要求
行/分支覆盖率达到 90%，变更代码达到 95%。批量授权、Redis pipeline、有界并发、短事务和 SSE
慢客户端隔离属于性能契约，任何重构都必须保留并补充回归测试。
