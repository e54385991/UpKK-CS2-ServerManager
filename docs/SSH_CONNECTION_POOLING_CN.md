# SSH 连接复用覆盖场景说明

## 问题确认

**Q: SSH连接复用是否包含websocket、框架安装等所有情况？**

**A: 是的！SSH 连接池已完全覆盖所有 SSH 操作场景。**

## 覆盖的完整场景列表

### ✅ 1. 服务器部署操作
- **deploy_cs2_server()** - CS2 服务器部署
- 使用位置: `api/routes/actions.py:206`
- WebSocket 实时输出: ✓

### ✅ 2. 服务器控制操作
- **start_server()** - 启动服务器
  - 位置: `api/routes/actions.py:231`
  - WebSocket 实时输出: ✓
- **stop_server()** - 停止服务器
  - 位置: `api/routes/actions.py:249`
- **update_server()** - 更新服务器
  - 位置: `api/routes/actions.py:274`
  - WebSocket 实时输出: ✓
- **validate_server()** - 验证服务器文件
  - 位置: `api/routes/actions.py:294`
  - WebSocket 实时输出: ✓

### ✅ 3. 插件框架安装（重点！）
- **install_metamod()** - 安装 Metamod:Source
  - 位置: `api/routes/actions.py:314`
  - WebSocket 实时输出: ✓
  - 批量安装: `api/routes/actions.py:655`
  
- **install_counterstrikesharp()** - 安装 CounterStrikeSharp
  - 位置: `api/routes/actions.py:334`
  - WebSocket 实时输出: ✓
  - 批量安装: `api/routes/actions.py:657`
  
- **install_cs2fixes()** - 安装 CS2Fixes
  - 位置: `api/routes/actions.py:354`
  - WebSocket 实时输出: ✓
  - 批量安装: `api/routes/actions.py:659`

### ✅ 4. 插件更新操作
- **update_metamod()** - 更新 Metamod
  - 位置: `api/routes/actions.py:374`
  - WebSocket 实时输出: ✓
  
- **update_counterstrikesharp()** - 更新 CounterStrikeSharp
  - 位置: `api/routes/actions.py:394`
  - WebSocket 实时输出: ✓
  
- **update_cs2fixes()** - 更新 CS2Fixes
  - 位置: `api/routes/actions.py:414`
  - WebSocket 实时输出: ✓

### ✅ 5. WebSocket 交互式 SSH 终端
- **ssh_console_websocket()** - SSH 控制台
  - 位置: `api/routes/actions.py:723`
  - 实时命令执行: ✓
  - 交互式终端: ✓

### ✅ 6. 文件管理操作
- **list_directory()** - 列出目录
  - 位置: `api/routes/file_manager.py:107`
- **read_file()** - 读取文件
  - 位置: `api/routes/file_manager.py:137`
- **write_file()** - 写入文件
  - 位置: `api/routes/file_manager.py:168`
- **create_directory()** - 创建目录
  - 位置: `api/routes/file_manager.py:215`
- **delete_path()** - 删除文件/目录
  - 位置: `api/routes/file_manager.py:263`
- **rename_path()** - 重命名/移动
  - 位置: `api/routes/file_manager.py:312`
- **upload_file()** - 上传文件
  - 位置: `api/routes/file_manager.py:349`

### ✅ 7. 服务器监控
- **server_monitor** - 服务器状态监控
  - 位置: `main.py:94`
  - 持续监控: ✓

### ✅ 8. 服务器信息查询
- **get CPU info** - 获取 CPU 信息
  - 位置: `api/routes/servers.py:215`
- **get server status** - 获取服务器状态
  - 位置: `api/routes/servers.py:483`

## 技术实现细节

### 默认启用连接池

所有 `SSHManager()` 实例默认使用连接池：

```python
# 默认行为 - 自动使用连接池
ssh_manager = SSHManager()  # use_pool=True (默认)

# 如需禁用（通常不需要）
ssh_manager = SSHManager(use_pool=False)
```

### 连接复用机制

当多个操作在同一服务器上执行时：

```python
# 场景：在同一物理服务器上执行多个操作
server1 = Server(host="192.168.1.100", ssh_port=22, ssh_user="cs2user")
server2 = Server(host="192.168.1.100", ssh_port=22, ssh_user="cs2user")

# 操作 1: 部署 server1
ssh_mgr1 = SSHManager()
await ssh_mgr1.connect(server1)  # 创建新连接
await ssh_mgr1.deploy_cs2_server(server1)
await ssh_mgr1.disconnect()  # 释放到连接池（不关闭）

# 操作 2: 安装插件到 server2
ssh_mgr2 = SSHManager()
await ssh_mgr2.connect(server2)  # ✓ 复用连接！
await ssh_mgr2.install_metamod(server2)
await ssh_mgr2.disconnect()  # 释放到连接池
```

### WebSocket 实时输出也受益

所有带 `progress_callback` 的操作都支持 WebSocket 实时输出，并且都使用连接池：

```python
# WebSocket 部署示例
await ssh_manager.deploy_cs2_server(
    server, 
    lambda msg: asyncio.create_task(
        send_deployment_update(server_id, "output", msg)
    )
)
# ↑ 使用连接池 + WebSocket 实时输出
```

## 性能提升实例

### 示例 1: 批量安装插件

**场景**: 在同一服务器上安装 3 个插件（Metamod, CounterStrikeSharp, CS2Fixes）

**之前**:
```
连接 1 → 安装 Metamod → 断开
连接 2 → 安装 CSS → 断开
连接 3 → 安装 CS2Fixes → 断开
总连接: 3 次 × 300ms = 900ms 开销
```

**现在（有连接池）**:
```
连接 1 → 安装 Metamod → 释放
复用连接 1 → 安装 CSS → 释放
复用连接 1 → 安装 CS2Fixes → 释放
总连接: 1 次 × 300ms = 300ms 开销
节省: 67% 连接开销 ✓
```

### 示例 2: WebSocket 实时部署

**场景**: 通过 WebSocket 部署服务器并实时查看输出

**之前**:
```
连接 → 执行命令 1 → 断开 → WebSocket 输出
连接 → 执行命令 2 → 断开 → WebSocket 输出
连接 → 执行命令 3 → 断开 → WebSocket 输出
总连接: 多次连接，每次都有握手开销
```

**现在（有连接池）**:
```
连接（仅一次）→ 执行所有命令 → WebSocket 实时输出 → 释放
总连接: 1 次连接，复用整个部署过程
性能提升: 显著！✓
```

### 示例 3: 多服务器同主机场景

**场景**: 管理 5 个 CS2 服务器实例在同一物理机上

**之前**:
```
每个操作 × 5 服务器 = 5 个独立连接
更新操作: 5 × 2 连接 = 10 连接
```

**现在（有连接池）**:
```
5 个服务器共享 1 个连接
更新操作: 1 个连接被复用 5 次
连接减少: 80% ✓
```

## 连接池状态监控

### API 端点

```bash
# 查看连接池统计
curl http://your-server/api/server-status/pool/stats
```

### 返回示例

```json
{
  "success": true,
  "pool_stats": {
    "total_connections": 3,      // 总连接数
    "alive_connections": 3,      // 活跃连接
    "in_use_connections": 1,     // 正在使用的连接
    "idle_connections": 2,       // 空闲连接（可复用）
    "idle_timeout": 300,         // 空闲超时（秒）
    "max_lifetime": 3600         // 最大生命周期（秒）
  }
}
```

## 验证连接复用

### 方法 1: 查看日志

启动应用时，查看日志输出：

```
SSH connection pool started
Created new SSH connection: ConnectionKey(cs2user@192.168.1.100:22, PASSWORD)
Reusing existing connection: ConnectionKey(cs2user@192.168.1.100:22, PASSWORD)
Reusing existing connection: ConnectionKey(cs2user@192.168.1.100:22, PASSWORD)
```

### 方法 2: 监控连接数

在操作前后查看连接池状态：

```bash
# 操作前
curl http://your-server/api/server-status/pool/stats
# total_connections: 0

# 执行部署操作...

# 操作中
curl http://your-server/api/server-status/pool/stats
# total_connections: 1, in_use_connections: 1

# 操作后
curl http://your-server/api/server-status/pool/stats
# total_connections: 1, in_use_connections: 0, idle_connections: 1
```

### 方法 3: 批量操作测试

批量安装插件时，观察只创建一次连接：

```python
# 批量安装 API: POST /api/servers/install-plugins
# 请求体: {"server_ids": [1, 2, 3], "plugins": ["metamod", "counterstrikesharp"]}

# 日志输出应该是：
# Created new SSH connection: ... (仅一次)
# Reusing existing connection: ... (后续所有操作)
```

## 总结

✅ **完全覆盖**: 所有 SSH 操作（包括 WebSocket 和插件安装）都使用连接池

✅ **自动启用**: 默认行为，无需代码修改

✅ **性能提升**: 
- 同服务器操作减少 67-90% 的连接开销
- WebSocket 实时输出更流畅
- 批量操作显著加速

✅ **向后兼容**: 现有代码无需任何修改

✅ **监控支持**: 提供 API 端点实时查看连接池状态

## 相关文档

- [SSH Connection Pooling 详细文档](./SSH_CONNECTION_POOLING.md) - 英文完整版
- [性能优化指南](./SSH_CONNECTION_POOLING.md#performance-benefits) - 性能指标
- [故障排查](./SSH_CONNECTION_POOLING.md#troubleshooting) - 问题诊断
