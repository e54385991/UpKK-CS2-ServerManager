# SSH 健康监控系统

## 概述

SSH 健康监控系统是一个独立的后台守护进程，持续监控所有托管服务器的 SSH 连接性。它提供从瞬态问题的自动恢复，并防止在永久不可达的服务器上浪费资源。

## 核心功能

### 1. **独立后台守护进程**
- 在后台线程中完全独立运行
- 非阻塞 - 不干扰正常服务器操作
- 随应用程序自动启动
- 可配置检查间隔（默认：2小时）

### 2. **渐进式故障检测**
系统使用多层方法评估服务器健康状态：

- **正常(Healthy)**: SSH 连接成功，无问题
- **异常(Degraded)**: 1-2次连续失败，服务器可能暂时不可达
- **离线(Down)**: 3次以上连续失败，服务器标记为离线
- **完全离线(Completely Down)**: 84次以上连续失败（2小时间隔下为7天）

### 3. **自动恢复**
- 自动检测先前离线的服务器何时恢复可用
- 连接成功时重置失败计数器
- 瞬态问题无需人工干预

### 4. **资源保护**
- "完全离线"状态的服务器不会自动检查
- 防止在永久离线的服务器上浪费资源
- 需要从管理界面手动重连才能恢复

### 5. **可视化状态指示器**
`/servers-ui` 中的服务器卡片显示：
- SSH 连接状态及颜色编码
- 连续失败次数
- 估计离线时长
- 上次探测时间
- 完全离线服务器的手动重连按钮

## 架构设计

### 数据库架构

添加到 `servers` 表的新字段：

```sql
-- SSH 健康监控配置
enable_ssh_health_monitoring TINYINT(1) DEFAULT 1
ssh_health_check_interval_hours INT DEFAULT 2
ssh_health_failure_threshold INT DEFAULT 84
last_ssh_health_check TIMESTAMP NULL
ssh_health_status VARCHAR(50) DEFAULT 'unknown'
```

### 服务组件

#### 1. SSH 健康监控服务 (`services/ssh_health_monitor.py`)

主守护进程服务，负责：
- 运行持续后台循环
- 检查所有启用监控的服务器
- 遵守各服务器的检查间隔
- 更新数据库中的健康状态
- 提供手动重连 API

#### 2. 数据库迁移 (`modules/database.py`)

自动模式迁移：
- 如果不存在则添加新列
- 设置适当的默认值
- 保持向后兼容性

#### 3. API 端点 (`api/routes/servers.py`)

两个新端点：

**GET `/servers/{server_id}/ssh-health`**
- 返回详细的 SSH 健康状态
- 计算离线时长估计
- 需要身份验证

**POST `/servers/{server_id}/ssh-reconnect`**
- 手动测试连接并重置健康状态
- 用于"完全离线"的服务器
- 需要身份验证

#### 4. UI 组件 (`templates/servers.html`)

- 服务器卡片显示 SSH 健康状态
- 颜色编码的状态指示器
- 失败计数和离线估计
- 手动重连按钮
- 所有文本的 i18n 支持

## 配置

### 服务器级设置

每个服务器都有独立配置：

```python
enable_ssh_health_monitoring: bool = True  # 启用/禁用监控
ssh_health_check_interval_hours: int = 2   # 检查频率（小时）
ssh_health_failure_threshold: int = 84     # 完全离线前的失败次数
```

### 默认行为

默认情况下，所有服务器具有：
- 启用监控
- 2小时检查间隔
- 84次失败阈值（7天）

这意味着服务器每2小时检查一次，84次连续失败后（约7天），将被标记为"完全离线"。

## 状态转换

```
未知(unknown) → 正常(healthy) (首次成功检查)
正常(healthy) → 异常(degraded) (首次失败)
异常(degraded) → 正常(healthy) (成功检查)
异常(degraded) → 离线(down) (3次以上失败)
离线(down) → 正常(healthy) (成功检查)
离线(down) → 完全离线(completely_down) (84次以上失败)
完全离线(completely_down) → 正常(healthy) (仅通过手动重连)
```

## 使用指南

### 最终用户

1. **查看 SSH 健康状态**
   - 导航到 `/servers-ui`
   - 每个服务器卡片显示 SSH 连接状态
   - 颜色编码：
     - 绿色：正常
     - 黄色：异常
     - 红色：完全离线

2. **手动重连**
   - 如果服务器显示"完全离线"
   - 点击重连按钮 (⟳)
   - 系统将测试连接，成功则重置状态

### 管理员

1. **监控配置**
   - 通过数据库或 API 编辑服务器
   - 调整 `ssh_health_check_interval_hours` 以改变检查频率
   - 调整 `ssh_health_failure_threshold` 以改变容忍水平

2. **禁用监控**
   - 为特定服务器设置 `enable_ssh_health_monitoring = False`
   - 适用于故意离线的服务器

## 技术细节

### 检查流程

1. 守护进程循环每60秒运行一次
2. 对于每个启用监控的服务器：
   - 根据间隔计算下次检查时间
   - 如果还没到时间则跳过
   - 如果在最近30秒内检查过则跳过（去重）
   - 如果状态为"完全离线"则跳过
3. 尝试 SSH 连接，超时时间10秒
4. 更新数据库结果
5. 记录状态变化

### 失败跟踪

```python
# 失败时
consecutive_ssh_failures += 1
last_ssh_failure = now
is_ssh_down = (consecutive_ssh_failures >= 3)
ssh_health_status = determine_status(consecutive_ssh_failures, threshold)

# 成功时
consecutive_ssh_failures = 0
last_ssh_success = now
is_ssh_down = False
ssh_health_status = "healthy"
```

### 性能考虑

- 使用连接池提高效率
- 10秒超时防止挂起
- 非阻塞异步操作
- 数据库更新快速且原子化
- 对服务器操作无性能影响

## 日志记录

系统记录重要事件：

```python
# Info 级别
- 监控启动/停止
- 执行的健康检查
- 状态变化

# Warning 级别
- 异常状态
- 达到失败阈值

# Error 级别
- 服务器标记为完全离线
- 连接错误
```

## API 响应示例

### SSH 健康状态

```json
{
  "server_id": 1,
  "ssh_health_status": "degraded",
  "consecutive_failures": 5,
  "failure_threshold": 84,
  "is_ssh_down": true,
  "last_ssh_success": "2024-01-01T10:00:00",
  "last_ssh_failure": "2024-01-01T20:00:00",
  "last_health_check": "2024-01-01T20:00:00",
  "check_interval_hours": 2,
  "offline_duration_estimate": {
    "hours": 10,
    "days": 0.4,
    "description": "~10 小时 (0.4 天)"
  },
  "monitoring_enabled": true
}
```

### 手动重连成功

```json
{
  "success": true,
  "message": "SSH 连接成功 - 服务器健康已恢复",
  "ssh_health_status": "healthy"
}
```

### 手动重连失败

```json
{
  "success": false,
  "message": "SSH 连接失败 - 服务器仍然不可达",
  "ssh_health_status": "completely_down"
}
```

## 故障排除

### 问题：服务器未被监控

**检查：**
1. `enable_ssh_health_monitoring` 是否为 `True`
2. 守护进程是否运行（检查启动日志）
3. 服务器凭据是否正确

### 问题：误报（服务器标记为离线但实际正常）

**解决方案：**
1. 如果网络慢，增加 `ssh_health_check_interval_hours`
2. 检查防火墙规则
3. 验证服务器上 SSH 服务正在运行
4. 检查认证凭据

### 问题：检查过于频繁

**解决方案：**
- 增加 `ssh_health_check_interval_hours` 以降低频率
- 默认为2小时，可设置为4、6、12或24小时

## 未来增强

可能的改进：

1. **邮件/Webhook 通知**
   - 服务器离线时警报
   - 达到完全离线阈值时警报

2. **可配置的检查模式**
   - 一天中不同时间使用不同间隔
   - 工作时间采用更积极的检查

3. **健康趋势**
   - 跟踪历史在线/离线时间
   - 生成可用性报告

4. **智能阈值**
   - 基于服务器历史的自适应阈值
   - 不同服务器类型使用不同阈值

## 与现有功能集成

### SSH 连接池
- 健康监控器在可用时使用连接池
- 遵守现有连接管理
- 无重复连接

### 服务器操作
- 操作在尝试前检查 `is_ssh_down`
- 防止在已知离线的服务器上浪费时间
- 如果服务器离线，通知用户

### 自动重启
- 自动重启遵守 SSH 健康状态
- 不会尝试重启完全离线的服务器
- 防止重启循环

## 迁移说明

升级到此版本时：

1. 数据库迁移在启动时自动运行
2. 所有现有服务器默认启用监控
3. 初始状态为"未知"，直到首次检查
4. 首次检查在启动后2小时内进行
5. 用户无需任何操作

## 总结

SSH 健康监控系统提供：
- ✅ 自动后台监控
- ✅ 渐进式故障检测
- ✅ 自动恢复
- ✅ 资源保护
- ✅ 可视化状态指示器
- ✅ 手动重连功能
- ✅ 可配置的间隔和阈值
- ✅ 非阻塞操作
- ✅ 完整的 i18n 支持

这一增强功能通过提供主动监控和智能资源管理，显著改善了 SSH 连接性的服务器管理。
