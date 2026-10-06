# 依赖与运行时矩阵

依赖版本由 `pyproject.toml`、`uv.lock`、`requirements.txt` 和
`frontend/package-lock.json` 共同锁定。`requirements.txt` 为生产导出并包含哈希；开发环境使用 `uv sync --dev`。

| 类别 | 当前基线 | 维护方式 |
| --- | --- | --- |
| Python | 最低 3.14；生产 3.14.7；兼容验证 3.15.0rc3 | `uv` 按 Python 版本解析，生产导出 `requirements.txt` |
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
| multidict | 6.9.1 | 7.0.0 | `aiohttp@3.14.4` 要求 `multidict<7.0` |
| pydantic-core（Python 3.14） | 2.46.5 | 2.49.0 | 稳定版 `pydantic@2.13.5` 精确要求 `pydantic-core==2.46.5` |

前端的其他传递依赖同样由父包的版本范围决定，使用 npm 正常解析所得的最新兼容版本。
npm 已将 ESLint 9.39.5 标记为停止维护；当前保留它是插件兼容性限制，
应在这些插件支持 ESLint 10 后解除该限制。
后续更新应重新核对这些约束，不能把本表视为永久冻结策略。

## Python 3.15 rc3 兼容准备（2026-10-06）

生产镜像、最低支持版本和静态检查目标继续使用 Python 3.14。兼容验证使用普通 GIL 的
Python 3.15.0rc3；不启用 free-threaded、lazy imports 或其他新运行模式。

Pydantic 根据 Python 版本分支解析：3.14 使用 `>=2.13.5,<2.14` 的稳定版及对应
core 2.46.5；3.15 及以上暂时精确使用 `2.14.0b2` 及其要求的 core 2.49.0。
这是已明确选择的预发布依赖例外，不开启全局预发布解析，也不绕过父包约束。
该 beta 的 [官方说明](https://pypi.org/project/pydantic/2.14.0b2/)声明完整支持 Python 3.15。
稳定版发布后再通过常规依赖更新移除这个临时分支。

定向升级 `aiohttp` 3.14.4、`uvloop` 0.23.0、`cbor2` 6.1.5 和 `MarkupSafe` 3.0.4，
它们提供 cp315 wheels；[uvloop 0.23.0](https://github.com/MagicStack/uvloop/releases/tag/v0.23.0)
还适配了 Python 3.15 的 `eager_start` 参数。锁文件与带哈希导出包含两条 Pydantic 分支，
Python 3.14 的生产安装不会引入 beta。

[Python 3.15 默认使用 UTF-8](https://docs.python.org/3.15/whatsnew/3.15.html#other-language-changes)。
8 处质量/性能工具的子进程文本输出已明确编码：结构化输出使用 UTF-8，通用外部程序
版本信息显式使用 locale 编码。回归测试在 ASCII locale、关闭 UTF-8 模式、将
`EncodingWarning` 视为错误的独立进程内验证中文 JSON。生产代码审计未发现直接使用
3.15 已移除的 API；`re.match()` 仅软弃用，保留它以兼容最低支持版本。

独立 CI job `python-315-compatibility` 精确断言 rc3 版本，用同一锁文件执行 HTTP/Pydantic
契约检查、带原有覆盖率门槛的全量后端测试，以及隔离 PostgreSQL 18 集成测试；
`DeprecationWarning` 和 `PendingDeprecationWarning` 均作为错误处理。原有 3.14 完整基线
继续负责静态检查、前端构建、依赖审计与其他质量门禁。

本机已在隔离的 macOS arm64 rc3 环境安装全部 138 个开发/运行依赖，其中 `hiredis`、
`inflate64`、`pybcj`、`pyppmd` 和 `grimp` 从源码构建成功。原生 Redis/HTTP 解析器、
7z 的 LZMA2/PPMd/Deflate64/Brotli/BCJ 编解码和 uvloop 任务创建均通过功能验证。
rc3 全量后端测试在严格弃用检查下为 2359 项通过、1 项跳过、126 个 subtest 通过，
分支覆盖率 87.47%，高于原有 86.70% 门槛；保留一个既有 Discord mock 的未等待协程
`RuntimeWarning`。独立临时 PostgreSQL 18.4 集成检查 18 项通过，测试后已停止实例。
Python 3.14 完整质量基线通过：同样 2359 项测试通过，分支覆盖率 87.48%，分域覆盖率
91.25%；前端测试、lint/typecheck/build、40 页包体预算和依赖审计均通过。
新增 workflow 通过 actionlint 1.7.12 检查，3.14 Linux musl 的带哈希生产依赖通过
仅二进制安装 dry-run；该 dry-run 只证明依赖解析与 wheel 可用性。
这些结果不能替代 Linux glibc CI 或 Alpine 生产镜像认证；后者不在本次升级范围内。

本地验证必须使用独立的 `UV_PROJECT_ENVIRONMENT` 和精确 rc3 解释器，避免替换现有
`.venv`。如 uv 自带下载目录尚未列出 rc3，可通过 `--python-downloads-json-url`
指定 [uv 官方最新目录](https://raw.githubusercontent.com/astral-sh/uv/main/crates/uv-python/download-metadata.json)，
安装后核对 `sys.version_info == (3, 15, 0, "candidate", 3)`。没有 cp315 wheel 的扩展
允许正常源码构建；不得使用忽略 Python 版本限制、强制覆盖 core 或忽略测试失败的参数。

## 更新流程

1. 核对最新稳定版本及上游兼容约束，更新 `pyproject.toml` 下限后运行
   `uv lock --upgrade` 和 `uv export --no-dev --no-emit-project --format requirements-txt -o requirements.txt`；
2. 前端先更新 `frontend/package.json` 中的精确版本，再在 `frontend/` 内运行
   `npm update --package-lock-only && npm ci`，同步更新兼容范围内的传递依赖；
3. 执行 `uv run python scripts/check_baseline.py`，确认锁文件、测试和审计一致；
4. 生产升级前先在 PostgreSQL 18、Redis 8 Compose 环境做健康启动和回滚演练。
