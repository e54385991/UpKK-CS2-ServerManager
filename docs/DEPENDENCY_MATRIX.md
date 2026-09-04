# 依赖与运行时矩阵

依赖版本由 `pyproject.toml`、`uv.lock`、`requirements.txt` 和 `package-lock.json` 共同
锁定。`requirements.txt` 为生产导出并包含哈希；开发环境使用 `uv sync --dev`。

| 类别 | 当前基线 | 维护方式 |
| --- | --- | --- |
| Python | 3.14+ | `uv` 解析，生产导出 `requirements.txt` |
| FastAPI / Starlette | `>=0.141.1` / `>=1.6.0` | 保持上游兼容约束 |
| SQLAlchemy / SQLModel | `>=2.0.52` / `>=0.0.42` | PostgreSQL 主路径，短事务 |
| PostgreSQL | Compose `18.6-alpine` | 健康检查后启动，Alembic 自动升级 |
| Redis | Compose `8.10.1-alpine` | 保持 Redis 7 协议兼容，pipeline/MGET |
| Caddy | Compose `2.11.4-alpine`（digest 钉死） | 仅 `--profile edge` / 1Panel 公网入口 |
| MySQL | 测试 `8.4.11` LTS | 仅遗留迁移测试，不作为生产默认 |
| HTTP | 生产 `httpx>=0.28.1` | 应用级共享 transport |
| Starlette 测试客户端 | `httpx2>=2.12.0`（开发） | 仅用于测试兼容层 |
| SSH | `asyncssh>=2.24.0` | 显式 lease 和连接池 |
| Node.js | 26 Current（Docker `node:26.8.1-alpine`） | CI `setup-node` 与前端镜像对齐 |
| 前端控制台 | Next.js 16.3.4、React 19.2.8 | `frontend/package-lock.json`，TypeScript 钉 5.9.3、ESLint 钉 9 |
| 遗留静态资源 | Alpine.js、Bootstrap、xterm 6 | 根目录 `package-lock.json` 锁定，`npm run vendor:frontend` |

关键安全包当前下限为 `boto3>=1.43.88`、`cryptography>=50.0.1`。Dependabot 每周检查
uv、npm（仓库根与 `frontend/`）、Docker Compose 和 GitHub Actions；补丁/次版本合并分组，主版本单独 PR。TypeScript 7 与 ESLint 10 在 typescript-eslint 支持前保持忽略；架构检查使用 `grimp>=3.16,<3.17` 与 `import-linter>=2.14,<2.15`。

## 更新流程

1. 修改声明后运行 `uv lock --upgrade` 和 `uv export --no-dev --no-emit-project --format requirements-txt`；
2. 运行 `npm update --package-lock-only && npm ci && npm run vendor:frontend`；
3. 执行 `uv run python scripts/check_baseline.py`，确认锁文件、测试、审计和静态资源一致；
4. 生产升级前先在 PostgreSQL 18、Redis 8 Compose 环境做健康启动和回滚演练。
