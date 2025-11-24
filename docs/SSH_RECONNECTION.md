# SSH 自动重连功能 | SSH Auto-Reconnection Feature

## 中文说明

### 问题描述
SSH 连接复用（Connection Pooling）在某些情况下可能会出现连接失效的问题，导致 SFTP 操作失败，例如：
- `Error listing directory: open failed`
- `Connection reset by peer`
- `Broken pipe`

### 解决方案
实现了带有速率限制的自动重连机制，在检测到连接问题时自动尝试重新连接。

### 主要特性

#### 1. 自动检测连接问题
系统会检测以下关键字来判断是否需要重连：
- `open failed`
- `connection`
- `broken pipe`
- `reset`

#### 2. 速率限制（防止无限循环）
- 每个连接在连接池生命周期内最多重连 3 次（可配置）
- 时间窗口基于连接池的 `max_lifetime` 设置（默认 1 小时）
- 超过限制后会提示用户等待时间
- 自动清理超过生命周期的重连记录

#### 3. 自动重试
- 重连成功后会自动重试失败的操作
- 每次操作只重试一次，避免无限循环

#### 4. 详细日志
所有重连尝试都会记录到日志中，包括：
- 重连原因
- 重连时间
- 重连结果
- 当前重连次数

### 配置参数

在 `SSHConnectionPool` 初始化时可以配置：
```python
ssh_connection_pool = SSHConnectionPool(
    idle_timeout=300,              # 空闲超时（秒）
    max_lifetime=3600,             # 最大生命周期（秒）
    cleanup_interval=60,           # 清理间隔（秒）
    max_reconnections_per_hour=3   # 每小时最大重连次数
)
```

### 使用示例

#### 正常使用
用户无需任何操作，系统会自动处理连接问题：
```python
# 文件管理操作会自动处理连接问题
ssh_manager = SSHManager()
success, files, error = await ssh_manager.list_directory("/path", server)
```

#### 错误提示
当达到重连限制时，用户会看到友好的错误信息：
```
已达到重连次数上限 (3次/60分钟)，请等待 1234 秒后重试
Reconnection limit reached (3/60 minutes), please wait 1234 seconds
```

### 监控和调试

#### 查看日志
日志会包含如下信息：
```
[SSH Pool] SFTP error detected in list_directory, attempting reconnection: open failed
[SSH Pool] Closing stale connection for reconnection: ConnectionKey(user@host:22, PASSWORD)
[SSH Pool] Creating new SSH connection after reconnect: ConnectionKey(user@host:22, PASSWORD)
[SSH Pool] Reconnection recorded for ConnectionKey(user@host:22, PASSWORD). Total attempts in last hour: 1/3
[SSH Pool] ✓ Reconnection successful: ConnectionKey(user@host:22, PASSWORD). Total connections: 1
[SSH Manager] Reconnection successful, retrying list_directory
```

#### 重连统计
重连次数会在日志中显示：
```
Total attempts in last hour: 1/3
```

---

## English Documentation

### Problem Description
SSH connection pooling may encounter stale connections that cause SFTP operations to fail, such as:
- `Error listing directory: open failed`
- `Connection reset by peer`
- `Broken pipe`

### Solution
Implemented an automatic reconnection mechanism with rate limiting that attempts to reconnect when connection issues are detected.

### Key Features

#### 1. Automatic Connection Problem Detection
The system detects these error types to determine if reconnection is needed:

**SFTP Errors:**
- `open failed`
- `connection`
- `broken pipe`
- `reset`

**SSH Connection Errors:**
- `asyncssh.ConnectionLost`
- `asyncssh.DisconnectError`
- `asyncssh.ChannelOpenError`

#### 2. Rate Limiting (Prevent Infinite Loops)
- Maximum 3 reconnections per connection within the pool's lifetime (configurable)
- Time window is based on connection pool's `max_lifetime` setting (default: 1 hour)
- Users are informed of wait time when limit is exceeded
- Automatically cleans up reconnection records older than the lifetime window

#### 3. Automatic Retry
- Automatically retries the failed operation after successful reconnection
- Each operation is retried only once to avoid infinite loops

#### 4. Detailed Logging
All reconnection attempts are logged with:
- Reconnection reason
- Reconnection timestamp
- Reconnection result
- Current reconnection count

### Configuration Parameters

Configure when initializing `SSHConnectionPool`:
```python
ssh_connection_pool = SSHConnectionPool(
    idle_timeout=300,              # Idle timeout (seconds)
    max_lifetime=3600,             # Maximum lifetime (seconds)
    cleanup_interval=60,           # Cleanup interval (seconds)
    max_reconnections_per_hour=3   # Maximum reconnections per hour
)
```

### Usage Example

#### Normal Usage
No user action required, the system automatically handles connection issues:
```python
# File operations automatically handle connection problems
ssh_manager = SSHManager()
success, files, error = await ssh_manager.list_directory("/path", server)
```

#### Error Messages
When the reconnection limit is reached, users see a friendly error message:
```
已达到重连次数上限 (3次/60分钟)，请等待 1234 秒后重试
Reconnection limit reached (3/60 minutes), please wait 1234 seconds
```

### Monitoring and Debugging

#### View Logs
Logs will include information like:
```
[SSH Pool] SFTP error detected in list_directory, attempting reconnection: open failed
[SSH Pool] Closing stale connection for reconnection: ConnectionKey(user@host:22, PASSWORD)
[SSH Pool] Creating new SSH connection after reconnect: ConnectionKey(user@host:22, PASSWORD)
[SSH Pool] Reconnection recorded for ConnectionKey(user@host:22, PASSWORD). Total attempts in last hour: 1/3
[SSH Pool] ✓ Reconnection successful: ConnectionKey(user@host:22, PASSWORD). Total connections: 1
[SSH Manager] Reconnection successful, retrying list_directory
```

#### Reconnection Statistics
Reconnection counts are shown in logs (window based on pool lifetime):
```
Total attempts in last 60 minutes: 1/3
```

## Technical Implementation

### Architecture

1. **PooledConnection**: Tracks reconnection attempts with timestamps
2. **SSHConnectionPool**: Manages connection lifecycle and enforces rate limiting
3. **SSHManager**: Detects errors and triggers reconnection through the pool

### Rate Limiting Algorithm

```python
def _can_reconnect(pooled_conn):
    now = time.time()
    window_start = now - max_lifetime  # Use pool's max_lifetime instead of fixed 1 hour
    
    # Remove old attempts (sliding window based on pool lifetime)
    pooled_conn.reconnection_attempts = [
        ts for ts in pooled_conn.reconnection_attempts if ts > window_start
    ]
    
    # Check limit
    if len(pooled_conn.reconnection_attempts) >= max_reconnections_per_hour:
        return False, "Rate limit exceeded"
    
    return True, ""
```

### Error Detection Flow

```
SFTP Operation Fails
    ↓
Check Error Message for Keywords
    ↓
Match Found? → No → Return Original Error
    ↓ Yes
Check Rate Limit
    ↓
Limit Exceeded? → Yes → Return Rate Limit Error
    ↓ No
Close Stale Connection
    ↓
Create New Connection
    ↓
Record Reconnection Attempt
    ↓
Retry Operation
    ↓
Return Result
```

## Testing

### Manual Testing
1. Cause a connection to become stale (e.g., network interruption)
2. Attempt a file operation
3. Verify reconnection happens automatically
4. Check logs for reconnection messages

### Rate Limit Testing
1. Trigger 3 reconnections within an hour
2. Attempt 4th reconnection
3. Verify rate limit error message is shown

### Log Verification
Check that logs contain:
- Connection error detection
- Reconnection attempts
- Reconnection success/failure
- Retry results
