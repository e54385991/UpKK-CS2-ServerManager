# 1Panel 部署 / 1Panel Deployment

本文提供两种部署路径：根目录 Docker Compose 继续自带 PostgreSQL 和 Redis；1Panel 本地应用包只部署 CS2 Server Manager，并复用 1Panel 已安装的数据库服务。

This guide covers two deployment paths: the root Docker Compose keeps its bundled PostgreSQL and Redis, while the 1Panel local app package deploys only CS2 Server Manager and reuses services already installed by 1Panel.

## 中文

### 适用版本

- 1Panel 当前版本，已启用 Docker 和 `1panel-network`；
- PostgreSQL 18.6（应用要求 PostgreSQL 18 或更高）；
- Redis 8.10.1（Redis 7 及以上协议兼容）。

1Panel 应用商店当前提供 [PostgreSQL 18.6](https://github.com/1Panel-dev/appstore/tree/dev/apps/postgresql/18.6-alpine) 和 [Redis 8.10.1](https://github.com/1Panel-dev/appstore/tree/dev/apps/redis/8.10.1)。

### 1. 准备外部服务

1. 在 1Panel 应用商店安装并启动 PostgreSQL 18.6 和 Redis 8.10.1。
2. 在 PostgreSQL 管理界面创建专用数据库、用户和密码，例如：
   - 数据库：`cs2_manager`
   - 用户：`cs2_manager`
   - 权限：仅授予该数据库所需权限，不使用超级用户。
3. 记住 Redis 密码和使用的 DB 编号。Redis 无密码时，安装表单中的密码保持为空。
4. 确认 PostgreSQL、Redis 和待安装应用都连接到 1Panel 的 `1panel-network`。

不要填写 `localhost` 作为容器内的数据库地址；安装表单应选择对应的 1Panel 服务实例。容器内的 `localhost` 指向应用容器本身。

### 2. 导入本地应用包

在项目仓库目录执行：

```bash
sudo mkdir -p /opt/1panel/resource/apps/local/cs2-server-manager
sudo cp -a deploy/1panel/apps/cs2-server-manager/. \
  /opt/1panel/resource/apps/local/cs2-server-manager/
# 确认应用根目录和版本目录都包含 data.yml
test -f /opt/1panel/resource/apps/local/cs2-server-manager/data.yml
test -f /opt/1panel/resource/apps/local/cs2-server-manager/1.0.0/data.yml
```

`cs2-server-manager` 必须是应用包根目录，不能只复制 `1.0.0` 目录，也不能把整个
代码仓库直接作为本地应用目录。若日志提示 `data.yml 文件不存在`，先删除错误的
`/opt/1panel/resource/apps/local/cs2-server-manager` 目录，再按上面的命令复制；复制
完成后目录应至少包含 `data.yml`、`logo.png` 和 `1.0.0/data.yml`。

然后打开 1Panel：**应用商店 → 本地应用 → 刷新**，找到 **CS2 Server Manager** 并点击安装。

安装表单中：

- PostgreSQL 服务选择刚创建的数据库实例；
- 填写专用数据库名、用户和密码；
- Redis 服务选择已安装的 Redis 实例，并填写密码和 DB 编号；
- HTTP 端口默认 `8000`，如端口冲突可更换；
- `SECRET_KEY` 和 `JWT_SECRET_KEY` 保持 1Panel 自动生成的随机值；安装脚本会把表单
  生成的短占位值升级为 64 位十六进制（256-bit SHA-256）随机密钥；已有长度不少于
  32 位的自定义值不会被轮换；
- 应用监听地址 `API_HOST` 已固定为 `0.0.0.0`，因此会监听所有网卡；`BACKEND_URL` 默认
  是可通过 1Panel 校验的 `http://0.0.0.0:8000` 占位值。首次安装后，如果要生成密码
  重置链接、OAuth 回调或反向代理链接，请改成用户实际访问地址；反向代理场景应填写
  HTTPS 公网地址。

应用首次启动会自动执行 Alembic 数据库迁移，无需手工运行 SQL 或迁移命令。

### 3. 运维与升级

- 通过 1Panel 查看应用日志、启动、停止和重启；
- 升级前先备份 PostgreSQL 数据库和应用数据目录；
- 应用包升级只替换应用容器，不会删除外部 PostgreSQL/Redis；
- 不要删除应用包版本目录对应的 `data` 数据；
- 卸载时确认 1Panel 的数据删除选项，外部数据库备份不会由本应用包代替。

### 4. 故障排查

```bash
docker network inspect 1panel-network
docker logs <应用容器名>
docker exec <应用容器名> python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
```

- 无法解析数据库主机：重新选择 1Panel 服务实例，确认它加入 `1panel-network`；
- PostgreSQL 版本错误：应用要求主版本 18 或更高；
- 认证失败：核对专用数据库用户名、密码、数据库名以及 Redis 密码；
- 迁移失败：先查看应用日志和 PostgreSQL 日志，不要手工删除表。

根目录 Docker 用户仍可直接使用 [Docker 一键部署文档](DOCKER_QUICKSTART.md)，该路径会独立启动自己的 PostgreSQL 和 Redis。

## English

### Supported setup

- A current 1Panel installation with Docker and the `1panel-network` network;
- PostgreSQL 18.6 or newer;
- Redis 8.10.1 (Redis 7-compatible commands are supported).

The 1Panel app store provides [PostgreSQL 18.6](https://github.com/1Panel-dev/appstore/tree/dev/apps/postgresql/18.6-alpine) and [Redis 8.10.1](https://github.com/1Panel-dev/appstore/tree/dev/apps/redis/8.10.1).

### 1. Prepare the external services

1. Install and start PostgreSQL 18.6 and Redis 8.10.1 from the 1Panel App Store.
2. Create a dedicated PostgreSQL database and user, for example `cs2_manager`, and grant only the required database permissions.
3. Keep the Redis password and DB number available. Leave the password empty when Redis has no password.
4. Confirm that both services and the application will use 1Panel's `1panel-network`.

Do not use `localhost` for a database host inside the application container. Select the matching 1Panel service instance in the installation form; container `localhost` means the application container itself.

### 2. Install the local app package

From the repository checkout, copy the package into 1Panel's local app directory:

```bash
sudo mkdir -p /opt/1panel/resource/apps/local/cs2-server-manager
sudo cp -a deploy/1panel/apps/cs2-server-manager/. \
  /opt/1panel/resource/apps/local/cs2-server-manager/
test -f /opt/1panel/resource/apps/local/cs2-server-manager/data.yml
test -f /opt/1panel/resource/apps/local/cs2-server-manager/1.0.0/data.yml
```

Copy the package root, not only `1.0.0` and not the whole repository. If the sync log says
`data.yml is missing`, remove the incomplete directory and repeat the copy command above.

Open **App Store → Local Apps → Refresh**, select **CS2 Server Manager**, and install it.

In the form, select the PostgreSQL and Redis service instances, enter the dedicated PostgreSQL credentials, keep the generated application and JWT secrets, choose an HTTP port, and set `BACKEND_URL` to the public URL (HTTPS when using a reverse proxy). The container listens on all interfaces through `API_HOST=0.0.0.0`; the default `BACKEND_URL` is the validation-safe placeholder `http://0.0.0.0:8000` and should be replaced with the real public URL for reset links and OAuth callbacks. The init script upgrades short 1Panel placeholders to 64-character hexadecimal (256-bit SHA-256) secrets while preserving custom values of at least 32 characters.

The application automatically runs the reviewed Alembic migrations at startup. No manual SQL or migration command is required.

### 3. Operations and upgrades

- Use 1Panel for logs, start/stop/restart, backup, and restore;
- Back up PostgreSQL and the application data before upgrading;
- App upgrades replace only the application container and do not remove external PostgreSQL or Redis;
- Check 1Panel's data deletion option before uninstalling;
- Keep a verified database backup for rollback.

For the self-contained Docker path, use the [Docker quick-start guide](DOCKER_QUICKSTART.md).
