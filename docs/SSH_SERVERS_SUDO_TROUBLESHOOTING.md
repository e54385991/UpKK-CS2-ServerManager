# SSH Servers Sudo Table 问题排查

## 问题描述

`ssh_servers_sudo` 表中没有数据。

## 可能原因

1. **数据库表未创建**: 表可能还未在数据库中创建
2. **使用 root 用户测试**: 如果使用 root 用户进行初始化，不会保存 sudo 信息（因为 root 不需要 sudo）
3. **数据库会话问题**: 数据可能未正确提交到数据库

## 解决方案

### 1. 检查表是否存在

运行以下 SQL 命令检查表是否存在：

```sql
SHOW TABLES LIKE 'ssh_servers_sudo';
```

如果表不存在，请继续下一步。

### 2. 重启应用以运行迁移

应用程序在启动时会自动运行数据库迁移（`migrate_db()`），这会创建 `ssh_servers_sudo` 表。

```bash
# 重启应用
docker-compose restart  # 如果使用 Docker
# 或
systemctl restart cs2-manager  # 如果使用 systemd
```

### 3. 手动创建表（如果重启后仍不存在）

运行提供的 SQL 脚本：

```bash
mysql -u your_user -p your_database < db/create_ssh_servers_sudo.sql
```

或直接在 MySQL 中执行：

```sql
CREATE TABLE IF NOT EXISTS ssh_servers_sudo (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    host VARCHAR(255) NOT NULL,
    ssh_port INT NOT NULL DEFAULT 22,
    sudo_user VARCHAR(100) NOT NULL,
    sudo_password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_ssh_sudo_config (user_id, host, ssh_port, sudo_user),
    INDEX idx_ssh_servers_sudo_user_id (user_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 4. 验证表结构

```sql
DESCRIBE ssh_servers_sudo;
```

应该显示以下字段：
- id
- user_id
- host
- ssh_port
- sudo_user
- sudo_password
- created_at
- updated_at

### 5. 测试保存功能

使用**非 root 用户**进行服务器初始化测试：

1. 访问 setup wizard
2. 使用具有 sudo 权限的普通用户（如 `ubuntu`, `admin` 等）
3. 完成初始化
4. 检查日志中的调试信息：
   - `调试信息: needs_sudo=True/False, sudo_pass=已设置/未设置`
   - `保存 sudo 配置到数据库...`
   - `✓ sudo 配置已保存 (用户: xxx, 类型: 无密码 sudo/带密码 sudo)`

### 6. 查询数据

初始化完成后，查询数据：

```sql
SELECT * FROM ssh_servers_sudo;
```

## 调试信息

最新版本添加了详细的调试日志，会显示：

1. `needs_sudo` 的值（True/False）
2. `sudo_pass` 是否设置
3. 保存的具体参数（user_id, host, port, sudo_user, password_length）
4. 如果是 root 用户，会显示"跳过 sudo 配置保存 (root 用户或未使用 sudo)"
5. 如果保存失败，会显示完整的错误堆栈信息

## 重要提示

- ✅ **使用普通用户测试**（带 sudo 权限）
- ❌ **不要使用 root 用户测试**（root 不会保存 sudo 信息）
- ✅ **检查 WebSocket/日志输出**中的调试信息
- ✅ **重启应用**以确保数据库迁移运行

## 数据保存条件

数据会在以下条件下保存到 `ssh_servers_sudo` 表：

1. ✅ 使用非 root 用户（`needs_sudo = True`）
2. ✅ 初始化成功完成
3. ✅ 数据库表存在
4. ✅ 用户已登录（有 `current_user.id`）

数据**不会**保存的情况：

1. ❌ 使用 root 用户登录远程服务器
2. ❌ 表不存在且迁移未运行
3. ❌ 数据库连接失败

## 检查清单

- [ ] 表 `ssh_servers_sudo` 已创建
- [ ] 应用已重启（运行迁移）
- [ ] 使用**普通用户**（非 root）测试
- [ ] 用户有 sudo 权限
- [ ] 检查日志中的调试信息
- [ ] 查询数据库确认数据已保存
