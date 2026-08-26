# Docker 一键部署

生产镜像发布到 Docker Hub 后，准备一台安装 Docker Compose v2 的主机即可启动：

```bash
mkdir cs2-manager && cd cs2-manager
curl -fsSLO https://raw.githubusercontent.com/e54385991/UpKK-CS2-ServerManager/main/docker-compose.yml
docker compose up -d
```

发布者需要先把 Compose 文件中的 `your-dockerhub-user` 替换为自己的 Docker Hub
命名空间；也可以通过 `CS2_MANAGER_IMAGE` 环境变量覆盖镜像地址。运行者不需要手工执行
`docker pull`，Compose 会自动拉取镜像。

默认会启动应用、PostgreSQL 18 和 Redis 8，应用启动时自动执行 Alembic 数据库迁移。
访问 <http://localhost:8000> 后直接注册第一个账户。

首次启动会在 `app_data` 卷中自动生成并持久化 `SECRET_KEY`、`JWT_SECRET_KEY` 和 AI
凭据加密密钥；不需要填写 `.env`。升级或重启时不要删除 `app_data`、`postgres_data`
或 `redis_data` 卷。

## 常用操作

```bash
docker compose ps
docker compose logs -f app
docker compose pull app && docker compose up -d app
docker compose down
```

如果需要从源码构建而不是使用 GHCR 镜像：

```bash
docker compose up -d --build
```

可以通过环境变量覆盖端口、数据库密码、公开访问地址和镜像标签；默认值适合单机首次
安装，但公网部署仍建议在反向代理后启用 HTTPS，并设置强数据库密码。

## 自动发布到 Docker Hub

在 GitHub 仓库设置以下 Actions Secrets（不要把 Token 提交到代码）：

- `DOCKERHUB_USERNAME`：Docker Hub 用户名或组织名；
- `DOCKERHUB_TOKEN`：Docker Hub Access Token，至少具有目标仓库的推送权限。

创建公开仓库 `upkk-cs2-server-manager` 后，推送 `main` 或 `v*` 标签会自动生成
`main`、语义化版本和提交 SHA 镜像标签。当前发布工作流使用完整 commit SHA 锁定的
官方 Docker Actions。
