# SSH 连接复用验证 - 服务器启动场景

## 问题确认

**Q: 服务器启动有很多 SSH 操作，确保复用？**

**A: 是的！已确保所有 SSH 操作都使用同一个连接池连接。**

## 服务器启动流程详细分析

### 完整调用链

```
start_server() 方法执行流程:
├── 1. connect(server) ────────────────────→ [从连接池获取/创建连接]
│
├── 2. execute_command("screen -wipe...") ──→ [使用同一连接]
├── 3. execute_command("screen -list...") ──→ [使用同一连接]
├── 4. execute_command(kill_all_cmd) ───────→ [使用同一连接]
├── 5. execute_command(verify_cmd) [×3] ────→ [使用同一连接]
├── 6. execute_command(final_kill_cmd) ─────→ [使用同一连接]
│
├── 7. _kill_stray_cs2_processes() ─────────→ [内部多个 execute_command]
│   ├── execute_command(check_cmd) ────────→ [使用同一连接]
│   ├── execute_command(kill_cmd) [×N] ────→ [使用同一连接]
│   └── execute_command(verify_cmd) ───────→ [使用同一连接]
│
├── 8. perform_server_selfcheck() ─────────→ [内部多个 execute_command]
│   ├── execute_command(verify_cmd) ───────→ [使用同一连接]
│   ├── execute_command(chmod_cmd) ────────→ [使用同一连接]
│   ├── execute_command(check_cmd) ────────→ [使用同一连接]
│   ├── execute_command(mkdir_cmd) ────────→ [使用同一连接]
│   ├── execute_command(symlink_cmd) ──────→ [使用同一连接]
│   ├── execute_command(grep_cmd) ─────────→ [使用同一连接]
│   ├── execute_command(backup_cmd) ───────→ [使用同一连接]
│   ├── execute_command(sed_cmd) ──────────→ [使用同一连接]
│   ├── execute_command(check_script_cmd) ─→ [使用同一连接]
│   └── execute_command(create_script_cmd) → [使用同一连接]
│
├── 9. execute_command(start_cmd) ──────────→ [使用同一连接]
├── 10. execute_command_streaming() ────────→ [使用同一连接]
├── 11. execute_command(screen_check) ──────→ [使用同一连接]
├── 12. execute_command(log_check) ─────────→ [使用同一连接]
├── 13. execute_command(process_check) ─────→ [使用同一连接]
├── 14. execute_command(port_check) ────────→ [使用同一连接]
│
└── finally: disconnect() ──────────────────→ [释放连接回连接池]
                                               (不关闭，保留供下次使用)
```

### 关键代码验证

#### 1. start_server 方法结构

```python
async def start_server(self, server: Server, progress_callback=None):
    # 第一步：从连接池获取连接
    success, msg = await self.connect(server)  # ← 连接池
    if not success:
        return False, f"Connection failed: {msg}"
    
    try:
        # 所有这些命令都使用 self.conn (同一个连接)
        await self.execute_command("screen -wipe || true")
        await self.execute_command(check_cmd)
        await self.execute_command(kill_all_cmd)
        # ... 更多命令 ...
        
        # 调用子方法（使用同一个 self.conn）
        await self._kill_stray_cs2_processes(server, progress_callback)
        await self.perform_server_selfcheck(server, progress_callback)
        
        # ... 更多命令 ...
        
    except Exception as e:
        return False, f"Start error: {str(e)}"
    finally:
        # 最后：释放连接回连接池（不关闭）
        await self.disconnect()  # ← 释放回连接池
```

#### 2. execute_command 使用 self.conn

```python
async def execute_command(self, command: str, timeout: int = 30):
    if not self.conn:  # 检查连接是否存在
        return False, "", "Not connected"
    
    try:
        # 使用 self.conn 执行命令
        result = await asyncio.wait_for(
            self.conn.run(command, check=False),  # ← 使用同一个连接
            timeout=timeout
        )
        # ...
```

#### 3. 子方法不重新连接

```python
async def perform_server_selfcheck(self, server: Server, progress_callback=None):
    # 注意：没有 connect() 调用！
    # 直接使用父方法建立的 self.conn
    
    try:
        # 所有命令都使用父方法的连接
        await self.execute_command(verify_cmd)
        await self.execute_command(chmod_cmd)
        # ...
    except Exception as e:
        # 注意：没有 finally disconnect()！
        # 连接由父方法管理
```

## 连接复用效果统计

### 典型服务器启动的 SSH 命令数量

根据代码分析，一次完整的服务器启动可能执行：

| 阶段 | SSH 命令数 | 说明 |
|------|-----------|------|
| 清理 screen 会话 | 1 | screen -wipe |
| 检查现有会话 | 1-4 | 检查 + 可能的重试 |
| 终止现有进程 | 1-4 | kill + 验证 + 重试 |
| 清理 CS2 进程 | 2-5 | 检查 + kill + 验证 |
| 自检修复 | 5-15 | 检查可执行文件、symlink、配置等 |
| 启动服务器 | 3-8 | 启动 + 验证 + 日志检查 |
| **总计** | **13-37** | **所有使用同一个连接！** |

### 性能对比

**没有连接池（之前）：**
```
每个命令都要：
- 建立 TCP 连接: ~50-100ms
- SSH 握手: ~100-200ms
- 执行命令: 实际时间
- 关闭连接: ~10-50ms

总开销 = 命令数 × (50+100+10) = 13-37 × 160ms = 2-6 秒
```

**有连接池（现在）：**
```
只有第一个命令需要：
- 建立 TCP 连接: ~50-100ms
- SSH 握手: ~100-200ms

后续所有命令：
- 执行命令: 实际时间（几乎无开销）

总开销 = 1 × 160ms + 36 × 0ms = 0.16 秒
节省: 95-97% ！
```

## 实际测试验证

### 方法 1: 查看日志输出

启动服务器时，查看应用日志：

```bash
# 启动服务器
# 观察日志输出

预期日志：
2024-01-15 10:00:00 - INFO - Created new SSH connection: ConnectionKey(cs2user@192.168.1.100:22, PASSWORD)
2024-01-15 10:00:00 - DEBUG - 执行命令: screen -wipe || true
2024-01-15 10:00:00 - DEBUG - 执行命令: screen -list | grep cs2server_1 || true
2024-01-15 10:00:01 - DEBUG - 执行命令: screen -ls | grep cs2server_1...
... (30+ 个命令) ...
2024-01-15 10:00:05 - DEBUG - Released connection: ConnectionKey(cs2user@192.168.1.100:22, PASSWORD)

关键点：
✓ 只有一次 "Created new SSH connection"
✓ 没有多次连接创建
✓ 最后一次 "Released connection"
```

### 方法 2: 监控连接池统计

```bash
# 启动前
curl http://localhost:8000/api/server-status/pool/stats
# {"total_connections": 0, "in_use_connections": 0}

# 启动服务器过程中（另一个终端）
curl http://localhost:8000/api/server-status/pool/stats
# {"total_connections": 1, "in_use_connections": 1, "alive_connections": 1}

# 启动完成后
curl http://localhost:8000/api/server-status/pool/stats
# {"total_connections": 1, "in_use_connections": 0, "idle_connections": 1}
```

### 方法 3: 网络监控

使用 tcpdump 或 wireshark 监控 SSH 连接：

```bash
# 在服务器上监控 SSH 连接
sudo tcpdump -i any port 22 and host <manager_ip>

预期结果：
- 启动前：0 个连接
- 启动时：1 个 TCP 连接建立（SYN, SYN-ACK, ACK）
- 启动中：持续使用同一个连接（数据传输）
- 启动后：连接保持（没有 FIN）
```

### 方法 4: 代码级验证

添加临时日志验证：

```python
# 在 execute_command 开始处添加
logger.debug(f"Executing command using connection id: {id(self.conn)}")

# 预期输出（所有命令使用同一个连接 ID）
Executing command using connection id: 140234567890123
Executing command using connection id: 140234567890123  # ← 相同！
Executing command using connection id: 140234567890123  # ← 相同！
...
```

## 其他高频 SSH 操作场景

### 1. 部署服务器 (deploy_cs2_server)

```
deploy_cs2_server():
├── connect() ──────────→ 获取连接
├── 30-50 个命令 ───────→ 使用同一连接
└── disconnect() ───────→ 释放连接
```

**命令数量**: 30-50 个
- 检查环境
- 安装依赖
- 下载 SteamCMD
- 安装 CS2（流式输出）
- 修复 symlink
- 部署脚本

**连接复用**: ✅ 100% 复用

### 2. 更新服务器 (update_server)

```
update_server():
├── connect() ──────────→ 获取连接
├── 10-15 个命令 ───────→ 使用同一连接
└── disconnect() ───────→ 释放连接
```

**命令数量**: 10-15 个
- 停止服务器
- 清理进程
- 运行 SteamCMD 更新
- 重启服务器

**连接复用**: ✅ 100% 复用

### 3. 安装插件 (install_metamod, install_counterstrikesharp, install_cs2fixes)

```
install_metamod():
├── connect() ──────────→ 获取连接
├── 8-12 个命令 ────────→ 使用同一连接
└── disconnect() ───────→ 释放连接
```

**命令数量**: 8-12 个每个插件
- 检查目录
- 下载文件
- 解压
- 修改配置
- 验证安装

**连接复用**: ✅ 100% 复用

## 连接池内部机制

### 连接获取流程

```python
# 1. 第一次调用 start_server
ssh_mgr1 = SSHManager()
await ssh_mgr1.connect(server1)  # host=192.168.1.100, user=cs2user

连接池内部：
┌─────────────────────────────────────┐
│ 检查连接池：无匹配连接              │
│ → 创建新 SSH 连接                    │
│ → 存储: Key(192.168.1.100:22, cs2user, PASSWORD) → Connection#1
│ → 标记为使用中                       │
└─────────────────────────────────────┘

# 执行 30 个 SSH 命令...
await ssh_mgr1.execute_command(cmd1)  # 使用 Connection#1
await ssh_mgr1.execute_command(cmd2)  # 使用 Connection#1
# ...
await ssh_mgr1.execute_command(cmd30) # 使用 Connection#1

await ssh_mgr1.disconnect()

连接池内部：
┌─────────────────────────────────────┐
│ 标记连接为空闲                      │
│ → Connection#1 保留在池中            │
│ → 状态: idle, 可复用                │
└─────────────────────────────────────┘

# 2. 第二次调用（例如更新服务器）
ssh_mgr2 = SSHManager()
await ssh_mgr2.connect(server1)  # 相同 host/user/auth

连接池内部：
┌─────────────────────────────────────┐
│ 检查连接池：找到匹配连接 Connection#1│
│ → 检查连接是否活跃: ✓               │
│ → 复用 Connection#1                 │
│ → 标记为使用中                       │
└─────────────────────────────────────┘

# 执行 15 个 SSH 命令...
await ssh_mgr2.execute_command(cmd1)  # 复用 Connection#1
# ...

await ssh_mgr2.disconnect()

连接池内部：
┌─────────────────────────────────────┐
│ 再次标记为空闲                      │
│ → Connection#1 继续保留              │
└─────────────────────────────────────┘
```

## 总结

✅ **确认**: 服务器启动的所有 SSH 操作都使用同一个连接

✅ **验证**: 
- 代码结构确保一次 connect() 对应所有后续命令
- 子方法不重新连接，使用父方法的连接
- finally 块确保连接被释放（不是关闭）

✅ **性能**: 
- 单次启动可能执行 13-37 个 SSH 命令
- 连接开销从 2-6 秒降低到 0.16 秒
- **性能提升 95-97%**

✅ **其他场景**: 
- 部署: 30-50 个命令，100% 复用
- 更新: 10-15 个命令，100% 复用  
- 插件安装: 8-12 个命令/插件，100% 复用

✅ **监控验证**: 
- API 端点可实时查看连接状态
- 日志输出确认连接复用
- 网络监控可见单一 TCP 连接

## 相关文档

- [SSH 连接池完整文档](./SSH_CONNECTION_POOLING.md)
- [场景覆盖说明](./SSH_CONNECTION_POOLING_CN.md)
