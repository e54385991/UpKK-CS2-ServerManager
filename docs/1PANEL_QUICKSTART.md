# 1Panel 部署 / 1Panel Deployment

**不要用两个「运行环境」分别部署前后端。** Next 容器访问宿主机局域网 IP
（例如 `http://192.168.50.245:8000`）会走 Docker hairpin，登录页慢、验证码一直
Loading。正确做法是一套 Compose：Caddy → Next → 本实例 FastAPI（容器内 `:8000`）。

推荐两条路径：

1. **应用商店 / 本地应用包**（复用 1Panel 的 PostgreSQL 和 Redis）；
2. **容器 → Compose** 粘贴仓库根目录 `docker-compose.yml`（自带 PostgreSQL 和 Redis）。

The two-runtime 1Panel setup is unsupported. Install the local app package or
paste the root Compose file so Next proxies to FastAPI on container port 8000
(the service name `app` is unique only inside that Compose project).

## 中文

### 适用版本

- 1Panel 当前版本，已启用 Docker 和 `1panel-network`；
- PostgreSQL 18.6（应用要求 PostgreSQL 18 或更高）；
- Redis 8.10.1（Redis 7 及以上协议兼容）。

1Panel 应用商店当前提供 [PostgreSQL 18.6](https://github.com/1Panel-dev/appstore/tree/dev/apps/postgresql/18.6-alpine) 和 [Redis 8.10.1](https://github.com/1Panel-dev/appstore/tree/dev/apps/redis/8.10.1)。

### 1. 准备外部服务

1. 在 1Panel 应用商店安装并启动 PostgreSQL 18.6 和 Redis 8.10.1。
2. Redis 密码为必填。先在 1Panel 安装带密码的 Redis，再选择该实例。本地商店 Redis
   选中后通常会回填 `PANEL_REDIS_ROOT_PASSWORD`；没回填就从 **数据库 → Redis → 连接信息**
   复制。本包只复用已有 Redis 实例和 DB，不会创建 Redis 或 ACL 用户。不支持无密码 Redis。
3. 确认 PostgreSQL、Redis 和待安装应用都连接到 1Panel 的 `1panel-network`。

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

安装表单（前后端在同一套应用里，不用再填局域网 IP）：

- PostgreSQL / Redis：选择 1Panel 已安装的服务实例。PostgreSQL 库名、用户、密码默认自动生成；
  Redis 密码必填，选中本地 Redis 时一般会自动填上，空密码不能安装；
- **控制台 HTTP 端口**：默认 `31800`，映射到 Caddy `:80`，再反代 Next。FastAPI
  只在该实例容器内监听 `:8000`，不要改成 `:8001`，也不要映射到宿主机；
- **浏览器访问地址**：默认 `http://localhost:31800`。装完后改成实际地址，例如
  `http://192.168.50.245:31800`（改端口时一并改这里）。不要填 `0.0.0.0`；
- **后端内部地址**：保持 `http://app:8000`。每套应用有自己的内部网，
  `app` 只在这一套里解析。不要改成 `:8001`，也不要把 Next/Caddy 挂到
  共享的 `1panel-network`（两套会抢 `app` / `frontend`）；
- 前后端镜像：保持表单默认的 Docker Hub 地址，不要留空（1Panel 拉镜像不会展开 `${VAR:-默认值}`）；
- `SECRET_KEY` / `JWT_SECRET_KEY`：保持自动生成。init 脚本会把短占位值升级为 64 位十六进制密钥。

应用首次启动会自动执行 Alembic 数据库迁移，无需手工运行 SQL 或迁移命令。

全新数据库首次启动时会自动创建管理员账户。安装完成后访问
`http://服务器IP:外部端口`，使用默认用户名 `admin`、默认密码 `admin123` 登录，
并在首次登录后立即修改默认密码。复用已有数据库时，系统会继续使用已有账户，
不会重新创建或重置默认管理员密码。

### 3. 运维与升级

- 通过 1Panel 查看应用日志、启动、停止和重启；
- 升级前先备份 PostgreSQL 数据库和应用数据目录；
- 应用包升级只替换应用容器，不会删除外部 PostgreSQL/Redis；
- 不要删除应用包版本目录对应的 `data` 数据；
- 卸载时确认 1Panel 的数据删除选项，外部数据库备份不会由本应用包代替。

### 4. 故障排查

安装卡在「启动 应用 / Creating」时，先在 1Panel **卸载**失败的应用（保留或删除空库均可），再确认没有残留容器：

```bash
docker ps -a --filter name=cs2-server-manager
docker network inspect 1panel-network >/dev/null
```

商店包里 Next 走本实例容器名 `:8000`，不要给前端加
`host.docker.internal:host-gateway`（部分 1Panel Docker 会在 Creating 阶段直接失败）。
也不要把 FastAPI 改成宿主机 `8001`。

```bash
docker network inspect 1panel-network
docker logs <应用容器名>
docker exec <应用容器名> python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
```

- 无法解析数据库主机：重新选择 1Panel 服务实例，确认它加入 `1panel-network`；
- PostgreSQL 版本错误：应用要求主版本 18 或更高；
- 认证失败：核对专用数据库用户名、密码、数据库名以及 Redis 密码；
- 第二个实例登录后 401：先确认控制台端口和「浏览器访问地址」是 `31801` 而不是
  把 FastAPI 改成 `8001`；并重新复制本仓库 1Panel 包后重建容器（见下文「两个实例」）；
- 迁移失败：先查看应用日志和 PostgreSQL 日志，不要手工删除表。

### 5. 同一台机器安装两个实例

可以共用 1Panel 的 PostgreSQL / Redis。第二套请：

1. **控制台 HTTP 端口**用另一个端口（例如 `31801`），**浏览器访问地址**写成
   `http://服务器IP:31801`。公网入口是 Caddy → Next，不是 FastAPI。
2. **不要**把 FastAPI 改成 `8001`，也**不要**把「后端内部地址」改成 `:8001`。
   FastAPI 始终在该实例容器内 `:8000`。
3. 为第二套选**另一个 PostgreSQL 库**（表单会自动生成库名/用户）。Redis 可以
   继续用 DB `0`：应用会用容器名给 key 加前缀。也可以选不同的 Redis DB，但不是必须。
4. 每个实例各自生成 `JWT_SECRET_KEY`。会话 cookie 会带上控制台端口
   （`upkk_access_token_31800` / `upkk_access_token_31801`），避免同一浏览器串会话。

旧包把 Next / Caddy / FastAPI 都挂在共享的 `1panel-network` 上，两套的服务名
`app` 会抢 DNS，表现为登录成功后立刻 401。把本仓库
`deploy/1panel/apps/cs2-server-manager` 重新复制到 1Panel 本地应用目录并**重建**
两套容器（只改端口不够）。

根目录 Docker 用户仍可直接使用 [Docker 一键部署文档](DOCKER_QUICKSTART.md)，该路径会独立启动自己的 PostgreSQL 和 Redis。

## English

### Supported setup

- A current 1Panel installation with Docker and the `1panel-network` network;
- PostgreSQL 18.6 or newer;
- Redis 8.10.1 (Redis 7-compatible commands are supported).

The 1Panel app store provides [PostgreSQL 18.6](https://github.com/1Panel-dev/appstore/tree/dev/apps/postgresql/18.6-alpine) and [Redis 8.10.1](https://github.com/1Panel-dev/appstore/tree/dev/apps/redis/8.10.1).

### 1. Prepare the external services

1. Install and start PostgreSQL 18.6 and Redis 8.10.1 from the 1Panel App Store.
2. The Redis password is required. Install Redis in 1Panel with a password first, then select that instance. A local App Store Redis usually fills `PANEL_REDIS_ROOT_PASSWORD` when you pick it; otherwise copy the password from **Database → Redis → Connection info**. The package reuses the selected Redis instance and DB and does not create a Redis server or ACL user. Redis without a password is not supported by this package.
3. Confirm that both services and the application will use 1Panel's `1panel-network`.

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

In the form, select the PostgreSQL and Redis service instances. Keep the generated
PostgreSQL name/user/password unless you are reusing an existing 1Panel database.
The Redis password is required: a local Redis instance usually auto-fills it; an
empty password cannot be installed.
Set **Console HTTP Port** to `31800` (or another free host port). That port maps to
Caddy `:80` → Next; FastAPI stays private on container port `8000`. Set **Browser origin URL**
to the address people type, such as `http://192.168.50.245:31800` — never `0.0.0.0`.
Leave **Internal API URL** at `http://app:8000`. Each install has a private
compose network, so `app` is unique to that stack. Do not change it to `:8001`.
Keep the default backend and
frontend image fields (do not leave them empty; 1Panel cannot pull
`${VAR:-default}` image refs). Keep the generated `SECRET_KEY` /
`JWT_SECRET_KEY`; `init.sh` upgrades short placeholders to 64-character
hexadecimal secrets.

The application automatically runs the reviewed Alembic migrations at startup. No manual SQL or migration command is required.

On the first startup with a new database, the application creates the default administrator
account. Open `http://server-ip:<port>` and sign in with username `admin` and password
`admin123`, then change the default password immediately. When reusing an existing database,
existing accounts are preserved and the administrator password is not reset.

### 3. Operations and upgrades

- Use 1Panel for logs, start/stop/restart, backup, and restore;
- Back up PostgreSQL and the application data before upgrading;
- App upgrades replace only the application container and do not remove external PostgreSQL or Redis;
- Check 1Panel's data deletion option before uninstalling;
- Keep a verified database backup for rollback.

### 4. Two instances on one host

Both installs may share the 1Panel PostgreSQL and Redis services.

1. Give the second console another HTTP port (`31801`) and set **Browser origin URL**
   to `http://SERVER_IP:31801`. The public entry is Caddy → Next, not FastAPI.
2. Do **not** remap FastAPI to host `8001`, and do **not** set the internal API to
   `:8001`. FastAPI stays on container port `8000`.
3. Use a **separate PostgreSQL database** (the form generates one). Redis may stay
   on DB `0`: keys are prefixed with the container name. A different Redis DB is
   optional, not required.
4. Each install keeps its own `JWT_SECRET_KEY`. Session cookies include the console
   port (`upkk_access_token_31800` vs `upkk_access_token_31801`).

Older packages put Next, Caddy, and FastAPI on the shared `1panel-network`, so
both installs answered the service name `app` and the second console got 401
after login. Recopy this repo's 1Panel package and **recreate both stacks**
(changing only the console port is not enough).

For the self-contained Docker path, use the [Docker quick-start guide](DOCKER_QUICKSTART.md).
