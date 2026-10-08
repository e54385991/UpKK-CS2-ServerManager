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
- 最新候选 `candidate-10` 的生产矩阵 **352 项通过**：38 页 × 两类导航 × 两种
  语言 × 两种宽度，另含 44 项兼容性、权限、租户、语言、404、错误恢复、保存
  及交互检查。活动托盘、multipart 上传、助手 SSE、实时终端实际连接通过；
  切页后流与 socket 归零，浏览器没有未处理异常。
- 概览差分验证：只恢复顶层 `await getSession()` 后，同一 `instant()` 检查因
  外壳标记缺失而失败；恢复拆分后通过。动态业务数据在测试锁下保持阻塞，释放
  后显示实际内容，测试不能通过空外壳。
- 测试 API 关闭的候选生产构建通过原 bundle 门禁，最大路由为 **258,933 bytes**；
  原路由上限 259,072 bytes、chunk 上限 153,600 bytes 保持不变。
  CI 新增独立 production-navigation job，运行生产导航矩阵及原 bundle 门禁。
- `uv run python scripts/check_baseline.py` 完整通过，日志为
  `/tmp/upkk-cc-final-migration-baseline.log`。此后仅修改独立负载脚本和记录，
  未重复运行已覆盖且未受影响的应用检查。

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
- 修订后的基线测量使用 `rss-baseline-full-v3`；候选构建为容器内
  `/acceptance/candidate-current`，两项候选特性开启、测试 API 关闭。
  当前尚无六轮完整结果与 5% RSS 比较结论。

## 剩余验收

- 完成修订负载的完整预热覆盖核对，完成两个版本各三轮长测、连接释放与
  三轮 p95 中位数/每轮持续增长判定。所有门禁仍为 1.05，不以短测替代。
- 开发浏览器已遍历 31 个受保护页面，Next MCP `get_compilation_issues` 为空；
  仍需补全公共页面、中文遍历及取消异常复核。
- 上述门禁全部通过后才默认开启两项特性，执行最终完整质量基线，更新现役
  规范与验收记录并本地提交。不推送、发布或重启线上服务。

生产矩阵、差分和基线日志保留在 `/tmp/upkk-cc-*.log`。当前状态不是任务完成。
