# Quick Start Guide | 快速入门指南

[English](#english) | [中文](#chinese)

---

<a name="english"></a>
## English

### 🚀 Quick Start in 3 Steps

#### ⚠️ CRITICAL: Read This First

**You MUST initialize your target servers BEFORE using this manager!** This is the most common mistake. The CS2 Server Manager cannot install system packages - they must be pre-installed by a system administrator.

---

### Step 1: Initialize Target Servers (REQUIRED)

On **each server where you want to run CS2** (NOT on the manager server):

```bash
# Install required system packages (as root or with sudo)
sudo apt-get update
sudo apt-get install -y \
    lib32gcc-s1 \
    lib32stdc++6 \
    lib32z1 \
    libsdl2-2.0-0:i386 \
    curl \
    wget \
    tar \
    screen \
    tmux

# Create a dedicated user for CS2
sudo useradd -m -s /bin/bash cs2server
sudo passwd cs2server
```

**✅ Verification:**
- Test SSH access: `ssh cs2server@your-server-ip`
- Verify packages are installed
- Confirm at least 30GB free disk space

**📖 For detailed instructions, see [DEPLOYMENT.md](DEPLOYMENT.md)**

---

### Step 2: Setup Manager

On your **management server** (can be your local computer):

```bash
# Clone the repository
git clone https://github.com/e54385991/UpKK-CS2-ServerManager.git
cd UpKK-CS2-ServerManager

# Configure database and Redis before the first upgrade
cp .env.example .env
# Edit .env with your database and Redis connection settings

# Start dependencies (MySQL + Redis)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Prepare locked dependencies and apply/verify migrations
./upgrade.sh

# Create the first administrator (replace every placeholder)
uv run python -m cs2_manager.cli create-admin \
  --username YOUR_ADMIN_NAME \
  --email YOUR_ADMIN_EMAIL \
  --password-prompt

# Run the single-worker manager
./start.sh
```

For a single-instance source checkout, `upgrade.sh` may be called from any working directory. If `.env` is absent it copies `.env.example`; otherwise it appends only missing keys and keeps existing values/comments. It restricts the resulting `.env` to owner read/write permissions (`0600`). Empty or example application secrets and the AES-256-GCM keyring are generated from a cryptographically secure random source. The script then runs legacy normalization, Alembic upgrade, and revision checking under the existing MySQL advisory lock. It is idempotent, stops on failure, and never starts the application.

Database and Redis connection values are operator-owned. If the first run creates `.env` but cannot connect, edit those values and rerun `./upgrade.sh`.

Production Docker, 1Panel, and multi-process deployments must still run `uv run --locked python -m cs2_manager.migrate upgrade` and `check` as an independent release step before starting any application process. Do not use `upgrade.sh` as a container entrypoint or process-manager startup hook.

**✅ Verification:**
- Open http://localhost:8000 in your browser
- You should see the login page

---

### Step 3: Deploy CS2 Server

1. **Login** to the web interface at http://localhost:8000/login
   - Use the administrator explicitly created in Step 2
   - No default credentials are generated

2. **Add a Server** at http://localhost:8000/servers-ui
   - Click "Add Server"
   - Fill in:
     - Server name: `My CS2 Server`
     - Host: IP address of your target server (from Step 1)
     - SSH User: `cs2server` (the user you created in Step 1)
     - SSH Password: the password you set in Step 1
     - Game Port: `27015` (or your preferred port)
     - Game Directory: `/home/cs2server/cs2`
   - Click "Save"

3. **Deploy CS2**
   - Click on your server in the list
   - Click "Deploy" button
   - Watch the real-time deployment logs
   - Wait for completion (~10-30 minutes depending on internet speed)

4. **Start the Server**
   - Click "Start" button
   - Server will start and you can connect to it

5. **Connect to Your Server**
   - Open CS2 game
   - Open console (usually `~` key)
   - Type: `connect your-server-ip:27015`

---

### Common Mistakes

❌ **Not initializing the target server first**
- Symptoms: `command not found` errors during deployment
- Solution: Install required packages on target server (Step 1)

❌ **Using root user**
- Symptoms: Security warnings, permission issues
- Solution: Create a dedicated user as shown in Step 1

❌ **Wrong server for package installation**
- Symptoms: Deployment fails with missing dependencies
- Solution: Install packages on TARGET server (where CS2 runs), not on management server

❌ **Insufficient disk space**
- Symptoms: Deployment fails partway through
- Solution: Ensure at least 30GB free space on target server

---

### Next Steps

- **Install Plugins**: See [PLUGIN_INSTALLATION_GUIDE.md](PLUGIN_INSTALLATION_GUIDE.md)
- **Configure Auto-Restart**: See [AUTO_RESTART_GUIDE.md](AUTO_RESTART_GUIDE.md)
- **Enable Auto-Updates**: See [AUTO_UPDATE_GUIDE.md](AUTO_UPDATE_GUIDE.md)
- **Monitor Server**: Use A2S queries - see [A2S_QUERY_GUIDE.md](A2S_QUERY_GUIDE.md)

---

### Need Help?

1. Check the error messages in the web interface logs
2. Verify all steps in [DEPLOYMENT.md](DEPLOYMENT.md)
3. Review the full documentation in [README.md](README.md)
4. Open an issue on GitHub with details

---

<a name="chinese"></a>
## 中文

### 🚀 三步快速入门

#### ⚠️ 重要：请先阅读

**在使用此管理器之前，您必须先初始化目标服务器！** 这是最常见的错误。CS2 服务器管理器无法安装系统包 - 它们必须由系统管理员预先安装。

---

### 步骤 1: 初始化目标服务器（必须）

在**每台要运行 CS2 的服务器上**（不是在管理器服务器上）：

```bash
# 安装必需的系统包（以 root 或使用 sudo）
sudo apt-get update
sudo apt-get install -y \
    lib32gcc-s1 \
    lib32stdc++6 \
    lib32z1 \
    libsdl2-2.0-0:i386 \
    curl \
    wget \
    tar \
    screen \
    tmux

# 创建 CS2 专用用户
sudo useradd -m -s /bin/bash cs2server
sudo passwd cs2server
```

**✅ 验证：**
- 测试 SSH 访问：`ssh cs2server@your-server-ip`
- 验证包已安装
- 确认至少有 30GB 可用磁盘空间

**📖 详细说明请参阅 [DEPLOYMENT.md](DEPLOYMENT.md)**

---

### 步骤 2: 设置管理器

在您的**管理服务器**上（可以是您的本地计算机）：

```bash
# 克隆仓库
git clone https://github.com/e54385991/UpKK-CS2-ServerManager.git
cd UpKK-CS2-ServerManager

# 首次升级前配置数据库和 Redis
cp .env.example .env
# 编辑 .env 文件中的数据库和 Redis 连接

# 启动依赖（MySQL + Redis）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# 准备锁定依赖，并执行/校验迁移
./upgrade.sh

# 创建首个管理员（请替换所有占位值）
uv run python -m cs2_manager.cli create-admin \
  --username YOUR_ADMIN_NAME \
  --email YOUR_ADMIN_EMAIL \
  --password-prompt

# 运行单 worker 管理器
./start.sh
```

源码单实例可从任意工作目录调用 `upgrade.sh`。缺少 `.env` 时脚本从 `.env.example` 创建；已有文件时仅追加缺失键，保留已有值和注释，并将最终 `.env` 权限收紧为仅所有者可读写（`0600`）。空值或模板占位的应用密钥及 AES-256-GCM keyring 会使用加密安全随机源生成。随后脚本在现有 MySQL advisory lock 保护下执行旧结构归一化、Alembic upgrade 和版本检查。整个过程幂等、失败即停止，并且不会启动应用。

数据库和 Redis 连接由运维负责。首次运行创建 `.env` 后如果无法连接，请编辑连接值并重新执行 `./upgrade.sh`。

生产 Docker、1Panel 和多进程部署仍须在发布阶段独立执行 `uv run --locked python -m cs2_manager.migrate upgrade` 和 `check`，然后再启动任何应用进程；不要把 `upgrade.sh` 作为容器入口或进程管理器启动钩子。

**✅ 验证：**
- 在浏览器中打开 http://localhost:8000
- 您应该看到登录页面

---

### 步骤 3: 部署 CS2 服务器

1. **登录** Web 界面 http://localhost:8000/login
   - 使用步骤 2 中显式创建的管理员账户
   - 系统不会生成默认凭据

2. **添加服务器** http://localhost:8000/servers-ui
   - 点击"Add Server"
   - 填写：
     - 服务器名称：`My CS2 Server`
     - 主机：目标服务器的 IP 地址（来自步骤 1）
     - SSH 用户：`cs2server`（步骤 1 中创建的用户）
     - SSH 密码：步骤 1 中设置的密码
     - 游戏端口：`27015`（或您首选的端口）
     - 游戏目录：`/home/cs2server/cs2`
   - 点击"Save"

3. **部署 CS2**
   - 在列表中点击您的服务器
   - 点击"Deploy"按钮
   - 观察实时部署日志
   - 等待完成（约 10-30 分钟，取决于网速）

4. **启动服务器**
   - 点击"Start"按钮
   - 服务器将启动，您可以连接到它

5. **连接到您的服务器**
   - 打开 CS2 游戏
   - 打开控制台（通常是 `~` 键）
   - 输入：`connect your-server-ip:27015`

---

### 常见错误

❌ **未先初始化目标服务器**
- 症状：部署期间出现 `command not found` 错误
- 解决方案：在目标服务器上安装必需的包（步骤 1）

❌ **使用 root 用户**
- 症状：安全警告、权限问题
- 解决方案：按步骤 1 所示创建专用用户

❌ **在错误的服务器上安装包**
- 症状：部署失败，缺少依赖项
- 解决方案：在目标服务器（运行 CS2 的服务器）上安装包，而不是在管理服务器上

❌ **磁盘空间不足**
- 症状：部署中途失败
- 解决方案：确保目标服务器上至少有 30GB 可用空间

---

### 下一步

- **安装插件**：参阅 [PLUGIN_INSTALLATION_GUIDE.md](PLUGIN_INSTALLATION_GUIDE.md)
- **配置自动重启**：参阅 [AUTO_RESTART_GUIDE.md](AUTO_RESTART_GUIDE.md)
- **启用自动更新**：参阅 [AUTO_UPDATE_GUIDE.md](AUTO_UPDATE_GUIDE.md)
- **监控服务器**：使用 A2S 查询 - 参阅 [A2S_QUERY_GUIDE.md](A2S_QUERY_GUIDE.md)

---

### 需要帮助？

1. 检查 Web 界面日志中的错误消息
2. 验证 [DEPLOYMENT.md](DEPLOYMENT.md) 中的所有步骤
3. 查看 [README.md](README.md) 中的完整文档
4. 在 GitHub 上创建 Issue，提供详细信息
