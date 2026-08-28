# CS2 Server Manager

[English](README.md) | [简体中文](README.zh-CN.md)

[![FastAPI](https://img.shields.io/badge/FastAPI-0.141+-009688.svg?style=flat&logo=FastAPI)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.14+-blue.svg?style=flat&logo=python)](https://www.python.org)
[![Docker](https://img.shields.io/badge/Docker-One--click_Deploy-2496ED.svg?style=flat&logo=docker)](docs/DOCKER_QUICKSTART.md)

> 🚀 **Docker one-click deployment is recommended.** There is no need to
> install Python, PostgreSQL, or Redis manually—a single command starts the
> complete management panel.
>
> [1Panel quick deployment](docs/1PANEL_QUICKSTART.md) is also supported. See
> the [full documentation](docs/README.md) for more information.

## Overview

CS2 Server Manager is a modern, web-based **multi-server management panel for
Counter-Strike 2**. It connects to one or more game servers over SSH, allowing
you to deploy, start, stop, update, monitor, and manage plugins entirely from
your browser.

The management panel and game servers can run on the same host or on separate
hosts. We recommend running the panel on a dedicated host and managing game
servers over SSH. This setup is easier to maintain and prevents the management
services and game processes from interfering with each other.

### Key features

- Deploy, start, stop, restart, and update CS2 servers with one click;
- Centrally manage multiple servers and monitor their status, logs, and
  deployment progress in real time;
- Use a web console, file manager, and common server configuration tools;
- Install and update Metamod:Source, CounterStrikeSharp, and related plugins
  with one click;
- Configure automatic restart protection, automatic updates, and scheduled
  tasks;
- Authenticate with a password or SSH key, with support for user permissions
  and API keys;
- Back up data to S3-compatible storage with configurable retention policies;
- Relay downloads through the panel or a GitHub URL proxy in restricted
  network environments;
- Run on FastAPI, PostgreSQL, and Redis, with Docker automatically preparing
  all dependencies and applying database migrations.

### How it works

1. Deploy the management panel with the command below;
2. Sign in and change the default password immediately;
3. Add the SSH connection details for your game servers;
4. Click **Deploy** in the web interface, then manage your servers from the
   panel.

## Install prerequisites

Update the package index and make sure `curl` is installed:

```bash
sudo apt update && sudo apt install -y curl
```

## Docker quick deployment

Use a fresh **Ubuntu 24.04+** or **Debian 13+** host for the management panel.
Run the following command as a user with `sudo` privileges:

```bash
curl -fsSL https://raw.githubusercontent.com/e54385991/UpKK-CS2-ServerManager/main/docker-quickstart.sh | bash
```

The script automatically:

- Installs Docker Engine and the Docker Compose plugin;
- Generates a random database password and secure application keys;
- Downloads the Compose configuration and starts the management panel,
  PostgreSQL, and Redis;
- Applies database migrations and waits for the services to become healthy.

When deployment is complete, open:

```text
http://YOUR_SERVER_IP:8000
```

Default credentials for the first sign-in:

```text
Username: admin
Password: admin123
```

> ⚠️ **Change the default password immediately after your first sign-in.** If
> the page is unreachable, make sure TCP port `8000` is allowed by both your
> cloud security group and system firewall. For a public production service,
> configure a domain name and HTTPS.

The management panel is now ready; you do not need to clone the repository or
configure the database manually. For upgrades, backups, logs, port changes,
and troubleshooting, see the [Docker quick deployment guide](docs/DOCKER_QUICKSTART.md).

## Documentation

This README covers only the shortest deployment path. Use the relevant guide
below when you need additional features.

### Deployment and getting started

| Task | Guide |
| --- | --- |
| Upgrade or back up Docker deployments and troubleshoot issues | [Docker quick deployment](docs/DOCKER_QUICKSTART.md) |
| Reuse PostgreSQL and Redis with 1Panel | [1Panel quick deployment](docs/1PANEL_QUICKSTART.md) |
| Prepare a target server to run CS2 | [Game server deployment requirements](docs/DEPLOYMENT.md) |
| Add and deploy a game server from scratch | [Beginner's illustrated guide](docs/ALIYUN_ECS_DEPLOY.md) |
| Browse all documentation | [Documentation center](docs/README.md) |

### Common features

| Task | Guide |
| --- | --- |
| Use the web console and run commands | [Console usage guide](docs/CONSOLE_USAGE_GUIDE.md) |
| Install and manage plugins | [Plugin installation guide](docs/PLUGIN_INSTALLATION_GUIDE.md) |
| Configure automatic restarts | [Automatic restart guide](docs/AUTO_RESTART_GUIDE.md) |
| Configure automatic updates | [Automatic update guide](docs/AUTO_UPDATE_GUIDE.md) |
| Configure scheduled tasks | [Scheduled tasks guide](docs/SCHEDULED_TASKS.md) |
| Configure a GitHub download proxy | [Panel proxy guide](docs/GITHUB_PROXY.md) |
| Use API keys | [API key usage guide](docs/API_KEY_USAGE.md) |

> Most linked guides are currently available in Simplified Chinese.

### Video tutorials

- [Management panel walkthrough and feature demo](https://youtu.be/PPzykUZmNy0)

## Getting help

Check the [documentation center](docs/README.md) first. If the issue remains,
open a [GitHub issue](https://github.com/e54385991/UpKK-CS2-ServerManager/issues)
and include your operating system version, deployment method, and relevant
logs.
