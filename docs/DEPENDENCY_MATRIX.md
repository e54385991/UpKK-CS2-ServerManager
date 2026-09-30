# 依赖与运行时矩阵

依赖版本由 `pyproject.toml`、`uv.lock`、`requirements.txt` 和
`frontend/package-lock.json` 共同锁定。`requirements.txt` 为生产导出并包含哈希；开发环境使用 `uv sync --dev`。

| 类别 | 当前基线 | 维护方式 |
| --- | --- | --- |
| Python | 3.14+ | `uv` 解析，生产导出 `requirements.txt` |
| uv | Docker `0.12.21-alpine`（digest 钉死） | 只用于镜像构建，运行镜像不保留 uv |
| FastAPI / Starlette | `>=0.142.2` / `>=1.7.0` | 保持上游兼容约束 |
| SQLAlchemy / SQLModel | `>=2.0.54` / `>=0.0.47` | PostgreSQL 主路径，短事务 |
| PostgreSQL | Compose `18.6-alpine` | 健康检查后启动，Alembic 自动升级 |
| Redis | Compose `8.10.2-alpine3.23` | 保持 Redis 7 协议兼容，pipeline/MGET |
| Caddy | Compose `2.11.4-alpine`（digest 钉死） | 仅 `--profile edge` / 1Panel 公网入口 |
| HTTP | 生产 `httpx>=0.28.1` | 应用级共享 transport |
| Starlette 测试客户端 | `httpx2>=2.13.1`（开发） | 仅用于测试兼容层 |
| SSH | `asyncssh>=2.24.0` | 显式 lease 和连接池 |
| Node.js | 26 Current（Docker `node:26.10.0-alpine3.24`） | CI `setup-node` 与前端镜像对齐 |
| 前端控制台 | Next.js 16.3.8、React 19.3.0、next-intl 4.14.8 | `frontend/package-lock.json`，TypeScript 6.0.3、ESLint 9.39.5 |

关键安全包当前下限为 `boto3>=1.43.106`、`cryptography>=50.0.2`、`webauthn>=3.0.1`。Dependabot 每周检查
uv、npm（`frontend/`）、Docker Compose 和 GitHub Actions；补丁/次版本合并分组，主版本单独 PR。TypeScript 7 超出 `typescript-eslint` 当前支持范围；ESLint 10 超出 React、import 和 JSX accessibility 插件声明的 peer 支持范围，因此两项主版本仍保持忽略；架构检查使用 `grimp>=3.17,<4.0.0` 与 `import-linter>=2.15,<3.0.0`。

SQLModel `0.0.45` 起默认把普通 `datetime` 映射为要求带时区的 `UTCDateTime`（`TIMESTAMP WITH TIME ZONE`）。现有 Alembic schema 把这些列存为 naive UTC 的 `TIMESTAMP WITHOUT TIME ZONE`，因此表模型里的普通 `datetime` 字段都显式声明 `sa_type=DateTime(timezone=False)`，需要带时区的列继续使用 `sa_column=Column(DateTime(timezone=True))` 并配套迁移；`tests/test_postgresql_migrations.py` 会拒绝任何隐式 `UTCDateTime` 列并锁定带时区列清单。SQLAlchemy 仍停在 2.0.x：SQLModel 0.0.47 声明 `SQLAlchemy<2.1.0`，待上游放开后再评估 2.1。

## 稳定版本核对（2026-10-01）

本轮依据 PyPI、npm、GitHub Releases 和容器 registry 的实时元数据更新：
Python 锁文件升级 18 个包，前端锁文件升级 84 个条目（含各平台可选二进制包），
uv 构建镜像升级到 0.12.21 并重新固定多架构摘要。Python、Node.js、PostgreSQL、Redis、
Caddy 镜像，以及 GitHub Actions 和 pre-commit hooks 已是当时的最新稳定版本。
Next.js 16.3.8 包含安全修复，见[官方发布说明](https://github.com/vercel/next.js/releases/tag/v16.3.8)。

最新发布版本并不一定能放进当前依赖图；以下包保持上游支持的最新稳定版本，
不通过强制安装或覆盖依赖约束绕过兼容性要求：

| 包 | 当前锁定 | 最新稳定 | 保留原因 |
| --- | --- | --- | --- |
| TypeScript | 6.0.3 | 7.0.2 | `typescript-eslint@8.71.0` 声明 `typescript >=4.8.4 <6.1.0` |
| ESLint | 9.39.5 | 10.11.0 | React、import、JSX accessibility 插件的 peer 范围最高为 ESLint 9 |
| SQLAlchemy | 2.0.54 | 2.1.1 | `sqlmodel@0.0.47` 要求 `SQLAlchemy<2.1.0` |
| multidict | 6.9.1 | 7.0.0 | `aiohttp@3.14.3` 要求 `multidict<7.0` |
| pydantic-core | 2.46.5 | 2.49.0 | `pydantic@2.13.5` 精确要求 `pydantic-core==2.46.5` |

前端的其他传递依赖同样由父包的版本范围决定，使用 npm 正常解析所得的最新兼容版本。
npm 已将 ESLint 9.39.5 标记为停止维护；当前保留它是插件兼容性限制，
应在这些插件支持 ESLint 10 后解除该限制。
后续更新应重新核对这些约束，不能把本表视为永久冻结策略。

## 更新流程

1. 核对最新稳定版本及上游兼容约束，更新 `pyproject.toml` 下限后运行
   `uv lock --upgrade` 和 `uv export --no-dev --no-emit-project --format requirements-txt -o requirements.txt`；
2. 前端先更新 `frontend/package.json` 中的精确版本，再在 `frontend/` 内运行
   `npm update --package-lock-only && npm ci`，同步更新兼容范围内的传递依赖；
3. 执行 `uv run python scripts/check_baseline.py`，确认锁文件、测试和审计一致；
4. 生产升级前先在 PostgreSQL 18、Redis 8 Compose 环境做健康启动和回滚演练。
