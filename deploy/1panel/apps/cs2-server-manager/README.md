## 产品介绍

CS2 Server Manager 是一个 Counter-Strike 2 服务器管理面板：公网入口由 Caddy 反代
Next.js 控制台，FastAPI 只提供 `/api`。支持通过 SSH 远程管理多个游戏服务器，并提供
部署、启停、监控、插件和实时控制台功能。

将此目录完整复制到 `/opt/1panel/resource/apps/local/cs2-server-manager/`，确保应用根目录
同时存在 `data.yml`、`logo.png` 和 `1.0.0/data.yml`；不要只复制 `1.0.0` 目录。

## 主要功能

- Web 界面管理多个 CS2 服务器；
- SSH 远程部署、启动、停止和重启；
- Metamod:Source、CounterStrikeSharp 插件框架安装；
- Redis 状态缓存、PostgreSQL 数据持久化和 WebSocket 实时状态。
