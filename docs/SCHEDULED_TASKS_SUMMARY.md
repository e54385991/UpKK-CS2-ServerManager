# Scheduled Tasks Feature - Implementation Summary

## 问题陈述 (Problem Statement)
增加新功能 计划任务功能 比如可设置循环 比如每天一次重启等 在配置中

Translation: Add new feature: scheduled task function, for example, can set loop, such as restart once a day, etc. in configuration

## 已实现功能 (Implemented Features)

### 1. 数据库模型 (Database Model)
- 新增 `ScheduledTask` 表，包含14个字段
- 支持任务名称、操作类型、启用状态、计划类型、计划值
- 记录执行历史：上次运行、下次运行、运行次数、状态、错误信息
- 与服务器表建立外键关联，支持 CASCADE 删除

### 2. 计划类型 (Schedule Types)
支持三种主要计划类型：

#### 每日 (Daily)
- 格式：`HH:MM`（例如：`14:30`）
- 功能：每天在指定时间运行
- 验证：小时 0-23，分钟 0-59

#### 每周 (Weekly)
- 格式：`DAY:HH:MM`（例如：`MON:14:30`, `SUN:08:00`）
- 功能：每周指定日期和时间运行
- 支持：MON, TUE, WED, THU, FRI, SAT, SUN

#### 间隔 (Interval)
- 格式：秒数（例如：`3600` 表示每小时）
- 功能：按固定时间间隔重复运行
- 限制：最小间隔 60 秒

### 3. 支持的操作 (Supported Actions)
只允许安全的服务器操作：
- `restart` - 重启服务器
- `start` - 启动服务器
- `stop` - 停止服务器
- `update` - 更新服务器
- `validate` - 验证文件

### 4. API 端点 (API Endpoints)

#### 创建计划任务
```
POST /api/scheduled-tasks/{server_id}
```

#### 列出所有计划任务
```
GET /api/scheduled-tasks/{server_id}
```

#### 获取单个计划任务
```
GET /api/scheduled-tasks/{server_id}/tasks/{task_id}
```

#### 更新计划任务
```
PUT /api/scheduled-tasks/{server_id}/tasks/{task_id}
```

#### 删除计划任务
```
DELETE /api/scheduled-tasks/{server_id}/tasks/{task_id}
```

#### 切换启用/禁用
```
POST /api/scheduled-tasks/{server_id}/tasks/{task_id}/toggle
```

### 5. 后台服务 (Background Service)
- 每 30 秒检查一次待执行的任务
- 自动计算下次运行时间
- 防止重复执行
- 记录执行历史和错误
- 使用现有的 SSH 管理器执行操作

### 6. 安全特性 (Security Features)
- **操作限制**：只允许 5 种安全操作
- **格式验证**：每种计划类型都有严格的格式检查
- **注入防护**：检查并阻止命令注入字符（`;`, `&`, `|`, `$`, `` ` ``, 等）
- **最小间隔**：间隔计划必须至少 60 秒
- **用户隔离**：只能管理自己创建的服务器的任务
- **预提交验证**：API 在提交到数据库前验证计划配置

### 7. 国际化 (Localization)
完整的英文和中文支持：
- 界面文本翻译
- 帮助信息翻译
- 错误消息翻译
- 所有计划类型的说明

### 8. 文档 (Documentation)
详细文档位于 `docs/SCHEDULED_TASKS.md`，包含：
- 功能概述
- API 使用说明
- 所有计划类型的示例
- 安全注意事项
- 错误处理说明

## 使用示例 (Usage Examples)

### 每天凌晨3点重启服务器
```json
{
  "name": "每日凌晨重启",
  "action": "restart",
  "enabled": true,
  "schedule_type": "daily",
  "schedule_value": "03:00"
}
```

### 每周日凌晨4点更新服务器
```json
{
  "name": "周日更新",
  "action": "update",
  "enabled": true,
  "schedule_type": "weekly",
  "schedule_value": "SUN:04:00"
}
```

### 每小时验证一次文件
```json
{
  "name": "每小时检查",
  "action": "validate",
  "enabled": true,
  "schedule_type": "interval",
  "schedule_value": "3600"
}
```

## 技术实现细节 (Technical Implementation)

### 文件变更
- `modules/models.py` - 新增 ScheduledTask 模型
- `modules/schemas.py` - 新增请求/响应架构和验证逻辑
- `services/scheduled_task_service.py` - 后台执行服务
- `api/routes/scheduled_tasks.py` - RESTful API 端点
- `main.py` - 集成服务启动和关闭
- `static/locales/zh-CN.json` - 中文翻译
- `static/locales/en-US.json` - 英文翻译
- `docs/SCHEDULED_TASKS.md` - 功能文档

### 代码质量
- 遵循现有代码库模式
- SQLAlchemy 最佳实践
- 全面的错误处理
- 详细的日志记录
- 清晰的职责分离
- 所有代码审查问题已解决

### 测试验证
✅ 所有模块导入正确
✅ 数据库表创建验证
✅ 架构验证全面（接受有效输入，拒绝无效输入）
✅ 所有类型的计划计算正常工作
✅ 边缘情况的错误处理正常
✅ 两种语言的本地化完整
✅ 服务集成验证通过

## 部署说明 (Deployment Notes)

### 数据库迁移
应用启动时会自动创建 `scheduled_tasks` 表。如果服务器已在运行，表将在下次启动时自动创建。

### 服务启动
计划任务服务会：
1. 在应用启动时自动启动
2. 为所有启用的任务计算下次运行时间
3. 每 30 秒检查一次待执行的任务
4. 在应用关闭时优雅停止

### 性能考虑
- 后台服务使用异步操作，不会阻塞主应用
- 防止同一任务的重复执行
- 使用现有的 SSH 连接池提高效率

## 状态总结 (Status Summary)
✅ **功能完整，生产就绪**

所有要求的功能都已实现并经过全面测试。该功能可以立即投入生产使用。
