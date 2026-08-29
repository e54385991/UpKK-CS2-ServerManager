# Docker 一键部署

准备一台安装 Docker Compose v2 的主机即可启动。Compose 文件是自包含的：只下载这一份 YAML，不需要仓库里的 Caddyfile 或源码。

```bash
mkdir cs2-manager && cd cs2-manager
curl -fsSLO https://raw.githubusercontent.com/e54385991/UpKK-CS2-ServerManager/main/docker-compose.yml
docker compose up -d
```

或一条命令（会安装 Docker、生成随机数据库密码并拉起服务）：

```bash
curl -fsSL https://raw.githubusercontent.com/e54385991/UpKK-CS2-ServerManager/main/docker-quickstart.sh | bash
```

本仓库默认使用公开镜像：

- `docker.io/e54385991/upkk-cs2-server-manager:main`（FastAPI）
- `docker.io/e54385991/upkk-cs2-server-manager-web:main`（Next 控制台）

也可用 `CS2_MANAGER_IMAGE` / `CS2_FRONTEND_IMAGE` 覆盖。Compose 会自动拉取镜像。源码目录下可用 `docker compose up -d --build` 现场构建。

默认启动 Next、FastAPI、PostgreSQL 18 和 Redis 8。浏览器只访问 Next（默认 <http://localhost:3000>，`HTTP_PORT` 可改）。FastAPI、PostgreSQL、Redis **不映射到宿主机**，避免和 1Panel 自带的 80 / 8000 / 5432 / 6379 冲突。Next 在容器网内把 `/api`、`/health`、`/static` 代理到 `http://app:8000`，不要把 `INTERNAL_API_URL` 设成局域网 IP。

应用启动时自动执行 Alembic 数据库迁移。全新数据卷首次启动时会自动创建管理员账户：

- 用户名：`admin`
- 密码：`admin123`

> ⚠️ **首次登录后请立即修改默认密码。** 如果复用已有数据卷，系统会继续使用已有账户，不会重新创建或重置默认管理员密码。

首次启动会在 `app_data` 卷中自动生成并持久化 `SECRET_KEY`、`JWT_SECRET_KEY` 和 AI 凭据加密密钥；不需要填写这些项。升级或重启时不要删除 `app_data`、`postgres_data` 或 `redis_data` 卷。

## 1Panel

不要用两个「运行环境」分别部署前后端。在 1Panel **容器 → Compose** 新建项目，粘贴本仓库根目录 `docker-compose.yml`，端口保持 `3000`（或改 `HTTP_PORT`），启动即可。访问 `http://服务器IP:3000`。

可选：`docker compose --profile edge up -d` 再挂一个 Caddy 在 `:80`（宿主机 80 已被 OpenResty 占用时不要开）。

## 常用操作

```bash
docker compose ps
docker compose logs -f frontend app
docker compose pull && docker compose up -d
docker compose down
```

调试时如需把 FastAPI / Postgres / Redis 打到宿主机：

```bash
curl -fsSLO https://raw.githubusercontent.com/e54385991/UpKK-CS2-ServerManager/main/docker-compose.debug.yml
docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d
```

源码构建：

```bash
docker compose up -d --build
```

可以通过环境变量覆盖端口、数据库密码和镜像标签。公网部署仍建议在反向代理后启用 HTTPS，并设置强数据库密码。

## 自动发布到 Docker Hub

在 GitHub 仓库设置以下 Actions Secrets（不要把 Token 提交到代码）：

- `DOCKERHUB_USERNAME`：Docker Hub 用户名或组织名；
- `DOCKERHUB_TOKEN`：Docker Hub Access Token，至少具有目标仓库的推送权限。

创建公开仓库 `upkk-cs2-server-manager` 和 `upkk-cs2-server-manager-web` 后，推送 `main` 或 `v*` 标签会自动生成 API 与 Next 控制台的 `latest`、`main`、语义化版本和提交 SHA 镜像标签。
