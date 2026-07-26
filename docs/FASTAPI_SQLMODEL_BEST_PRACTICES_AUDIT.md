# FastAPI / SQLModel 最佳实践审计

审计日期：2026-07-26

## 结论

项目在应用工厂、`lifespan`、异步数据库会话、领域路由拆分、Pydantic v2、
SQLModel 模型与 API schema 分离，以及契约测试方面已经符合当前官方推荐实践。
本次修复了明确的框架兼容风险并升级了稳定依赖，但仍有需要分阶段治理的遗留项。
因此，结论是“基本符合，但尚未完全符合”。

## 官方依据

- [FastAPI Release Notes](https://fastapi.tiangolo.com/release-notes/)：
  FastAPI 0.140.0 是审计日可用的最新稳定版本；0.137.0 起
  `APIRouter.routes` 被标记为内部实现。
- [FastAPI Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/)：
  多模块应用通过 `APIRouter` 和 `include_router()` 组合。
- [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/)：
  推荐使用 `lifespan` 管理应用级启动与关闭资源。
- [FastAPI Response Model](https://fastapi.tiangolo.com/tutorial/response-model/)：
  强类型响应契约用于验证、过滤和生成 OpenAPI。
- [FastAPI Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)：
  推荐使用可复用依赖，并在现代 Python 中优先使用 `Annotated`。
- [SQLModel 0.0.39 Release](https://github.com/fastapi/sqlmodel/releases/tag/0.0.39)：
  SQLModel 0.0.39 是审计日可用的最新稳定版本。
- [SQLModel Update with Extra Data](https://sqlmodel.tiangolo.com/tutorial/fastapi/update-extra-data/)：
  部分更新使用 `model_dump(exclude_unset=True)` 和 `sqlmodel_update()`。

## 版本矩阵

| 组件 | 兼容下界 | 锁定版本 | 本次变化 |
| --- | ---: | ---: | --- |
| FastAPI | 0.140.0 | 0.140.0 | 0.139.2 → 0.140.0 |
| Starlette | 1.3.1 | 1.3.1 | 新增直接依赖 |
| SQLModel | 0.0.39 | 0.0.39 | 已是最新稳定版 |
| SQLAlchemy | 2.0.51 | 2.0.51 | 无变化 |
| Pydantic | 2.13.4 | 2.13.4 | 由锁文件管理 |
| Uvicorn | 0.51.0 | 0.51.0 | 无变化 |
| boto3 / botocore | 1.43.56 | 1.43.56 | 1.43.53 → 1.43.56 |
| Ruff | 0.16.0 | 0.16.0 | 0.15.22 → 0.16.0 |
| pre-commit | 4.6.1 | 4.6.1 | 4.6.0 → 4.6.1 |

项目继续采用 `pyproject.toml` 声明兼容下界、`uv.lock` 精确锁定应用环境的策略。
`requirements.txt` 从锁文件生成，仅包含生产依赖。

## 已符合或本次已修复

- `create_app()` 应用工厂与 `lifespan` 集中管理运行期资源。
- 数据访问使用 SQLAlchemy/SQLModel 异步引擎和 `AsyncSession`。
- 路由按领域拆分，兼容 facade 保留既有 Python 导出。
- 路由组合仅使用公开的 `include_router()`；测试和诊断使用
  `iter_route_contexts()`，生产代码不再读写 `APIRouter.routes`。
- 简单 PATCH/PUT 路径使用 `model_dump(exclude_unset=True)` 和
  `sqlmodel_update()`；密码、令牌、Discord 配置和派生状态继续走专用逻辑。
- 路由清单、匹配顺序和 OpenAPI 契约由基线测试保护，本次没有更改 HTTP 或
  WebSocket 接口。
- 生产启动命令使用 `uv run --no-dev --locked`，避免部署环境解析和安装测试、
  lint 与 pre-commit 工具。
- Starlette 因生产代码直接导入而被声明为直接依赖。
- Ruff 0.16 的格式化范围排除 Markdown，防止文档代码块被工具自动改写。

## 延期事项

以下事项需要独立设计和兼容计划，本次不进行会改变 OpenAPI、鉴权行为或数据库
升级流程的大规模整改：

1. 约 301 处依赖参数仍直接声明 `Depends(...)`，尚未统一为共享的
   `Annotated` 类型别名。
2. 约 97 条非页面 API 路由缺少显式 `response_model`，补齐会改变或收紧
   OpenAPI 与响应过滤行为。
3. 兼容 facade 和部分 schema/model 模块仍使用通配导入，静态分析边界不清晰。
4. 首次启动仍会创建 `admin` / `admin123` 默认管理员凭据，应改为一次性随机
   凭据或强制初始化流程。
5. 数据库升级依赖自定义有序迁移和 `SQLModel.metadata.create_all()`，尚未采用
   Alembic 等带版本图、升级和回滚记录的标准迁移系统。

MySQL `8.0` 与 Redis `7-alpine` 镜像主版本不在本次依赖升级范围内；基础设施
主版本升级应另行制定备份、兼容验证和回滚方案。
