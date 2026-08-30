## 产品介绍

CS2 Server Manager 是一个 Counter-Strike 2 服务器管理面板：一套应用同时包含
Caddy、Next.js 控制台和 FastAPI。公网入口是 Caddy → Next；FastAPI 只在容器网内
`app:8000` 提供 `/api`。不要把前后端拆成两个 1Panel 运行环境。

将此目录完整复制到 `/opt/1panel/resource/apps/local/cs2-server-manager/`，确保应用根目录
同时存在 `data.yml`、`logo.png` 和 `1.0.0/data.yml`；不要只复制 `1.0.0` 目录。

安装后访问 `http://服务器IP:3000`（或你改过的控制台端口），默认账号 `admin` /
`admin123`，首次登录后立即改密码。**后端内部地址**保持 `http://app:8000`。
**浏览器访问地址**改成实际 origin，不要填 `0.0.0.0` 或局域网 IP 当作容器内 API。

## 主要功能

- Web 界面管理多个 CS2 服务器；
- SSH 远程部署、启动、停止和重启；
- Metamod:Source、CounterStrikeSharp 插件框架安装；
- Redis 状态缓存、PostgreSQL 数据持久化和 WebSocket 实时状态。
