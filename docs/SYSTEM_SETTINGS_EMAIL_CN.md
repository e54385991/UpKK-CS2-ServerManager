# 系统设置和邮件管理功能

## 概述

本文档描述了添加到 CS2 服务器管理器的新系统设置和邮件管理功能。

## 功能特性

### 1. 系统设置页面（仅限管理员）

管理员专属的综合设置页面，用于配置全局系统行为。

#### 功能特性：
- **下载代理设置**：配置 GitHub 下载的默认代理模式
  - 直接连接：直接从 GitHub 下载（默认）
  - 使用面板服务器代理：通过面板服务器下载（推荐中国大陆用户）
  - 使用 GitHub 代理 URL：使用自定义 GitHub 代理 URL
- **邮件设置**：配置密码重置和通知的邮件系统
  - SMTP 配置支持
  - Gmail API 支持（即将推出）
  - 启用/禁用邮件系统
  - 配置发件人信息

#### 访问方式：
- 只有 `is_admin=true` 的用户才能访问此页面
- URL: `/system-settings`
- API 端点: `/api/system/settings`

### 2. 邮件管理系统

用于发送密码重置邮件和未来通知的邮件系统。

#### 功能特性：
- SMTP 邮件提供商支持
- 可配置的发件人邮箱和名称
- HTML 和纯文本邮件模板
- 安全的密码重置流程

#### SMTP 配置：
在系统设置中配置：
- SMTP 主机（例如：smtp.gmail.com）
- SMTP 端口（默认：587）
- SMTP 用户名
- SMTP 密码
- 启用/禁用 TLS

### 3. 密码重置流程

用户忘记密码时可以通过邮件重置。

#### 工作流程：
1. 用户在登录页面点击"忘记密码"
2. 用户输入邮箱地址并完成验证码
3. 系统发送密码重置邮件（如果邮箱存在）
4. 用户点击邮件中的链接（有效期 1 小时）
5. 用户输入新密码
6. 用户可以使用新密码登录

#### 功能特性：
- 基于安全令牌的重置（令牌 1 小时后过期）
- 邮箱枚举保护（总是返回成功）
- 验证码防止滥用
- 令牌只能使用一次
- 精美的 HTML 邮件模板

### 4. 国际化（i18n）

所有新功能完全支持多语言：
- 英语（en-US）
- 简体中文（zh-CN）

## API 端点

### 系统设置

#### GET /api/system/settings
获取当前系统设置（仅限管理员）

**认证要求**：需要（管理员）

**响应示例**：
```json
{
  "id": 1,
  "default_proxy_mode": "direct",
  "github_proxy_url": null,
  "email_enabled": true,
  "email_provider": "smtp",
  "email_from_address": "noreply@example.com",
  "email_from_name": "CS2 服务器管理器",
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "smtp_username": "user@example.com",
  "smtp_use_tls": true,
  "created_at": "2024-01-01T00:00:00",
  "updated_at": "2024-01-01T00:00:00"
}
```

#### PUT /api/system/settings
更新系统设置（仅限管理员）

**认证要求**：需要（管理员）

**请求体示例**：
```json
{
  "default_proxy_mode": "panel",
  "email_enabled": true,
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "smtp_username": "user@example.com",
  "smtp_password": "password",
  "smtp_use_tls": true
}
```

### 密码重置

#### POST /api/auth/forgot-password
请求密码重置邮件

**认证要求**：不需要

**请求体示例**：
```json
{
  "email": "user@example.com",
  "captcha_token": "abc123",
  "captcha_code": "1234"
}
```

**响应示例**：
```json
{
  "success": true,
  "message": "如果该邮箱对应的账户存在，密码重置链接已发送。"
}
```

#### POST /api/auth/reset-password-with-token
使用令牌重置密码

**认证要求**：不需要

**请求体示例**：
```json
{
  "token": "reset_token_here",
  "new_password": "newpassword123"
}
```

**响应示例**：
```json
{
  "success": true,
  "message": "密码重置成功。您现在可以使用新密码登录。"
}
```

## 数据库模型

### SystemSettings（系统设置）
- `id`: 主键
- `default_proxy_mode`: 默认代理模式（direct、panel、github_url）
- `github_proxy_url`: GitHub 代理 URL
- `email_enabled`: 启用/禁用邮件系统
- `email_provider`: 邮件提供商（smtp、gmail）
- `email_from_address`: 发件人邮箱地址
- `email_from_name`: 发件人名称
- `gmail_credentials_json`: Gmail API 凭据（未来功能）
- `gmail_token_json`: Gmail API 令牌（未来功能）
- `smtp_host`: SMTP 服务器主机
- `smtp_port`: SMTP 服务器端口
- `smtp_username`: SMTP 用户名
- `smtp_password`: SMTP 密码（加密）
- `smtp_use_tls`: 使用 TLS
- `created_at`: 创建时间戳
- `updated_at`: 更新时间戳

### PasswordResetToken（密码重置令牌）
- `id`: 主键
- `user_id`: 用户表外键
- `token`: 唯一重置令牌
- `expires_at`: 令牌过期时间
- `used`: 令牌是否已使用
- `created_at`: 创建时间戳

## 安全考虑

1. **管理员访问**：系统设置受管理员专属认证保护
2. **邮箱枚举**：忘记密码总是返回成功，防止邮箱枚举
3. **令牌安全**：重置令牌具有以下特性：
   - 密码学安全（64 字符）
   - 只能使用一次
   - 时间限制（1 小时）
4. **验证码保护**：所有密码重置请求都需要验证码
5. **SMTP 凭据**：安全存储在数据库中

## 配置

### 启用邮件系统

1. 以管理员身份登录
2. 导航到系统设置
3. 启用"邮件系统"
4. 配置 SMTP 设置：
   - SMTP 主机
   - SMTP 端口（TLS 使用 587，SSL 使用 465）
   - SMTP 用户名
   - SMTP 密码
   - 启用 TLS（推荐）
5. 设置发件人邮箱和名称
6. 保存设置

### 测试邮件配置

配置邮件后，通过以下方式测试：
1. 退出登录
2. 点击"忘记密码"
3. 输入有效的邮箱地址
4. 完成验证码
5. 检查邮箱收件箱中的密码重置链接

## 故障排除

### 邮件发送失败

1. 检查系统设置中的邮件配置
2. 验证 SMTP 凭据正确
3. 检查 SMTP 主机和端口
4. 确保端口 587 启用了 TLS
5. 查看应用程序日志中的错误

### Gmail SMTP

使用 Gmail SMTP：
- 主机：smtp.gmail.com
- 端口：587
- TLS：启用
- **重要**：使用应用专用密码，而不是常规密码
- 生成应用专用密码：https://myaccount.google.com/apppasswords

### 密码重置链接不工作

1. 检查邮件系统是否已启用
2. 验证令牌未过期（1 小时限制）
3. 确保令牌未被使用
4. 检查数据库中的令牌记录

## 未来增强功能

- Gmail API 支持（OAuth2）
- 其他通知的邮件模板
- 新注册的邮箱验证
- 通过邮件的双因素认证
- 可自定义的邮件模板
