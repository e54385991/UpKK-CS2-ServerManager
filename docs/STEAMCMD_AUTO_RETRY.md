# SteamCMD Auto-Retry Feature | SteamCMD 自动重试功能

[English](#english) | [中文](#chinese)

---

<a name="english"></a>
## English

### Overview

The CS2 Server Manager now includes automatic retry functionality for SteamCMD operations. When SteamCMD encounters network errors or temporary failures during deployment, updates, or validation, the system will automatically retry the operation instead of failing immediately.

### Features

- **Automatic Retry**: Up to 3 automatic retries for failed SteamCMD operations
- **Exponential Backoff**: Progressive delays between retry attempts (5, 10, 20 seconds)
- **Smart Error Detection**: Only retries on network-related or temporary errors
- **No Infinite Loops**: Maximum retry limit prevents endless retry cycles
- **Real-time Progress**: Progress messages show retry attempts and delays
- **Comprehensive Logging**: All retry attempts are logged for troubleshooting

### Configuration

The retry mechanism is configured with the following default values:

```python
STEAMCMD_MAX_RETRIES = 3  # Maximum number of retry attempts
STEAMCMD_RETRY_DELAY = 5  # Initial delay in seconds (uses exponential backoff)
```

### Retry Logic

The system will retry SteamCMD operations when encountering:

- Network timeouts
- Connection failures
- Download interruptions
- HTTP errors
- Corrupt file downloads
- Temporary server issues

The system will **NOT** retry on:

- Authentication failures
- Permission errors
- Disk space issues
- Invalid configuration

### Retry Delays

The retry mechanism uses exponential backoff:

1. **First retry**: 5 seconds delay
2. **Second retry**: 10 seconds delay (5 × 2¹)
3. **Third retry**: 20 seconds delay (5 × 2²)

### Affected Operations

The auto-retry feature is enabled for the following operations:

1. **Server Deployment** (`deploy_cs2_server`)
   - Initial CS2 server installation via SteamCMD
   - Downloads ~30GB of game files

2. **Server Updates** (`update_server`)
   - Updates CS2 server files to the latest version
   - Downloads only changed files

3. **Server Validation** (`validate_server`)
   - Validates and repairs server files
   - Re-downloads corrupted or missing files

### Example Usage

When deploying a server, you'll see messages like:

```
Installing CS2 server via SteamCMD...
Auto-retry is enabled: up to 3 automatic retries on network errors
...
⚠ SteamCMD failed with retryable error: Connection timeout
⏳ Retry attempt 1/3 - waiting 5 seconds before retry...
🔄 Starting retry attempt 1/3...
...
✓ SteamCMD command succeeded on retry attempt 1/3
```

### Benefits

- **Improved Reliability**: Automatic recovery from temporary network issues
- **Reduced Manual Intervention**: No need to manually retry failed operations
- **Better User Experience**: Progress messages keep users informed
- **Production Ready**: Safe for automated deployments and scheduled tasks

---

<a name="chinese"></a>
## 中文

### 概述

CS2 服务器管理器现在包含 SteamCMD 操作的自动重试功能。当 SteamCMD 在部署、更新或验证期间遇到网络错误或临时故障时，系统将自动重试操作，而不是立即失败。

### 功能特性

- **自动重试**: 失败的 SteamCMD 操作最多自动重试 3 次
- **指数退避**: 重试尝试之间的渐进式延迟（5、10、20 秒）
- **智能错误检测**: 仅对网络相关或临时错误进行重试
- **无限循环保护**: 最大重试限制防止无休止的重试循环
- **实时进度**: 进度消息显示重试尝试和延迟
- **全面日志记录**: 记录所有重试尝试以便故障排除

### 配置

重试机制配置了以下默认值：

```python
STEAMCMD_MAX_RETRIES = 3  # 最大重试次数
STEAMCMD_RETRY_DELAY = 5  # 初始延迟秒数（使用指数退避）
```

### 重试逻辑

系统会在遇到以下情况时重试 SteamCMD 操作：

- 网络超时
- 连接失败
- 下载中断
- HTTP 错误
- 文件下载损坏
- 临时服务器问题

系统**不会**在以下情况下重试：

- 身份验证失败
- 权限错误
- 磁盘空间不足
- 无效配置

### 重试延迟

重试机制使用指数退避：

1. **第一次重试**: 延迟 5 秒
2. **第二次重试**: 延迟 10 秒（5 × 2¹）
3. **第三次重试**: 延迟 20 秒（5 × 2²）

### 受影响的操作

以下操作启用了自动重试功能：

1. **服务器部署** (`deploy_cs2_server`)
   - 通过 SteamCMD 初始安装 CS2 服务器
   - 下载约 30GB 的游戏文件

2. **服务器更新** (`update_server`)
   - 将 CS2 服务器文件更新到最新版本
   - 仅下载已更改的文件

3. **服务器验证** (`validate_server`)
   - 验证和修复服务器文件
   - 重新下载损坏或丢失的文件

### 使用示例

部署服务器时，您会看到类似以下的消息：

```
Installing CS2 server via SteamCMD...
Auto-retry is enabled: up to 3 automatic retries on network errors
...
⚠ SteamCMD failed with retryable error: Connection timeout
⏳ Retry attempt 1/3 - waiting 5 seconds before retry...
🔄 Starting retry attempt 1/3...
...
✓ SteamCMD command succeeded on retry attempt 1/3
```

### 优势

- **提高可靠性**: 自动从临时网络问题中恢复
- **减少手动干预**: 无需手动重试失败的操作
- **更好的用户体验**: 进度消息让用户随时了解情况
- **生产就绪**: 适用于自动化部署和计划任务

---

## Technical Details | 技术细节

### Implementation

The retry mechanism is implemented in the `SSHManager` class as a new method:

```python
async def _execute_steamcmd_with_retry(
    self, 
    command: str, 
    server: Server,
    progress_callback=None,
    timeout: int = 1800,
    max_retries: int = None
) -> Tuple[bool, str, str]
```

This method wraps the `execute_command_streaming` method and adds:
- Retry loop with configurable maximum attempts
- Exponential backoff delay calculation
- Error classification (retryable vs non-retryable)
- Progress reporting for retry attempts
- Automatic cleanup of stale SteamCMD processes before retry

### Error Classification

Retryable errors include keywords:
- `timeout`, `timed out`
- `connection`, `network`
- `failed to download`, `download failed`
- `corrupt`, `error downloading`
- `unable to download`, `http error`
- `failed to install`, `no connection`

### Logging

All retry attempts are logged with appropriate severity:
- **WARNING**: Individual retry attempts
- **ERROR**: Final failure after exhausting all retries
- **INFO**: Successful retry (implicit in success logging)

### Testing

To test the retry mechanism manually:
1. Deploy a new server and observe the installation process
2. Simulate a network interruption during update
3. Check logs for retry messages and exponential backoff delays

---

## Changelog | 更新日志

### Version 1.0.0 (2025-12-02)

#### Added | 新增
- Initial implementation of SteamCMD auto-retry feature
- Exponential backoff mechanism
- Smart error classification
- Comprehensive logging for retry attempts
- Support for deploy, update, and validate operations

#### Changed | 变更
- Modified `deploy_cs2_server` to use retry mechanism
- Modified `update_server` to use retry mechanism
- Modified `validate_server` to use retry mechanism
- Added progress messages for retry status

#### Technical | 技术
- Added `STEAMCMD_MAX_RETRIES` constant (default: 3)
- Added `STEAMCMD_RETRY_DELAY` constant (default: 5 seconds)
- Added `_execute_steamcmd_with_retry` method to `SSHManager` class
