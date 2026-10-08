# Cache Components 隔离验收设施

本次迁移前版本：`11643908f470acc30223729d7cc5c924f9003024`。
Next 与 `@next/playwright` 均固定为 `16.4.0`，Playwright 为 `1.63.0`。

构建使用 `scripts/prepare-cache-components.mjs`，复制前端源码和同一套依赖到
独立新目录，排除 `.env*`、`.next`、开发产物及浏览器登录状态。不能以开发环境
的 `.next` 运行测试。迁移前源码来自该提交的 detached worktree。
`acceptance/build-input.json` 与 `.next/BUILD_ID` 标识实际受测构建。

```bash
git worktree add --detach /tmp/upkk-cc-baseline 11643908f470acc30223729d7cc5c924f9003024
node scripts/prepare-cache-components.mjs /tmp/upkk-cc-baseline/frontend /tmp/upkk-cc-build-baseline baseline
node scripts/prepare-cache-components.mjs "$PWD" /tmp/upkk-cc-build-candidate candidate
```

候选测试构建约定传入 `CC_TEST_BUILD=1` 和 `CC_CANDIDATE_BUILD=1`，导航测试
另传 `CC_EXPOSE_TEST_API=1`。构建后读取实际 Next 配置核对这三项；候选迁移
尚未满足构建条件时会失败，不能只靠测试声明宣称通过。
只有导航测试构建暴露 `experimental.exposeTestingApiInProductionBuild`；一般生产
构建和 RSS 构建均关闭测试锁。测试必须证明已知阻塞组件在 `instant()` 下无法显示，
修复后有意义的外壳出现，动态内容仍被阻塞；撤销同一修复再次失败，恢复后通过。
`instant()` 不以耗时判定，首次访问和真实 Link 导航分别检查。

专用 mock 后端为 `e2e/cache-components.mock.mjs`，绑定 `127.0.0.1:38261`；
Next standalone 为 `127.0.0.1:31961`。RSS 浏览器以 `cache-components.test:31961`
访问，并通过 Chromium resolver 直接映射到 loopback、关闭系统代理。测试不会
复用已存在的服务。开发遍历另用
独立构建目录与端口，不以开发导航结果充当生产证据。

固定测试 JWT 为 `fixture-admin-a`、`fixture-member-a`、`fixture-admin-b`、
`fixture-member-b`，只被 loopback mock 接受。浏览器写入 HttpOnly
`upkk_access_token`，Next 仍将它作为 Bearer 转发。奇数服务器属于租户 a，
偶数服务器属于租户 b；范围为 1–500。无 cookie、伪造及过期 token 返回 401。
语言为 `en-US`、`zh-CN`，视口为 390×844、1440×844。

全站页面队列由 `e2e/helpers/cache-components-surfaces.mjs` 定义；应与源码
38 个页面逐一核对。测试入口使用真实 Link，不能用 URL 修改或模拟点击代替。
管理员权限、语言、注册配置、页面标记和 mock 故障规则是需要固定的差异来源。

RSS 使用 `scripts/cache-components-rss.mjs`，直接启动 standalone `server.js` 并
每 5 秒读取该 PID 的当前 RSS，不测 npm wrapper、峰值 rusage 或后端 RSS。
5 个 Chromium 进程承载 30 个独立浏览器上下文，包含四类账号、两种语言和两种
宽度，固定轮转完整 500 个
服务器 ID，并反复读取、取消活动队列和助手 SSE，正式负载加入文档导航取消。真实 Link
导航由独立功能矩阵覆盖。
每个上下文按固定 30 秒迭代节奏执行，服务器 ID 按同租户上下文分片，确保
预热完整覆盖 500 个 ID。每次访问结束后关闭 tab，再在同一 cookie 上下文创建
tab，完成请求读者与预取的取消，避免旧 tab 的未结束请求积累。采样按绝对时间
每 5 秒调度，错误实时落盘；两个版本共用相同负载代码和 fixture。
正常访问等待文档完整加载；正式阶段从真实工作区 Link 发起导航并取消。
完整模式首个错误即停止，关闭上下文及流后落盘；超时另记录独立 HTTP 探测和
进程信息。运行前必须结束同一隔离主机上的构建和其他浏览器回归，不能与长测
重叠；脚本拒绝启用了测试 API 的构建，并检查同进程环境中的 Next 构建进程。

```bash
node scripts/cache-components-rss.mjs /tmp/upkk-cc-build-baseline /tmp/upkk-cc-rss-baseline full
node scripts/prepare-cache-components.mjs "$PWD" /tmp/upkk-cc-build-candidate-memory candidate memory
node scripts/cache-components-rss.mjs /tmp/upkk-cc-build-candidate-memory /tmp/upkk-cc-rss-candidate full
node scripts/cache-components-rss-report.mjs /tmp/upkk-cc-rss-baseline /tmp/upkk-cc-rss-candidate /tmp/upkk-cc-rss-verdict.json
```

两个版本分别三轮；每轮预热 300 秒、测量 3600 秒。不得同时运行两个负载版本。
原始 NDJSON、完整 JSON、Next 日志、构建标识、连接和取消计数落在报告目录。
预热和测量必须分别访问完整数据集。三轮 RSS p95 的中位数回归上限为 1.05，
每轮最后 5 分钟与预热后首 5 分钟的 RSS 中位数之比上限也为 1.05。
缺失轮次、时长、样本、服务器、错误 PID、未释放流或浏览器异常均不能通过。
任何完整轮次失败即停止后续轮次并保留结果；修复具体原因后重测，不能用不足
三轮的结果通过门禁，也不能混用不同负载代码的对照和候选结果。

`smoke` 模式只验证负载设施能运行（10 秒预热、30 秒测量），输出强制标记为
不完整，不能批准开启正式默认。完整内存和功能门禁均通过后，才开启两项默认。

Linux 实际验收使用独立容器 `upkk-cc-acceptance`，镜像
`mcr.microsoft.com/playwright:v1.63.0-noble`（digest:
`sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27`），
固定 Node 26.10.0 Linux arm64。两个版本在同一容器、同一安装依赖下重建，
进程和 mock 只绑定容器 loopback；采样读取容器内实际 Next PID。原始报告通过
`docker cp` 保留到隔离主机目录。不得以 macOS 调试失败样本作为 Linux 对照。
