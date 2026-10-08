# Cache Components 全站迁移验收记录

状态：实施中；正式 `cacheComponents`、`partialPrefetching` 尚未开启。
目标和门禁以本次用户计划及 [隔离设施说明](../frontend/instant-nav.rig.md) 为准。

## 已实现与验证

- 迁移前提交：`11643908f470acc30223729d7cc5c924f9003024`；38 个页面、18 个
  Route Handler。原根布局读取 cookie/header 决定语言，控制台布局等待认证与
  公告，阻塞初始外壳。
- 本地提交 `01fe710` 建立隔离设施；`5da5623` 完成页面和 Handler 迁移。
  页面迁入 `[locale]`，Proxy 按原语言优先级内部重写，规范化公开路径后检查
  认证。API、JSON、上传和流路径保留原地址；语言导航保持查询参数及 fragment。
- `next/root-params` 提供语言，公开路径类型来自 `next typegen`，Server Actions
  失效映射到两个内部语言路径。保留 namespace 裁剪及监控词典合并。
- 外壳、通用标题和已有骨架先显示；身份、权限、公告、活动托盘及业务数据分别
  加载。认证仅使用 React 请求内去重，业务请求保持 `no-store`、HttpOnly JWT
  及后端逐请求鉴权。18 个 Handler 移除不兼容配置，保留取消语义。
- 最新候选 `candidate-14` 的生产矩阵 **360 项通过**：38 页 × 两类导航 × 两种
  语言 × 两种宽度，另含 52 项兼容性、权限、租户、语言、404、错误恢复、保存
  及交互检查。活动托盘、multipart 上传、助手 SSE、实时终端实际连接通过；
  切页后流与 socket 归零，浏览器没有未处理异常。
- 概览差分验证：只恢复顶层 `await getSession()` 后，同一 `instant()` 检查因
  外壳标记缺失而失败；恢复拆分后通过。动态业务数据在测试锁下保持阻塞，释放
  后显示实际内容，测试不能通过空外壳。
- 测试 API 关闭的候选生产构建通过原 bundle 门禁（`candidate-production-14`
  最大路由 **258,956 bytes**）；
  原路由上限 259,072 bytes、chunk 上限 153,600 bytes 保持不变。
  CI 新增独立 production-navigation job，运行生产导航矩阵及原 bundle 门禁。
- `uv run python scripts/check_baseline.py` 完整通过，日志为
  `/tmp/upkk-cc-navigation-fixes-baseline.log`。本次真实 Link 点击复核修复了
  提示控件在 hydration 前改写 HTML 属性，以及管理员菜单忽略更具体公共子路径
  的问题。提示由 React 状态渲染，选中判定包含完整导航项；现有 13 项工作区
  回归通过，100ms 点击反馈门禁保持通过。认证延迟时的导航复核发现晚到的 SSR
  选中样式与当前客户端路径不同，出现 hydration 警告及双选中；当前使用稳定
  初始样式，在 hydration 后更新选中状态；同一认证延迟检查由两项桌面失败变为
  双语言/宽度四项通过，console/pageerror 为空。手机菜单也在 hydration 完成
  后启用，避免早期点击丢失。最新矩阵通过；受影响的 lint、typegen/typecheck、
  unit、默认生产 build/bundle 通过。保留 Next Activity 时测试定位当前可见
  配置页。18 个 Handler 逐个核对上游释放，另连续 20 次取消检查通过。

## 独立 Next RSS 门禁

原始构建和样本位于 `/Volumes/Ti7100Plus/github/.upkk-cc-builds`，不进入开发
`.next` 或 Git 提交。Linux arm64 容器 `upkk-cc-acceptance` 固定 Node 26.10.0、
Next 16.4.0、Playwright 1.63.0；迁移前与候选使用同一容器、依赖、fixture 和
负载脚本。RSS 与一般生产构建关闭测试 API。

- macOS 短测仅用于调试负载；不作为 Linux 对照或内存验收。
- 第一轮 Linux 长测预热只覆盖 462/500 ID，已中止并保留样本。
- 第二轮长测预热及正式阶段均覆盖 500 ID，但出现 **202 个导航错误**，正式
  样本仅 **718 个**，低于要求的 720 个。流最终全部释放，仍不能通过验收。
  原始失败文件保留在 `rss-baseline-full-v2-failed` 与
  `rss-baseline-full-v2-round-1.json`；后续无效轮次已停止。
- 错误日志显示旧 tab 累积未结束的导航及预取请求。负载保留 30 个独立浏览器
  上下文，每次访问结束后关闭 tab，下一次在同一 cookie 上下文创建 tab；采样
  使用绝对时间调度，错误实时落盘。任何完整轮次不通过即停止后续轮次，保留
  已有原始结果，不放宽阈值。
- 修订后的短测零错误、采样约每 5 秒一次；语法、ESLint、RSS 判定单元测试和
  `git diff --check` 通过。短测不能替代三轮各 65 分钟的对照及候选长测。
- 继续保留 `rss-baseline-full-v3-failed`、`rss-baseline-full-v4-failed`、
  `rss-baseline-full-v5-failed`。v4 的两阶段均覆盖 500 ID，但约 2,023 秒时
  导航超时，只有 345 个正式样本，12 个错误，全部流最终释放；因此不完整。
  v5 在预热发生 1 个超时，独立 HTTP 探测仍返回 200/204,800 bytes，进程
  诊断显示 Next build 和 TypeScript 编译与负载重叠，不能作为隔离对照。
- 当前负载固定每上下文 15 秒访问，采样仍为每 5 秒。正常页面等待文档完整
  加载，正式阶段使用真实工作区 Link 发起/取消导航。首个错误直接停止，避免
  诊断等待期间继续堆积负载；关闭上下文和流后保留不完整结果。新增 HTTP /
  进程诊断及构建重叠、测试 API 检查。两个版本共用同一脚本和固定 fixture，
  改动后重新测量，不能混用旧负载结果。完整长测期间不运行其他构建或回归。
- 最新候选为容器内 `/acceptance/candidate-current-4`，对应已通过功能检查的
  页面源码；两项候选特性开启、测试 API 关闭。六轮完整结果及 5% 比较仍待完成。
- 构建和其他回归结束后的 v6 在约 1,580.6 秒发生 1 个
  `page.waitForLoadState` 超时；两阶段均覆盖 500 ID，独立 HTTP 返回
  200/200,973 bytes（约 2,935ms），全部流最终释放。原始文件保留在
  `rss-baseline-full-v6-failed`，仍是不完整结果。剩余问题是浏览器主文档未在
  15 秒内结束；当前证据不足以认定为应用内存泄漏，需继续定位文档/导航生命周期。

## 剩余验收

- 完成修订负载的完整预热覆盖核对，完成两个版本各三轮长测、连接释放与
  三轮 p95 中位数/每轮持续增长判定。所有门禁仍为 1.05，不以短测替代。
- 开发浏览器完成 152 次遍历（38 页 × 双语言 × 两种宽度），语言全部匹配，
  MCP 编译/运行错误列表为空。真实侧栏点击已复核上述修复，浏览器 console
  原始提示属性及认证延迟导致的选中样式警告已修复，生产回归通过。根路径按
  既有契约重定向，开发校验器会报告
  `NEXT_REDIRECT` 无法验证目标段；生产测试验证实际落点，未用 `instant=false`
  绕过校验。原始记录为 `dev-sweep-current.json` 及 `/tmp/upkk-cc-nav-*.json`。
- 上述门禁全部通过后才默认开启两项特性，执行最终完整质量基线，更新现役
  规范与验收记录并本地提交。不推送、发布或重启线上服务。

生产矩阵、差分和基线日志保留在 `/tmp/upkk-cc-*.log`。当前状态不是任务完成。
