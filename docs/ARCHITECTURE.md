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
门禁限制在 15 以内。历史工作流的剩余债务不再扩大，并按领域迁移逐步移除。
