# Cache Components 全站迁移验收记录

状态：实施中；正式 `cacheComponents`、`partialPrefetching` 尚未开启。
目标和门禁以本次用户计划及 [隔离设施说明](../frontend/instant-nav.rig.md) 为准。

## 迁移前与设施

- 迁移前提交：`11643908f470acc30223729d7cc5c924f9003024`；源码确认为 38 个页面、
  18 个 Route Handler。根布局通过 cookie/header 决定语言，控制台布局依次等待
  认证与公告，均会阻塞初始外壳。
- 构建目录：`/Volumes/Ti7100Plus/github/.upkk-cc-builds/baseline`。
  来源是独立 detached worktree，不读取开发 `.env`，Next 16.4.0 生产构建成功。
- 本机环境：Darwin arm64、Node 26.8.2；测试原始数据保存在同一隔离目录下的
  `rss-smoke-baseline-*` 子目录。原始样本不会混进开发 `.next` 或 Git 提交。
- `@next/playwright@16.4.0` 已锁定。构建设施会检查实际 Next 配置；生产导航
  测试锁、已知阻塞案例和撤销/恢复验证尚未通过，不能宣称即时导航成立。
- RSS 分析器测试通过：恰好三轮、5% 临界值、每轮持续增长、缺失数据集、错误 PID、
  未释放流、不同运行时/主机/fixture/负载代码均按严格条件判定。
- 负载预检发现并发导航及资源完成超时，最新短测仍有 14 个错误。
  短测仅用于调试，三轮各 65 分钟的迁移前和候选测量尚未开始；没有内存通过结论。
- 既有生成产物被源码门禁误扫描，已仅排除 Next 产物、Playwright 报告和隔离目录，
  并补充行数扫描测试；800/1200 行、类型、bundle 等阈值保持原值。无对应开发
  服务使用的旧 `.next/dev/types` 已移动到隔离目录保留，随后重新生成当前路由类型。
- `uv run python scripts/check_baseline.py` 已完整通过；随后只调整独立负载/分析
  脚本和记录，并重新通过 ESLint 与分析器单元测试。

## 待验收

国际化与 Proxy、共享布局、38 页细粒度 Suspense、18 个 Route Handler 的请求/取消
兼容性、语言导航与双语言失效、生产首次访问/Link 导航、权限/租户/语言/错误恢复、
活动托盘/助手 SSE/上传/实时控制台、开发 Insights、CI 导航回归及完整 RSS 长测均待完成。
全部功能与内存门禁通过后，才默认启用两项特性并执行最终完整质量基线。
