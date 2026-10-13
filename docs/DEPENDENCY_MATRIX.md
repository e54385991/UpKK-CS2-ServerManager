# 依赖与运行时矩阵

Python 依赖约束由 `pyproject.toml` 声明，解析结果由 `uv.lock` 锁定；前端使用
`frontend/package-lock.json`。`requirements.txt` 是从 `uv.lock` 生成的带哈希生产导出，
不得独立升级其中的包；开发环境使用 `uv sync --dev`。

| 类别 | 当前基线 | 维护方式 |
| --- | --- | --- |
| Python | 最低 3.15；开发 / CI / Docker 统一 3.15.0 正式版、普通 GIL | `.python-version`、Docker 精确版本及摘要、`pyproject.toml` 与带哈希生产导出同步 |
| uv | Docker `0.13.0-alpine`（digest 钉死） | 只用于镜像构建，运行镜像不保留 uv |
| FastAPI / Starlette | `>=0.143.0` / `>=1.7.0` | 保持上游兼容约束 |
| SQLAlchemy / SQLModel | `>=2.1.4` / `>=0.0.48` | PostgreSQL 主路径，短事务 |
| PostgreSQL | Compose `18.6-alpine` | 健康检查后启动，Alembic 自动升级 |
| Redis | Compose `8.10.2-alpine3.23` | 保持 Redis 7 协议兼容，pipeline/MGET |
| Caddy | Compose `2.11.7-alpine`（digest 钉死） | 仅 `--profile edge` / 1Panel 公网入口 |
| HTTP | 生产 `httpx>=0.28.1` | 应用级共享 transport |
| Starlette 测试客户端 | `httpx2>=2.13.1`（开发） | 仅用于测试兼容层 |
| SSH | `asyncssh>=2.24.1` | 显式 lease 和连接池 |
| Node.js | 26 Current（Docker / CI `26.11.1`） | 前端镜像固定 Alpine 3.24 多架构摘要 |
| 前端控制台 | Next.js 16.4.0、React 19.3.0、next-intl 4.14.9 | `frontend/package-lock.json`，TypeScript 6.0.3、ESLint 9.39.5 |

关键安全包当前下限为 `boto3>=1.43.112`、`cryptography>=50.0.2`、`webauthn>=3.0.1`。Dependabot 每周检查
uv、npm（`frontend/`）、两个 Dockerfile、Docker Compose 和 GitHub Actions；补丁/次版本合并分组，主版本单独 PR。TypeScript 7 超出 `typescript-eslint` 当前支持范围；ESLint 10 超出 React、import 和 JSX accessibility 插件声明的 peer 支持范围，因此两项主版本仍保持忽略；架构检查使用 `grimp>=3.17,<4.0.0` 与 `import-linter>=2.15,<3.0.0`。

SQLModel `0.0.45` 起默认把普通 `datetime` 映射为要求带时区的 `UTCDateTime`（`TIMESTAMP WITH TIME ZONE`）。现有 Alembic schema 把这些列存为 naive UTC 的 `TIMESTAMP WITHOUT TIME ZONE`，因此表模型里的普通 `datetime` 字段都显式声明 `sa_type=DateTime(timezone=False)`，需要带时区的列继续使用 `sa_column=Column(DateTime(timezone=True))` 并配套迁移；`tests/test_postgresql_migrations.py` 会拒绝任何隐式 `UTCDateTime` 列并锁定带时区列清单。SQLModel 0.0.48 已放宽到 `SQLAlchemy<2.2.0`，当前锁定 SQLAlchemy 2.1.4；保留现有时区映射与 Alembic schema。

## Python 3.15 正式镜像与稳定依赖更新（2026-10-13）

[官方镜像清单](https://raw.githubusercontent.com/docker-library/official-images/master/library/python)
及 Docker registry 已提供 `python:3.15.0-alpine3.24` 正式版，amd64 / arm64 多架构摘要为
`sha256:1afb480ba8d3b3b7a8439a84f2365ce5c75ac45a79cf07daa091b49235cbe27b`。
开发、主 CI 和容器统一为普通 GIL 的 CPython 3.15.0；结束现役 RC 例外，容器契约检查
要求正式版且与 `.python-version` 精确一致。保留独立原生扩展编译阶段及精简运行镜像。

重新核对所有直接依赖并全量执行 `uv lock --upgrade`，更新 7 个 Python 锁定包：
Google Auth 2.62.0、Boto3 / Botocore 1.43.112、py7zr 1.1.4、pre-commit 4.7.0、
texttable 1.7.1 和 truststore 0.10.5。`requirements.txt` 由现有导出脚本重新生成。
前端正常解析锁文件，更新 14 个版本条目，包括 PostCSS 8.5.30、typescript-eslint
系列 8.71.2、Zod 4.6.7 和 electron-to-chromium 1.5.453。

TypeScript 6.0.3 / ESLint 9.39.5 继续保留：重新查询 npm 元数据后，当前
`@typescript-eslint/parser@8.71.2` 仍要求 TypeScript `<6.1.0`，React、import 和
JSX accessibility 的最新插件仍只声明支持到 ESLint 9，不使用强制 peer 安装或 override。
完整 npm 审计仍有 braces 3.0.3 开发依赖链的 5 个 high 条目，其最新稳定版尚无修复。

uv 0.13.0、Node.js 26.11.1、PostgreSQL 18.6、Redis 8.10.2、Caddy 2.11.7、
所有 Actions 和 pre-commit hooks 仍为核对时的最新稳定版；现用镜像摘要与 registry
一致。Dependabot 补齐根目录及 `frontend/` 的 Dockerfile 更新检查，保持现有每周计划、
版本与摘要固定策略及合并前质量门禁。下方带日期的 RC 验收记录保留原事实。

本轮验证：

- 隔离工作区的完整 `uv run python scripts/check_baseline.py` 退出码 0；后端
  2411 项测试、126 个 subtest 通过，1 项跳过，分支覆盖率 87.52%，分域 91.25%。
  BasedPyright 零错误/警告；前端 160 项单元测试、lint、typecheck、生产构建和 41 页
  包体预算通过，Python / npm 生产依赖审计均无已知漏洞。
- 两个架构的后端与前端均实际构建并运行：精确断言 CPython 3.15.0 正式版、普通 GIL，
  确认运行镜像没有编译器、uv、pip、ensurepip；Redis / HTTP 原生解析、五种 7z
  编解码和 uvloop 通过。独立临时 Compose 栈的数据库、Redis、后端、前端及 Caddy
  代理通过健康检查，启动自动迁移到 `0036_cs2_version_state`。
- Linux arm64 正式版容器全量后端为 2411 项通过、126 个 subtest 通过、1 项跳过，
  覆盖率 87.51%；独立 PostgreSQL 18.6 集成 18 项通过，两类弃用警告继续按错误处理。
- 四组现有 Playwright 门禁均通过：教程 1、概览 18、性能/导航 57、监控/设置 14。
  `git diff --check`、actionlint 和现役版本/历史记录一致性检查通过。
- 独立候选生产构建及关闭测试 API 的生产包体预算通过。执行
  `CC_BUILD_ROOT=/tmp/upkk-python315-stable-20261013/cc-candidate npm run test:cache-components`
  得到 360 项通过、4 项失败：既有提交 `5326b0f` 新增的描述任务国际化断言在整个托盘
  查找“已完成”，将同名标签页也计入，实际 3 处而预期 2 处。应用任务行和详情状态均
  正确显示；测试范围修正待单独授权，提交与 Docker Hub 发布保持待验收状态。
- 保留既有 Discord mock 的 `RuntimeWarning`；完整 npm 审计仍为 5 个开发依赖 high。
  TypeScript / ESLint 继续遵守上游兼容限制，未通过强制解析扩大版本范围。

## Python 3.15 镜像与全仓库更新（2026-10-12）

重新查询官方 Docker registry：`python:3.15.0-alpine3.24` 仍返回 404。
按本次明确授权，容器临时使用[官方镜像清单](https://raw.githubusercontent.com/docker-library/official-images/master/library/python)
中的 `3.15.0rc3-alpine3.24`，固定实际存在的 amd64 / arm64 多架构摘要。
开发与主质量基线继续固定普通 GIL 的 Python 3.15.0 正式版；这项 RC 例外不扩展到依赖包。
正式镜像发布后应重新核实摘要并通过相同容器检查替换，不能把 RC 称为正式版。

全量重新解析 Python 依赖，更新 20 个包：主要包括 FastAPI 0.143.0、Pydantic 2.14.1
及其精确要求的 core 2.50.1、SQLAlchemy 2.1.4、Google Auth 2.61.0、Boto3 1.43.111
和 Ruff 0.17.0。aiohttp 3.14.5 将 multidict 的上限放宽到 `<8.0`，因此同步升级到 7.1.0，
不继续保留已经失效的旧限制。生产导出由现有脚本生成，包含锁定版本、平台 marker 和哈希。

前端直接依赖更新到 lucide-react 1.55.0、Playwright 1.64.0 和 @types/node 26.6.5；
正常 npm 解析共更新 34 个锁文件条目，包含平台二进制和传递依赖。
TypeScript 6.0.3 / ESLint 9.39.5 的上游兼容限制重新核对后仍成立，保留它们并不代表
绝对最新主版本已经可用。uv 升到 0.13.0、Node.js / CI 升到 26.11.1；
setup-uv v10.3.0 与 setup-node v7.1.0 固定官方完整提交 SHA，Ruff hook 同步到 0.17.0。
PostgreSQL 18.6、Redis 8.10.2、Caddy 2.11.7 和其他 Actions / hooks 仍为核对时的最新稳定版本。

Python 3.15 的纯二进制 musllinux 安装预检因 Brotli 缺少适用 wheel 失败，不能沿用
3.14 镜像中“所有扩展都有 wheel”的假设。后端改为独立的依赖构建阶段，使用带哈希导出
编译缺少 wheel 的扩展，并将生产虚拟环境复制到相同基础镜像；最终镜像保留必要 C++
运行库，不保留编译器、uv、pip 或开发依赖。pip / ensurepip 清理按解释器实际路径执行。

Compose 健康 CI 在 amd64 / arm64 原生 runner 上构建当前源码的两个镜像，核对容器
解释器版本和 GIL，并在同一基础运行时执行带原覆盖率门槛的全量后端及 PostgreSQL
集成检查。正式版完整质量基线继续维护所有原门禁，弃用警告仍按错误处理。

本轮重新执行的完整 npm 审计仍报告 braces 开发依赖链的 5 个 high 条目；最新稳定版
braces 3.0.3 尚无对应修复版本。生产审计与完整验证结果须分别记录，不把这项限制隐藏
为“全部依赖无漏洞”。下方带日期章节保留当时的版本和验收事实。

macOS 27 本机源码构建 grimp 时遇到 `mis-aligned LINKEDIT string pool`，
重新构建仍可复现；未剥离的构建可正常加载，与
[Rust 已报告的 Mach-O 剥离对齐问题](https://github.com/rust-lang/rust/issues/157750)一致。
本机验证环境使用以下限定到一次构建的参数恢复该开发工具，不修改锁定版本、
不绕过架构门禁，也不把 macOS 参数带入 Linux 运行镜像：

```bash
RUSTFLAGS='-C strip=none' CARGO_PROFILE_RELEASE_STRIP=none \
  uv sync --locked --reinstall-package grimp --no-binary-package grimp --no-cache
```

### 本轮验证结果

- 在只包含本次升级改动的隔离工作区执行完整
  `uv run python scripts/check_baseline.py`，退出码 0：Python 3.15.0 正式版后端
  2411 项测试、126 个 subtest 通过，1 项跳过；分支覆盖率 87.51%，分域覆盖率 91.25%。
  BasedPyright 零错误/警告，架构、契约、锁文件、带哈希导出和全部其他门禁通过。
- 前端 7 项源码契约、159 项单元测试及 lint / typecheck / build 通过，41 页包体预算
  通过。现有四组浏览器回归全部通过（教程 1、概览 18、性能/导航 57、监控/设置 14）；
  性能组首次发现既有登录跳转断言遗漏 `next` 查询参数，经单独授权修正为同时核对
  `/login` 路径和 `/plugins` 回跳目标，受影响场景重跑通过。额外生产导航 360 项通过，
  关闭测试 API 的隔离生产构建包体预算通过，最大路由为 258957 字节。
- Python 与 npm 生产依赖审计无已知漏洞；完整 npm 审计仍保留上文的 5 个开发依赖
  high 条目。保留一个既有 Discord mock 的未等待协程 `RuntimeWarning`，未关闭弃用检查。
- amd64 / arm64 后端与前端镜像均实际构建成功，最终运行镜像确认普通 GIL 的
  Python 3.15.0rc3，且没有 gcc / g++ / uv / pip / ensurepip。两个独立名称、随机
  loopback 端口和临时数据卷的 Compose 栈全部通过 `up -d --wait`；数据库、Redis、
  后端及前端健康、登录、教程和 Caddy 代理均通过，启动自动迁移到 `0036_cs2_version_state`。
- arm64 RC 容器严格弃用检查下的全量后端测试为 2411 项通过、126 个 subtest 通过、
  1 项跳过，分支覆盖率 87.51%；随后 PostgreSQL 集成 18 项通过。正式版连接独立临时
  PostgreSQL 的集成检查也为 18 项通过。amd64 运行镜像另验证了 Redis/HTTP 原生解析、
  五种 7z 编解码、uvloop `eager_start`、bcrypt、PostgreSQL 查询和 Alembic head。
- 临时 Docker 磁盘写满及 `/tmp` 默认 noexec 曾导致容器验证失败；清理本次临时产物并
  使用允许执行的内存挂载后，arm64 全量检查重跑通过。本机 amd64 VM 在并发构建压力下
  额外全量测试耗时过长，已停止该追加检查；不将它计为通过。amd64 本地验证范围为实际
  构建、健康启动与上述原生功能检查；新增原生双架构 CI 全量测试门禁仍须由 CI 执行。

以上验证没有推送、发布、访问线上数据库或重启线上服务；无关的已有前端导航改动未纳入。

## Python 3.15 正式版升级（2026-10-10）

[Python 3.15.0](https://www.python.org/downloads/release/python-3150/) 已于 2026-10-09
正式发布。项目最低版本升至 3.15，`.python-version` 固定普通 GIL 的 3.15.0；
启动脚本、CI、Ruff / BasedPyright 与现役启动文档同步更新。

[Pydantic 2.14.0](https://pypi.org/project/pydantic/2.14.0/) 已正式支持 Python 3.15，
依赖统一为 `>=2.14.0,<3.0.0`，锁定 2.14.0 及其要求的 core 2.50.0。
移除原来的 3.14 稳定版 / 3.15 beta 分支，定向更新锁文件并重新生成带哈希生产导出，
其他依赖继续使用原锁定版本。

原 `python-315-compatibility` rc3 job 合并入正式版完整基线：保留所有质量门禁，
全量 / 分域后端测试和 PostgreSQL 集成测试继续把两类弃用警告视为错误。
不再重复执行一套 3.14 与一套 rc3 测试。

当日 Docker 官方 registry 核对时仍只有 3.15.0rc3，`python:3.15.0-alpine3.24` 返回
`not found`。当时未授权容器使用 RC，生产镜像升级仍受阻；2026-10-12 的临时例外见上文。
以下带日期的 3.14 / rc3 章节是历史记录，不再规定当前开发与 CI 版本。

## 稳定版本核对（2026-10-07）

通过 [PyPI](https://pypi.org/)、[npm registry](https://registry.npmjs.org/next/latest)、
GitHub Releases 和官方容器 registry 实时核对直接依赖、Python 锁定包、前端传递依赖、
GitHub Actions、pre-commit hooks 和基础镜像。更新 20 个 Python 包；主要变化包括
SQLModel 0.0.48、SQLAlchemy 2.1.3、AsyncSSH 2.24.1、Google Auth 2.60.0、Boto3 1.43.108、
Ruff 0.16.10 和 BasedPyright 1.40.2。前端升级到 Next.js / eslint-config-next 16.4.0、
next-intl 4.14.9、lucide-react 1.52.0、PostCSS 8.5.29 和 @types/node 26.6.4，
42 个 npm 包条目升级（包括各平台二进制包）；传递依赖通过正常 npm 解析更新，
保持精确版本和锁文件。

生产镜像升级到 Python 3.14.8、uv 0.12.23 和 Caddy 2.11.7，并固定多架构摘要。
Node.js 26.10.0、PostgreSQL 18.6、Redis 8.10.2 和所有 GitHub Actions 已是核对时的最新稳定版。
SQLModel 0.0.48 的发布元数据允许 SQLAlchemy 2.1，本轮解除旧限制。

仍保留以下经过重新核对的上游约束：

| 包 | 当前锁定 | 最新稳定 | 原因 |
| --- | --- | --- | --- |
| TypeScript | 6.0.3 | 7.0.2 | `@typescript-eslint/parser@8.71.1` 要求 `typescript >=4.8.4 <6.1.0` |
| ESLint | 9.39.5 | 10.12.0 | `eslint-plugin-react@7.37.5`、`eslint-plugin-import@2.32.0`、`eslint-plugin-jsx-a11y@6.10.2` 的 peer 范围最高为 9；React Hooks 已支持 10 |
| multidict | 6.9.1 | 7.0.0 | `aiohttp@3.14.4` 要求 `multidict<7.0` |
| pydantic-core（Python 3.14） | 2.46.5 | 2.49.0 | 最新稳定 `pydantic@2.13.5` 精确要求 core 2.46.5 |

Python 3.14 生产依赖保持稳定版；Python 3.15 兼容验证仍沿用下文已选择的
`pydantic==2.14.0b2` 临时例外，因为支持 3.15 的稳定版尚未发布。不扩大预发布解析范围。
上述保留项不代表已升级到其绝对最新版本；升级不能绕过父包和 peer 约束。

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

生产导出使用 `uv run python scripts/export_requirements.py`。uv 的普通导出将次版本
边界规范成 `python_full_version < / >= '3.15'`，而 pip 对 `3.15.0rc3` 的 PEP 440 判断
会让两条分支都不匹配。导出脚本仅将 `<` / `>=` 的两段版本边界还原成
`python_version`，保留补丁级约束、平台条件、包版本和全部哈希。回归测试用
3.14.7、3.15.0rc3 与 3.15.0 确认项目声明和生产导出都唯一选择匹配的 Pydantic/core。

`uv run python scripts/export_requirements.py --check` 只检查版本、marker 和哈希与
锁文件的完整导出是否一致，不修改文件；完整质量基线与 3.15 兼容 CI 都执行这项检查。
Dependabot 的 uv 配置通过 `exclude-paths` 排除生成的 `requirements.txt`，继续维护
`pyproject.toml` 和 `uv.lock`；依赖更新后须使用上面的导出命令同步生产文件。
否则单独升级 `pydantic-core` 会破坏 Pydantic 对 core 的精确版本约束。

[Python 3.15 默认使用 UTF-8](https://docs.python.org/3.15/whatsnew/3.15.html#other-language-changes)。
8 处质量/性能工具的子进程文本输出已明确编码：结构化输出使用 UTF-8，通用外部程序
版本信息显式使用 locale 编码。回归测试在 ASCII locale、关闭 UTF-8 模式、将
`EncodingWarning` 视为错误的独立进程内验证中文 JSON。生产代码审计未发现直接使用
3.15 已移除的 API；`re.match()` 仅软弃用，保留它以兼容最低支持版本。

独立 CI job `python-315-compatibility` 精确断言 rc3 版本，用同一锁文件执行 HTTP/Pydantic
契约检查、带哈希生产导出的依赖审计、带原有覆盖率门槛的全量后端测试，
以及隔离 PostgreSQL 18 集成测试；
`DeprecationWarning` 和 `PendingDeprecationWarning` 均作为错误处理。原有 3.14 完整基线
继续负责静态检查、前端构建、依赖审计与其他质量门禁。

本机已在隔离的 macOS arm64 rc3 环境安装全部 138 个开发/运行依赖，其中 `hiredis`、
`inflate64`、`pybcj`、`pyppmd` 和 `grimp` 从源码构建成功。原生 Redis/HTTP 解析器、
7z 的 LZMA2/PPMd/Deflate64/Brotli/BCJ 编解码和 uvloop 任务创建均通过功能验证。
rc3 全量后端测试在严格弃用检查下为 2359 项通过、1 项跳过、126 个 subtest 通过，
分支覆盖率 87.47%，高于原有 86.70% 门槛；保留一个既有 Discord mock 的未等待协程
`RuntimeWarning`。独立临时 PostgreSQL 18.4 集成检查 18 项通过，测试后已停止实例。
随后新增的 4 项导出 marker 回归检查也在 rc3 下通过；该环境的生产依赖审计无已知漏洞。
Python 3.14 最终完整质量基线通过：2363 项测试通过、1 项跳过，分支覆盖率 87.48%，分域覆盖率
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
   `uv lock --upgrade` 和 `uv run python scripts/export_requirements.py`；
2. 前端先更新 `frontend/package.json` 中的精确版本，再在 `frontend/` 内运行
   `npm update --package-lock-only && npm ci`，同步更新兼容范围内的传递依赖；
3. 执行 `uv run python scripts/check_baseline.py`，确认锁文件、测试和审计一致；
4. 生产升级前先在 PostgreSQL 18、Redis 8 Compose 环境做健康启动和回滚演练。

## 当前审计限制（2026-10-07）

生产 Python 依赖与前端生产依赖的审计以完整质量基线为准。
额外的 `npm --prefix frontend audit`（含开发依赖）报告 5 个 high 条目，
它们来自同一条 `eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch → braces`
开发依赖链。braces 3.0.3 是核对时的最新稳定版，
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) 明确尚无修复版本；
升级前的锁文件已使用该版本。本轮未降级 Next.js、强制覆盖依赖或忽略审计告警。
该问题在解析攻击者控制的深度嵌套 glob 模式时可能导致进程栈溢出，
开发工具链仍有此已知限制；不得把生产审计通过表述为所有依赖均无漏洞。

SQLAlchemy 2.1 将 `Select` / `Row` 的泛型从一个 tuple 参数改为变长列参数。
本轮同步修正概览统计查询和 Discord 双实体查询的精确类型，保持原 SQL、授权过滤和响应契约；
不使用 `cast`、`type: ignore` 或扩大 `Any` 来绕过静态检查。

Next.js 16.4 在 `agentRules: false` 时会删除 AGENTS.md 中已有的托管段落。
前端项目说明保留为普通正文并移除托管标记，避免开发服务器删除手写规则；
不启用 `cacheComponents` 或 `partialPrefetching`。

## 本轮验证结果（2026-10-07）

- Python 3.14.8 隔离环境执行 `uv run python scripts/check_baseline.py` 全部通过：
  2400 项后端测试、126 个 subtest 通过，1 项跳过，分支覆盖率 87.35%；
  分域覆盖率 91.25%，BasedPyright 零错误/警告，前端测试、lint、typecheck、
  生产构建和 40 页包体预算通过。生产 Python 与前端生产依赖审计均无已知漏洞。
- Python 3.15.0rc3 严格弃用检查下的全量后端测试同样为 2400 项通过、
  126 个 subtest 通过、1 项跳过，覆盖率 87.35%。两种环境均保留一个既有
  Discord mock 未消费 `Client.start` 协程的 `RuntimeWarning`。
- 临时 PostgreSQL 18.4 实例上的迁移集成检查，在两种 Python 环境中各 18 项通过；
  不访问线上数据库，检查后停止并删除实例。
- 完成 CI 中现有四组 Playwright 检查：公共教程 1 项、概览 18 项、
  性能/导航 51 项、监控/设置 14 项，共 84 项通过；Next MCP 编译诊断无问题。
  首轮性能场景发现新建服务器页既有的 `serverConfig` 命名空间缺失告警，
  随后的 Next.js 16.4 实践调整已补齐该命名空间，并用两种语言分别复核。
- Linux arm64 Alpine 后端镜像（Python 3.14.8）与前端镜像（Node.js 26.10.0）
  构建成功。使用已有 Colima context 创建独立名称、随机 loopback 端口、临时数据卷的
  Compose 栈；app/frontend/PostgreSQL 18.6/Redis 8.10.2/Caddy 2.11.7 均健康。
  启动自动迁移到 `0036_cs2_version_state`；Redis PING、PostgreSQL readiness、
  数据库 head 诊断及前端/Caddy 的健康、登录、教程端点均通过，随后删除全部临时容器和卷。
  未修改默认 Docker context，未推送、发布或重启线上服务。

镜像检查覆盖 Linux arm64；x86_64 musl 带哈希的纯二进制安装 dry-run 通过，
但它不等于 amd64 镜像运行认证。更新后的依赖解析、构建和测试结果不能代替线上故障取证。

## Next.js 16.4 追加验证（2026-10-07）

构建、Proxy、请求边界、导航、错误恢复、图片加载和可选开关的核对见
[前端实践说明](FRONTEND.md)。

- 在隔离 Git worktree 中，以 `.env.example` 作为进程级验证配置执行完整
  `uv run python scripts/check_baseline.py`，最终退出码为 0：2400 项后端测试、
  126 个 subtest 通过，1 项跳过；分支覆盖率 87.51%，分域覆盖率 91.25%。
  BasedPyright 零错误/警告，7 项前端源码/构建契约检查、143 项前端单元测试、
  lint、typecheck、Turbopack 生产构建和 40 页包体预算均通过。
- 四组 CI Playwright 回归共 90 项通过（教程 1、概览 18、性能/导航 57、监控/设置 14）；
  新增的中英文 404、错误后重新请求、新建服务器翻译共 6 项在生产模式中再次通过。
  正常页面的 Next MCP 编译和执行诊断无问题；错误恢复测试主动注入的服务端异常为预期日志。
- 4 个 Docker 编译命令回归覆盖显式 ID、空 ID、`unknown` 回退 Git SHA、
  两者未知时不设置 ID，同时核对编译进程收到 BuildKit 密钥。
  实际 Linux arm64 Alpine 镜像分别用显式 deployment ID 和默认 `unknown` 构建并启动：
  Server Actions manifest 的密钥与传入 secret 一致，页面资源带正确的显式 ID 或 Git SHA。
  两次隔离 Compose 栈均健康，Redis/PostgreSQL/head/HTTP 检查通过，临时容器和卷已删除。
- 过程中曾遇到 npm 审计 TLS 中断和 pip-audit 临时 pip 环境升级失败；
  原命令重试通过，最终整轮基线的两项生产依赖审计也通过，未跳过或降低门禁。
  前述开发依赖 braces 告警、上游版本兼容限制和既有 Discord mock 警告仍保留。
- 原工作区的 SSH 巡检文件仍出现回写旧版本的现象，来源未定位；
  `1a2be09` 中的取消清理修复正确，隔离工作区保持该源码并通过全部检查。
  保留隔离工作区作为已验证副本；这不代表原工作区的回写原因已修复。
