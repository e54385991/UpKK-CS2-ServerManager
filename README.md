# CS2 Server Manager | CS2 服务器管理器

[![FastAPI](https://img.shields.io/badge/FastAPI-0.141+-009688.svg?style=flat&logo=FastAPI)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.14+-blue.svg?style=flat&logo=python)](https://www.python.org)
[![Docker](https://img.shields.io/badge/Docker-一键部署-2496ED.svg?style=flat&logo=docker)](docs/DOCKER_QUICKSTART.md)

> 🚀 **推荐使用 Docker 一键部署。** 无需手动安装 Python、PostgreSQL 或 Redis，
> 一条命令即可启动完整管理面板。
>
> 同时支持 [1Panel 快速部署](docs/1PANEL_QUICKSTART.md)，更多内容见
> [完整文档](docs/README.md)。

## 项目说明

CS2 Server Manager 是一个现代化的 **Counter-Strike 2 多服务器 Web 管理面板**。
管理面板通过 SSH 连接一台或多台游戏服务器，让部署、启动、停止、更新、监控和插件管理
都可以在浏览器中完成。

管理面板与游戏服务器可以部署在同一台主机，也可以分开部署。推荐将管理面板放在独立主机
上，通过 SSH 管理游戏服务器；这样更容易维护，也不会让管理服务与游戏进程互相影响。

### 主要功能

- 一键部署、启动、停止、重启和更新 CS2 服务器；
- 集中管理多台服务器，实时查看状态、日志和部署进度；
- 提供 Web 控制台、文件管理和常用服务器配置；
- 一键安装和更新 Metamod:Source、CounterStrikeSharp 及相关插件；
- 支持自动重启保护、自动更新和计划任务；
- 支持密码或 SSH 密钥认证，并提供用户权限和 API Key；
- 支持 S3 兼容存储备份以及备份保留策略；
- 支持面板中转和 GitHub URL 代理，方便受限网络环境下载插件；
- 管理面板基于 FastAPI、PostgreSQL 和 Redis，Docker 部署会自动准备全部依赖并执行数据库迁移。

### 使用流程

1. 使用下方命令部署管理面板；
2. 登录面板并立即修改默认密码；
3. 添加游戏服务器的 SSH 连接信息；
4. 在网页中点击部署，随后即可完成日常管理。

## 先更新下软件包 和 确保 CURL存在
```bash
sudo apt update && sudo apt install -y curl
```

## Docker 快速部署

适用于全新的 **Ubuntu 24.04+** 或 **Debian 13+** 管理端主机。使用具有 `sudo`
权限的用户执行：

```bash
curl -fsSL https://raw.githubusercontent.com/e54385991/UpKK-CS2-ServerManager/main/docker-quickstart.sh | bash
```

脚本会自动：

- 安装 Docker Engine 和 Docker Compose 插件；
- 生成随机数据库密码和安全密钥；
- 下载 Compose 配置并启动管理面板、PostgreSQL 和 Redis；
- 自动完成数据库迁移并等待服务健康检查通过。

部署完成后访问：

```text
http://你的服务器IP:8000
```

首次登录凭据：

```text
用户名：admin
密码：admin123
```

> ⚠️ **首次登录后请立即修改默认密码。** 如果无法打开页面，请确认云服务器安全组和
> 系统防火墙已放行 TCP `8000` 端口。正式对公网提供服务时建议配置域名和 HTTPS。

至此管理面板已经部署完成，无需克隆源码或手动配置数据库。升级、备份、日志查看、
端口修改和故障排查请查看 [Docker 快速部署文档](docs/DOCKER_QUICKSTART.md)。

## 文档导航

README 只保留最短部署路径。需要哪项功能时，直接打开对应文档即可。

### 部署与入门

| 需求 | 文档 |
| --- | --- |
| Docker 升级、备份与故障排查 | [Docker 快速部署](docs/DOCKER_QUICKSTART.md) |
| 使用 1Panel 复用 PostgreSQL 和 Redis | [1Panel 快速部署](docs/1PANEL_QUICKSTART.md) |
| 准备运行 CS2 的目标服务器 | [游戏服务器部署要求](docs/DEPLOYMENT.md) |
| 从零开始添加并部署游戏服务器 | [新手图文教程](docs/ALIYUN_ECS_DEPLOY.md) |
| 查看完整文档目录 | [项目文档中心](docs/README.md) |

### 常用功能

| 需求 | 文档 |
| --- | --- |
| Web 控制台与命令操作 | [控制台使用指南](docs/CONSOLE_USAGE_GUIDE.md) |
| 安装和管理插件 | [插件安装指南](docs/PLUGIN_INSTALLATION_GUIDE.md) |
| 配置自动重启 | [自动重启指南](docs/AUTO_RESTART_GUIDE.md) |
| 配置自动更新 | [自动更新指南](docs/AUTO_UPDATE_GUIDE.md) |
| 配置计划任务 | [计划任务指南](docs/SCHEDULED_TASKS.md) |
| 配置 GitHub 下载代理 | [面板代理指南](docs/GITHUB_PROXY.md) |
| 使用 API Key | [API Key 使用指南](docs/API_KEY_USAGE.md) |

### 视频教程

- [管理面板快速部署（约 2 分钟）](https://youtu.be/8GksFZHmO0c)
- [管理面板操作与功能演示](https://youtu.be/PPzykUZmNy0)

## 获取帮助

遇到问题时请先查看 [项目文档中心](docs/README.md)。如果仍无法解决，请在
[GitHub Issues](https://github.com/e54385991/UpKK-CS2-ServerManager/issues) 中提交问题，
并附上系统版本、部署方式和相关日志。
