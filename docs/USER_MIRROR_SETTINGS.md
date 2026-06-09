# User Mirror Settings Feature

## 概述 (Overview)

此功能允许用户在个人中心自定义镜像URL，以适配中国网络环境，提高下载速度。

This feature allows users to customize mirror URLs in their profile to adapt to China's network conditions and improve download speeds.

## 功能特性 (Features)

### 1. 预设镜像选项 (Preset Mirror Options)

系统预置了多个常用镜像源供用户选择：

The system provides multiple preset mirror sources for users to choose from:

#### SteamCMD 镜像
- **Official (Default)**: `https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz`
- **阿里云镜像 (Aliyun)**: `https://mirrors.aliyun.com/steamcmd/steamcmd_linux.tar.gz`
- **腾讯云镜像 (Tencent)**: `https://mirrors.cloud.tencent.com/steamcmd/steamcmd_linux.tar.gz`
- **自定义 (Custom)**: 用户输入自定义URL

#### GitHub API 镜像
- **Official (Default)**: `https://api.github.com`
- **GitHub加速 (FastGit)**: `https://hub.fastgit.xyz`
- **GitHub代理 (ghproxy)**: `https://mirror.ghproxy.com/https://api.github.com`
- **自定义 (Custom)**: 用户输入自定义URL

#### GitHub Objects 镜像
- **Official (Default)**: `https://github.com`
- **GitHub加速 (FastGit)**: `https://hub.fastgit.xyz`
- **GitHub代理 (ghproxy)**: `https://mirror.ghproxy.com/https://github.com`
- **自定义 (Custom)**: 用户输入自定义URL

### 2. 配置方式 (Configuration)

#### 在 config.py 中添加新的镜像预设

管理员可以在 `modules/config.py` 中的以下列表添加新的镜像源：

Administrators can add new mirror sources in the following lists in `modules/config.py`:

```python
STEAMCMD_MIRRORS: list = [
    {"name": "镜像名称", "url": "镜像URL"},
    # ...
]

GITHUB_API_MIRRORS: list = [
    {"name": "镜像名称", "url": "镜像URL"},
    # ...
]

GITHUB_OBJECTS_MIRRORS: list = [
    {"name": "镜像名称", "url": "镜像URL"},
    # ...
]
```

**注意**: 列表中的第一个选项将作为默认设置。

**Note**: The first option in each list will be used as the default.

#### 用户设置 (User Settings)

用户可以在个人中心 (Profile) 页面的"镜像设置"部分：
1. 从预设选项中选择镜像源
2. 选择"自定义"并输入自己的镜像URL
3. 点击"保存镜像设置"保存配置
4. 点击"重置为默认"恢复默认设置

Users can configure mirror settings in the "Mirror Settings" section of their Profile page:
1. Select a mirror from preset options
2. Choose "Custom" and enter their own mirror URL
3. Click "Save Mirror Settings" to save configuration
4. Click "Reset to Default" to restore default settings

## 技术实现 (Technical Implementation)

### 数据库表 (Database Table)

新增 `user_settings` 表存储用户的镜像设置：

```sql
CREATE TABLE `user_settings` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `steamcmd_mirror_url` varchar(500) DEFAULT NULL,
  `github_api_mirror_url` varchar(500) DEFAULT NULL,
  `github_objects_mirror_url` varchar(500) DEFAULT NULL,
  `created_at` datetime DEFAULT (now()),
  `updated_at` datetime DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_user_settings_user_id` (`user_id`)
);
```

### API 端点 (API Endpoints)

- `GET /api/user-settings/mirrors` - 获取所有镜像预设选项
- `GET /api/user-settings` - 获取当前用户的镜像设置
- `PUT /api/user-settings` - 更新用户的镜像设置
- `DELETE /api/user-settings` - 重置用户设置为默认值

### 使用流程 (Usage Flow)

1. 用户在部署服务器或安装插件时，系统自动获取用户的镜像设置
2. 如果用户有自定义设置，使用用户设置的镜像URL
3. 如果用户没有自定义设置，使用 config.py 中的默认镜像URL（第一个选项）
4. 所有下载操作（SteamCMD、GitHub插件等）都会使用配置的镜像URL

## 数据库迁移 (Database Migration)

执行以下SQL脚本创建 `user_settings` 表：

Run the following SQL script to create the `user_settings` table:

```bash
mysql -u cs2_manager -p cs2_manager < db/migrations/001_add_user_settings_table.sql
```

或者手动执行 `db/migrations/001_add_user_settings_table.sql` 中的SQL语句。

Or manually execute the SQL statements in `db/migrations/001_add_user_settings_table.sql`.

## 影响的功能 (Affected Features)

使用自定义镜像设置的功能包括：

Features that use custom mirror settings include:

1. **CS2服务器部署** - SteamCMD下载使用配置的SteamCMD镜像
   - **CS2 Server Deployment** - SteamCMD download uses configured SteamCMD mirror
2. **Metamod安装** - （当前从sourcemm.net下载，未受影响）
   - **Metamod Installation** - (Currently downloads from sourcemm.net, not affected)
3. **CounterStrikeSharp安装** - GitHub API调用使用配置的GitHub API镜像，文件下载使用GitHub Objects镜像
   - **CounterStrikeSharp Installation** - GitHub API calls use configured GitHub API mirror, file downloads use GitHub Objects mirror
4. **CS2Fixes安装** - GitHub API调用使用配置的GitHub API镜像，文件下载使用GitHub Objects镜像
   - **CS2Fixes Installation** - GitHub API calls use configured GitHub API mirror, file downloads use GitHub Objects mirror

### GitHub Objects 镜像说明 (GitHub Objects Mirror Explanation)

当从GitHub下载发布文件时，GitHub会将请求重定向到 `objects.githubusercontent.com`。通过配置GitHub Objects镜像，可以替换下载URL中的 `https://github.com` 前缀，从而使用镜像加速下载。

When downloading release files from GitHub, GitHub redirects requests to `objects.githubusercontent.com`. By configuring the GitHub Objects mirror, the `https://github.com` prefix in download URLs is replaced, enabling accelerated downloads through mirrors.

例如 (Example):
- 原始URL (Original): `https://github.com/owner/repo/releases/download/v1.0.0/file.zip`
- 使用镜像后 (With mirror): `https://mirror.example.com/owner/repo/releases/download/v1.0.0/file.zip`

## 向后兼容性 (Backward Compatibility)

- 现有用户如果没有配置镜像设置，将自动使用默认镜像（官方源）
- 无需任何操作，系统会自动处理
- 对于已有的部署和安装操作，不会有任何影响

Existing users without mirror settings will automatically use the default mirrors (official sources).
No action is required; the system handles this automatically.
Existing deployments and installations will not be affected.

## 安全性 (Security)

- 所有镜像URL必须以 `http://` 或 `https://` 开头
- 用户输入的自定义URL会经过验证
- 数据库中的用户设置与用户账号绑定，互不影响

All mirror URLs must start with `http://` or `https://`.
User-entered custom URLs are validated.
User settings in the database are tied to user accounts and do not affect each other.
