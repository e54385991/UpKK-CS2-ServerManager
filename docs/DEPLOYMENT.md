# CS2 Server Deployment Guide | CS2 服务器部署指南

[English](#english) | [中文](#chinese)

---

<a name="english"></a>
## English

### ⚠️ READ THIS FIRST - Server Initialization Required

This document explains **the mandatory steps** to prepare your server for CS2 deployment. **You MUST complete these steps before using the CS2 Server Manager.**

---

## System Requirements

### Operating System
- Ubuntu 24.04 LTS or later (recommended)
- Debian 12 or later
- Other Linux distributions with systemd support

### Hardware Requirements (Minimum)
- CPU: 2 cores (4+ recommended)
- RAM: 4GB (8GB+ recommended)
- Disk: 30GB free space
- Network: Stable internet connection with good upload speed

---

## Prerequisites - MUST BE DONE FIRST

### Step 1: Install Required System Packages

The CS2 Server Manager works entirely in user space and does NOT require sudo access for deployment. However, certain system packages **MUST** be installed by your system administrator beforehand.

**⚠️ CRITICAL: Run these commands on your TARGET server (where CS2 will run), NOT on the management server!**

**On Ubuntu/Debian, install these packages as root or with sudo:**

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

**Why these packages are needed:**
- `lib32gcc-s1`, `lib32stdc++6`, `lib32z1`: 32-bit compatibility libraries required by SteamCMD and CS2
- `libsdl2-2.0-0:i386`: SDL2 library for CS2 server
- `curl`, `wget`: For downloading files
- `tar`: For extracting archives
- `screen`: For running the server in background sessions

### Step 2: Create User Account

**Create a dedicated user for CS2 servers** (recommended):

```bash
sudo useradd -m -s /bin/bash cs2server
sudo passwd cs2server
```

**Or use an existing non-root user** - the deployment works entirely in the user's home directory.

**⚠️ IMPORTANT:**
- Do NOT use the root user
- The user must have a home directory
- The user needs SSH access

---

## Deployment Approach

This CS2 Server Manager follows the **LinuxGSM** approach:

1. ✅ **No sudo required** - all operations run in user space
2. ✅ **User directory installation** - everything goes in `~/cs2` or specified directory
3. ✅ **Minimal privileges** - only needs regular user SSH access
4. ✅ **Safe and isolated** - no system-wide changes during deployment

### What Happens During Deployment

When you deploy a CS2 server using the manager:

1. Connects to your server via SSH (using password or key authentication)
2. Creates the game directory in the user's home directory
3. Downloads and extracts SteamCMD
4. Uses SteamCMD to download CS2 server files (~30GB)
5. Sets up the server files
6. Returns success when complete

**No sudo commands are executed during deployment.**

---

## Configuration in CS2 Server Manager

### When Adding a Server

After installing prerequisites on your target server, configure it in the manager:

1. **Host**: IP address or hostname of your target server
2. **SSH Port**: Usually 22 (default)
3. **SSH User**: The username you created (e.g., `cs2server`)
4. **SSH Authentication**:
   - **Password**: Provide the SSH password
   - **Key File**: Or path to SSH private key on the management server
5. **Game Directory**: Where to install CS2 (default: `/home/cs2server/cs2`)

### Important Notes

- **Do NOT set sudo_password** - it's not needed and won't be used
- **SSH user should NOT be root** - use a dedicated user account
- **Game directory** should be in the user's home directory or a location the user can write to

---

## Verification Checklist

Before attempting deployment, verify:

- [ ] Required system packages are installed on target server
- [ ] Dedicated user account is created on target server
- [ ] SSH access is working (test with `ssh user@host`)
- [ ] User has write permission to the game directory
- [ ] Target server has internet access (for downloading CS2 files)
- [ ] At least 30GB free disk space is available

---

## Troubleshooting

### "Command not found" Errors

If you see errors like `wget: command not found` or `tar: command not found`, it means the required system packages are not installed.

**Solution:** Have your system administrator install the prerequisites on the target server:
```bash
sudo apt-get update
sudo apt-get install -y wget tar curl screen lib32gcc-s1 lib32stdc++6 lib32z1 libsdl2-2.0-0:i386
```

### Permission Denied Errors

If you see "Permission denied" when creating directories:

**Solution:** 
1. Make sure the game directory is in a location the SSH user can write to
2. Use a path like `/home/username/cs2` instead of system directories
3. Ensure the SSH user exists and has a home directory

### "Could not find or download steamcmd" 

If SteamCMD download fails:

**Solution:**
1. Check that `wget` is installed on target server
2. Verify the target server has internet access
3. Check firewall rules allow outbound connections

### Deployment Takes Too Long or Times Out

CS2 server files are approximately 30GB. The download time depends on your server's internet speed.

**Solution:**
1. Ensure good network connectivity
2. The default timeout is 30 minutes - should be sufficient for most connections
3. If needed, you can run the deployment again - SteamCMD will resume where it left off

---

## Security Best Practices

1. ✅ Use SSH key authentication instead of passwords when possible
2. ✅ Create dedicated user accounts for game servers
3. ✅ Don't run game servers as root
4. ✅ Keep system packages updated
5. ✅ Use firewall rules to restrict access to game server ports
6. ✅ Monitor server resources and logs

---

## Getting Help

If you encounter issues:
1. Check this documentation
2. Review server logs in the web interface
3. Verify all prerequisites are installed on the target server
4. Test SSH connectivity manually: `ssh user@host`
5. Open an issue on GitHub with details about your setup

---

<a name="chinese"></a>
## 中文

### ⚠️ 请先阅读此文档 - 必须先初始化服务器

本文档解释了准备 CS2 服务器部署的**强制性步骤**。**使用 CS2 服务器管理器之前，您必须完成这些步骤。**

---

## 系统要求

### 操作系统
- Ubuntu 24.04 LTS 或更高版本（推荐）
- Debian 12 或更高版本
- 其他支持 systemd 的 Linux 发行版

### 硬件要求（最低配置）
- CPU: 2 核心（推荐 4+ 核心）
- 内存: 4GB（推荐 8GB+）
- 磁盘: 30GB 可用空间
- 网络: 稳定的互联网连接，良好的上传速度

---

## 前置条件 - 必须首先完成

### 步骤 1: 安装必需的系统包

CS2 服务器管理器完全在用户空间运行，部署时不需要 sudo 访问权限。但是，某些系统包**必须**由系统管理员预先安装。

**⚠️ 关键提示：在目标服务器（将运行 CS2 的服务器）上运行这些命令，而不是在管理服务器上！**

**在 Ubuntu/Debian 上，以 root 或使用 sudo 安装这些包：**

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

**为什么需要这些包：**
- `lib32gcc-s1`, `lib32stdc++6`, `lib32z1`: SteamCMD 和 CS2 需要的 32 位兼容性库
- `libsdl2-2.0-0:i386`: CS2 服务器的 SDL2 库
- `curl`, `wget`: 用于下载文件
- `tar`: 用于解压存档
- `screen`: 用于在后台会话中运行服务器

### 步骤 2: 创建用户账户

**创建专用于 CS2 服务器的用户**（推荐）：

```bash
sudo useradd -m -s /bin/bash cs2server
sudo passwd cs2server
```

**或使用现有的非 root 用户** - 部署完全在用户的主目录中进行。

**⚠️ 重要提示：**
- 不要使用 root 用户
- 用户必须有主目录
- 用户需要 SSH 访问权限

---

## 部署方式

此 CS2 服务器管理器遵循 **LinuxGSM** 方式：

1. ✅ **不需要 sudo** - 所有操作都在用户空间运行
2. ✅ **用户目录安装** - 所有内容都安装在 `~/cs2` 或指定目录
3. ✅ **最小权限** - 只需要普通用户 SSH 访问权限
4. ✅ **安全隔离** - 部署期间不会进行系统级更改

### 部署过程

使用管理器部署 CS2 服务器时：

1. 通过 SSH 连接到您的服务器（使用密码或密钥认证）
2. 在用户的主目录中创建游戏目录
3. 下载并解压 SteamCMD
4. 使用 SteamCMD 下载 CS2 服务器文件（约 30GB）
5. 设置服务器文件
6. 完成后返回成功

**部署期间不会执行任何 sudo 命令。**

---

## 在 CS2 服务器管理器中配置

### 添加服务器时

在目标服务器上安装前置条件后，在管理器中配置它：

1. **主机**: 目标服务器的 IP 地址或主机名
2. **SSH 端口**: 通常是 22（默认）
3. **SSH 用户**: 您创建的用户名（例如 `cs2server`）
4. **SSH 认证**:
   - **密码**: 提供 SSH 密码
   - **密钥文件**: 或管理服务器上 SSH 私钥的路径
5. **游戏目录**: CS2 安装位置（默认：`/home/cs2server/cs2`）

### 重要说明

- **不要设置 sudo_password** - 不需要，也不会使用
- **SSH 用户不应该是 root** - 使用专用用户账户
- **游戏目录**应该在用户的主目录或用户可以写入的位置

---

## 验证清单

在尝试部署之前，请验证：

- [ ] 目标服务器上已安装必需的系统包
- [ ] 目标服务器上已创建专用用户账户
- [ ] SSH 访问正常（使用 `ssh user@host` 测试）
- [ ] 用户对游戏目录有写权限
- [ ] 目标服务器有互联网访问权限（用于下载 CS2 文件）
- [ ] 至少有 30GB 可用磁盘空间

---

## 故障排除

### "Command not found" 错误

如果您看到 `wget: command not found` 或 `tar: command not found` 等错误，说明未安装必需的系统包。

**解决方案：** 让系统管理员在目标服务器上安装前置条件：
```bash
sudo apt-get update
sudo apt-get install -y wget tar curl screen lib32gcc-s1 lib32stdc++6 lib32z1 libsdl2-2.0-0:i386
```

### 权限被拒绝错误

如果在创建目录时看到"Permission denied"：

**解决方案：**
1. 确保游戏目录在 SSH 用户可以写入的位置
2. 使用像 `/home/username/cs2` 这样的路径，而不是系统目录
3. 确保 SSH 用户存在且有主目录

### "Could not find or download steamcmd"

如果 SteamCMD 下载失败：

**解决方案：**
1. 检查目标服务器上是否安装了 `wget`
2. 验证目标服务器有互联网访问权限
3. 检查防火墙规则是否允许出站连接

### 部署时间过长或超时

CS2 服务器文件约为 30GB。下载时间取决于服务器的互联网速度。

**解决方案：**
1. 确保网络连接良好
2. 默认超时时间为 30 分钟 - 对大多数连接应该足够
3. 如果需要，可以再次运行部署 - SteamCMD 将从中断处继续

---

## 安全最佳实践

1. ✅ 尽可能使用 SSH 密钥认证而不是密码
2. ✅ 为游戏服务器创建专用用户账户
3. ✅ 不要以 root 身份运行游戏服务器
4. ✅ 保持系统包更新
5. ✅ 使用防火墙规则限制对游戏服务器端口的访问
6. ✅ 监控服务器资源和日志

---

## 获取帮助

如果遇到问题：
1. 查看此文档
2. 在 Web 界面中查看服务器日志
3. 验证目标服务器上已安装所有前置条件
4. 手动测试 SSH 连接：`ssh user@host`
5. 在 GitHub 上创建 Issue，提供有关您设置的详细信息
