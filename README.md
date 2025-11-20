# CS2 Server Manager | CS2 服务器管理器

[English](#english) | [中文](#chinese)

---

<a name="english"></a>
## English

A CS2 (Counter-Strike 2) server manager built with FastAPI + Redis + MySQL, supporting multi-server management via SSH including deployment, starting, stopping, and more.

### ⚠️ CRITICAL: Server Initialization Required

**IMPORTANT:** Before using this manager, you **MUST** initialize your target servers first!

The CS2 Server Manager operates in user space and does NOT require sudo privileges. However, your target servers must have all required system packages pre-installed by a system administrator.

**📖 See [Quick Start Guide](docs/QUICK_START.md) for a step-by-step walkthrough!**

**📖 See [Deployment Guide](docs/DEPLOYMENT.md) for complete server preparation steps.**

### Features

- ✅ **Async Architecture**: Fully async/await implementation for high performance
- 🚀 **Multi-Server Management**: Manage multiple CS2 servers
- 👥 **User Authentication**: JWT token authentication, users can only manage their own servers
- 🔐 **SSH Connection**: Supports both password and key file authentication
- 📦 **Auto Deployment**: Automatic CS2 server deployment via SSH
- 🎮 **Server Control**: Start, stop, restart servers
- 🔄 **Auto-Restart Protection**: Automatic restart on crash with crash loop protection ([View Docs](docs/AUTO_RESTART_GUIDE.md))
- 🔔 **Real-time Status Reporting**: Servers report crash and restart events to management backend via API
- 🔌 **Plugin Framework Installation**: One-click installation of Metamod:Source and CounterStrikeSharp, batch installation and updates supported
- 📊 **Status Monitoring**: Real-time server status monitoring
- 🔴 **WebSocket Real-time Updates**: Watch deployment process with live SSH status and output
- 💾 **Redis Caching**: Server status caching with Redis
- 📝 **Operation Logs**: Record all deployment and operation history
- 🐳 **Docker Support**: Quick dependency deployment with Docker Compose
- 🎨 **Modern Web UI**: Responsive interface based on Bootstrap 5 + Alpine.js, all resources fully localized

### System Requirements

#### Management Server (Running Web Interface)
- Python 3.9+ (3.11+ recommended, supports Python 3.14)
- MySQL 8.0+
- Redis 7.0+

#### Target Servers (Running CS2)
- Ubuntu 24.04+ or other Linux distributions
- Required system packages: lib32gcc-s1, lib32stdc++6, wget, tar, screen, etc.
- **IMPORTANT**: See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for complete server preparation guide

### Quick Start

#### Step 1: Prepare Target Servers (REQUIRED FIRST)

**⚠️ THIS STEP IS MANDATORY** - You must prepare your target CS2 servers before using this manager!

On each target server, install required packages as root:

```bash
sudo apt-get update
sudo apt-get install -y \
    lib32gcc-s1 \
    lib32stdc++6 \
    lib32z1 \
    libsdl2-2.0-0:i386 \
    curl \
    wget \
    tar \
    screen
```

Create a dedicated user (recommended):

```bash
sudo useradd -m -s /bin/bash cs2server
```

See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for detailed server preparation instructions.

#### Step 2: Clone Repository

```bash
git clone https://github.com/e54385991/CS2-ServerManager.git
cd CS2-ServerManager
```

#### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

#### Step 4: 


```
# Database Configuration
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=cs2admin
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=cs2_manager

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0

# Application Configuration
API_HOST=0.0.0.0
API_PORT=8000
DEBUG=True

# Security
SECRET_KEY=your-secret-key-change-this-in-production
```

#### Step 5: Start Dependencies (Using Docker)

```bash
docker-compose up -d
```

This starts MySQL and Redis services.

#### Step 6: Run Application

**Option 1: Using Startup Script (Recommended)**

Linux/Mac:
```bash
chmod +x start.sh
./start.sh
```

Windows:
```bash
start.bat
```

**Option 2: Direct Python**

```bash
python main.py
```

**Option 3: Using uvicorn**

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

#### Step 7: Access Application

Open browser and visit:
- **Web Interface**: http://localhost:8000/
  - Homepage: Features and quick navigation
  - Login/Register: http://localhost:8000/login or http://localhost:8000/register
  - Server Management: http://localhost:8000/servers-ui (requires login)
- **API Docs**: 
  - Swagger UI: http://localhost:8000/docs
  - ReDoc: http://localhost:8000/redoc

#### Step 8: First Login

Default admin account (created automatically on first startup):

```
Username: admin
Password: admin123
```

**⚠️ Security Warning**: Change the default password immediately after first login!

### Usage Workflow

1. **Prepare Target Servers** ⚠️ REQUIRED - Install system packages on target servers first
2. **Start Manager** - Start the web application
3. **Login** - Access http://localhost:8000/login
4. **Add Server** - Configure SSH connection details for your target server
5. **Deploy** - Manager will SSH to target server and deploy CS2
6. **Manage** - Start, stop, restart, monitor your servers

### Documentation

- [Deployment Guide](docs/DEPLOYMENT.md) - **START HERE** - Server preparation requirements
- [Auto-Restart Guide](docs/AUTO_RESTART_GUIDE.md) - Automatic restart and crash protection
- [Plugin Installation Guide](docs/PLUGIN_INSTALLATION_GUIDE.md) - Installing Metamod and CounterStrikeSharp
- [Auto-Update Guide](docs/AUTO_UPDATE_GUIDE.md) - Automatic CS2 version updates
- [A2S Query Guide](docs/A2S_QUERY_GUIDE.md) - Server querying and monitoring
- [I18N Guide](docs/I18N_GUIDE.md) - Internationalization support
- [Frontend Guide](docs/FRONTEND.md) - Frontend architecture and customization
- [LinuxGSM Config Guide](docs/LGSM_CONFIG_GUIDE.md) - LinuxGSM-style configuration
- [CS2 Startup Guide](docs/CS2_STARTUP_GUIDE.md) - Server startup parameters

### License

MIT License

### Support

For issues, please create an Issue or contact the maintainer.

---

<a name="chinese"></a>
## 中文

一个基于 FastAPI + Redis + MySQL 构建的 CS2 (Counter-Strike 2) 服务器管理器，支持通过 SSH 管理多个服务器，包括部署、启动、停止等操作。

### ⚠️ 重要：必须先初始化服务器

**重要提示：** 使用本管理器之前，您**必须**先初始化目标服务器！

CS2 服务器管理器在用户空间运行，不需要 sudo 权限。但是，您的目标服务器必须由系统管理员预先安装所有必需的系统包。

**📖 请参阅 [快速入门指南](docs/QUICK_START.md) 了解分步操作！**

**📖 请参阅 [部署指南](docs/DEPLOYMENT.md) 了解完整的服务器准备步骤。**

### 特性

- ✅ **异步架构**: 完全使用 async/await 实现高性能异步操作
- 🚀 **多服务器管理**: 支持管理多个 CS2 服务器
- 👥 **用户认证**: JWT 令牌认证，用户只能管理自己创建的服务器
- 🔐 **SSH 连接**: 支持密码和密钥文件两种认证方式
- 📦 **自动部署**: 通过 SSH 自动部署 CS2 服务器
- 🎮 **服务器控制**: 启动、停止、重启服务器
- 🔄 **自动重启保护**: 服务器崩溃时自动重启，具有崩溃循环保护机制 ([查看文档](docs/AUTO_RESTART_GUIDE.md))
- 🔔 **实时状态上报**: 服务器通过 API 向管理端上报崩溃、重启等事件
- 🔌 **插件框架安装**: 一键安装 Metamod:Source 和 CounterStrikeSharp，支持批量安装和更新
- 📊 **状态监控**: 实时查看服务器状态
- 🔴 **WebSocket 实时更新**: 部署过程实时查看 SSH 状态和输出
- 💾 **Redis 缓存**: 使用 Redis 缓存服务器状态
- 📝 **操作日志**: 记录所有部署和操作历史
- 🐳 **Docker 支持**: 提供 Docker Compose 快速部署依赖
- 🎨 **现代化 Web 界面**: 基于 Bootstrap 5 + Alpine.js 的响应式界面，所有资源完全本地化

### 系统要求

#### 管理端 (运行 Web 界面)
- Python 3.13+ (推荐 3.13 或更高版本，支持 Python 3.14)
- MySQL 8.0+
- Redis 7.0+

#### 目标服务器 (运行 CS2)
- Ubuntu 24.04+ 或其他 Linux 发行版
- 必需的系统包: lib32gcc-s1, lib32stdc++6, wget, tar, screen 等
- **重要**: 请参阅 [DEPLOYMENT.md](docs/DEPLOYMENT.md) 了解完整的服务器准备指南

### 快速开始

#### 步骤 1: 准备目标服务器（必须首先完成）

**⚠️ 此步骤是强制性的** - 使用本管理器之前，您必须先准备好目标 CS2 服务器！

在每台目标服务器上，以 root 身份安装必需的包：

```bash
sudo apt-get update
sudo apt-get install -y \
    lib32gcc-s1 \
    lib32stdc++6 \
    lib32z1 \
    libsdl2-2.0-0:i386 \
    curl \
    wget \
    tar \
    screen
```

创建专用用户（推荐）：

```bash
sudo useradd -m -s /bin/bash cs2server
```

详细的服务器准备说明请参阅 [DEPLOYMENT.md](docs/DEPLOYMENT.md)。

#### 步骤 2: 克隆仓库

```bash
git clone https://github.com/e54385991/CS2-ServerManager.git
cd CS2-ServerManager
```

#### 步骤 3: 安装依赖

```bash
pip install -r requirements.txt
```

#### 步骤 4: 配置服务器

modules/config.py 设置必要的数据库和redis服务器

# Database Configuration
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=cs2admin
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=cs2_manager

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0

# Application Configuration
API_HOST=0.0.0.0
API_PORT=8000
DEBUG=True

# Security
SECRET_KEY=your-secret-key-change-this-in-production
```

#### 步骤 5: 启动依赖服务 (使用 Docker)

```bash
docker-compose up -d
```

这将启动 MySQL 和 Redis 服务。

#### 步骤 6: 运行应用

**方式一：使用启动脚本（推荐）**

Linux/Mac:
```bash
chmod +x start.sh
./start.sh
```

Windows:
```bash
start.bat
```

**方式二：直接使用 Python**

```bash
python main.py
```

**方式三：使用 uvicorn**

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

#### 步骤 7: 访问应用

打开浏览器访问：
- **Web 界面**: http://localhost:8000/
  - 主页：功能介绍和快速导航
  - 登录/注册：http://localhost:8000/login 或 http://localhost:8000/register
  - 服务器管理：http://localhost:8000/servers-ui (需要登录)
- **API 文档**: 
  - Swagger UI: http://localhost:8000/docs
  - ReDoc: http://localhost:8000/redoc

#### 步骤 8: 首次登录

首次启动应用时，系统会自动创建默认管理员账户：

```
用户名: admin
密码: admin123
```

**⚠️ 重要安全提示**: 请在首次登录后立即更改默认密码！

### 使用流程

1. **准备目标服务器** ⚠️ 必须 - 首先在目标服务器上安装系统包
2. **启动管理器** - 启动 Web 应用程序
3. **登录** - 访问 http://localhost:8000/login
4. **添加服务器** - 配置目标服务器的 SSH 连接详情
5. **部署** - 管理器将通过 SSH 连接到目标服务器并部署 CS2
6. **管理** - 启动、停止、重启、监控您的服务器

### 文档

- [部署指南](docs/DEPLOYMENT.md) - **从这里开始** - 服务器准备要求
- [自动重启指南](docs/AUTO_RESTART_GUIDE.md) - 自动重启和崩溃保护
- [插件安装指南](docs/PLUGIN_INSTALLATION_GUIDE.md) - 安装 Metamod 和 CounterStrikeSharp
- [自动更新指南](docs/AUTO_UPDATE_GUIDE.md) - CS2 版本自动更新
- [A2S 查询指南](docs/A2S_QUERY_GUIDE.md) - 服务器查询和监控
- [国际化指南](docs/I18N_GUIDE.md) - 多语言支持
- [前端指南](docs/FRONTEND.md) - 前端架构和自定义
- [LinuxGSM 配置指南](docs/LGSM_CONFIG_GUIDE.md) - LinuxGSM 风格配置
- [CS2 启动指南](docs/CS2_STARTUP_GUIDE.md) - 服务器启动参数

### 许可证

MIT License

### 支持

如有问题，请创建 Issue 或联系维护者。
