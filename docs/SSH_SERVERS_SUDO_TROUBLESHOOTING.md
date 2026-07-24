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

### 2. 在启动应用前运行 Alembic 迁移

应用启动只检查数据库版本，**不会**创建表或执行迁移。先配置
`CREDENTIAL_ENCRYPTION_KEYS`、`CREDENTIAL_ACTIVE_KEY_ID` 和
`TOKEN_HASH_KEY`，再通过独立部署步骤升级数据库：

```bash
uv run python -m cs2_manager.cli migrate
```

容器生产部署可使用迁移 profile：

```console
docker compose -f docker-compose.production.yml --profile migrate run --rm migrate
```

迁移失败时应停止发布并检查日志、数据库权限和加密密钥。不要手工创建表：
手写结构无法获得正确的 Alembic 版本、索引和凭据 shadow 列，会导致应用拒绝启动或凭据无法解密。

### 3. 验证迁移版本和表结构

先确认迁移已到当前版本：

```bash
uv run alembic current
uv run alembic heads
```

```sql
DESCRIBE ssh_servers_sudo;
```

具体字段以当前 Alembic revision 为准；凭据会使用带 key version 的 AES-256-GCM
密文列，旧明文列只可能在分阶段迁移兼容窗口内存在。

### 4. 测试保存功能

使用**非 root 用户**进行服务器初始化测试：

1. 访问 setup wizard
2. 使用具有 sudo 权限的普通用户（如 `ubuntu`, `admin` 等）
3. 完成初始化
4. 检查日志中的调试信息：
   - `调试信息: needs_sudo=True/False, sudo_pass=已设置/未设置`
   - `保存 sudo 配置到数据库...`
   - `✓ sudo 配置已保存 (用户: xxx, 类型: 无密码 sudo/带密码 sudo)`

### 5. 查询数据

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
- ✅ **启动应用前独立运行迁移**，并确认 Alembic revision 为 current

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
- [ ] 已独立运行 Alembic 迁移且版本为 current
- [ ] 使用**普通用户**（非 root）测试
- [ ] 用户有 sudo 权限
- [ ] 检查日志中的调试信息
- [ ] 查询数据库确认数据已保存
