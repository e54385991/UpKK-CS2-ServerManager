# Frontend console

The operator console is the **Next.js 16.4.0** app in `frontend/`. It talks to
FastAPI through same-origin rewrites (`/api/*`, `/health`, `/static/*`).

FastAPI no longer ships a Jinja/Bootstrap HTML console. Leftover HTML paths
return 404. Plugin archives and host setup libraries still live under
`static/uploads/` and `static/linux_lib/`.

See `frontend/AGENTS.md` for stack, layout, and commands.

## Next.js 16.4 实践核对（2026-10-07）

行为依据安装包内的版本匹配文档 `frontend/node_modules/next/dist/docs/01-app/`，
包括升级、生产检查、Proxy、数据安全、自托管及错误页面章节；
版本以 [npm 稳定标签](https://registry.npmjs.org/next/latest)和锁文件为准。

| 项目 | 当前实现与依据 |
| --- | --- |
| 构建 | 开发、CI 与 Docker 均使用默认稳定 Turbopack，移除 Docker 的旧 Webpack 回退；保留 build-host 编译阶段，避免跨架构 QEMU 构建挂起。 |
| 自托管部署 | Docker 将 deployment ID 和 BuildKit 的 Server Actions 密钥直接传给 `next build`；旧命令只把变量传给前面的 `mkdir`，实际编译进程收不到。部署 ID 优先显式设置，再回退 Git SHA；`unknown` 占位符不作为统一 ID，运行镜像使用编译结果中的 ID。密钥继续使用 secret mount，不改成公开 build arg。 |
| Proxy | 使用 Next 16 的 `proxy.ts` 与默认 Node.js runtime；仅检查 cookie 是否存在，直接导入轻量 cookie helper。身份与权限仍由服务端数据访问和后端验证。 |
| 请求 API | `cookies()`、`headers()`、route params 使用异步 API；数据访问标记 `server-only`、转成 DTO，带会话凭证且使用 `no-store`，避免跨用户缓存。 |
| 导航 | 复用布局、`loading.tsx`、Suspense 和默认 Link 预取；独立数据并行加载，慢的可选信息不阻塞页面主体。 |
| 首屏图片 | 运行时确认教程第一张图为 LCP，改为 `loading="eager"`；后续截图仍懒加载。遵循当前 Image 文档，不使用已弃用的 `priority`，不预加载全部截图。 |
| 错误恢复 | 增加中英文 `error.tsx` 与 `global-error.tsx`；使用稳定 `retry()` 重新请求和渲染，避免仅重渲染旧错误 payload。该 API 自 16.3 已稳定，16.4 文档继续优先推荐它。全局错误页独立提供文档标签、样式、字体与语言文案，不依赖失败的布局或翻译 provider，不显示原始异常。 |
| 404 与翻译 | 增加中英文 `not-found.tsx` 和返回总览入口；服务器区 provider 补上 `serverConfig`，修复新建服务器字段的缺失文案。 |
| AGENTS.md | `agentRules: false` 在 16.4 会删除托管区块；手写规则保留为普通正文，避免启动开发服务器时丢失。 |

`cacheComponents` 和 `partialPrefetching` 继续遵守项目现有内存门槛，
不因升级而自动开启。16.4 的 `prefetchInlining` 已默认启用，无需重复配置；
`agentFeedback` 仍为实验功能，未启用。最佳实践按应用的鉴权、语言和部署需求采用，
不等于开启全部新开关。

浏览器回归覆盖两种语言的 404、错误后恢复且重新访问后端、新建服务器翻译；
完整质量基线及生产模式、隔离 Alpine Compose 的最终结果见
[依赖矩阵](DEPENDENCY_MATRIX.md)。
