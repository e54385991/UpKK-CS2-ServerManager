"""Manual host-setup script shared by the Next console and the setup wizard."""

from __future__ import annotations

import re

from services.system_dependencies import (
    SETUP_OPTIONAL_PACKAGES,
    SEVEN_ZIP_PACKAGE_ALTERNATIVES,
    STEAMCMD_REQUIRED_PACKAGES,
    STEAMCMD_RUNTIME_PACKAGES,
)

_CS2_USERNAME = re.compile(r"^[a-z_][a-z0-9_-]*$")


def validate_cs2_username(value: str) -> str:
    username = value.strip()
    if not _CS2_USERNAME.fullmatch(username):
        raise ValueError("CS2 username must be a Linux login: start with a letter or underscore")
    return username


def build_manual_setup_script(*, cs2_username: str, password: str) -> str:
    """Return the bash script Jinja previously inlined in ``server_setup_wizard.html``."""
    user = validate_cs2_username(cs2_username)
    conveniences = " ".join(
        package
        for package in STEAMCMD_REQUIRED_PACKAGES
        if package not in STEAMCMD_RUNTIME_PACKAGES
    )
    runtime = " ".join(STEAMCMD_RUNTIME_PACKAGES)
    required = f"{conveniences} \\\n    {runtime}"
    optional = " ".join(SETUP_OPTIONAL_PACKAGES)
    seven_zip_primary, seven_zip_fallback = SEVEN_ZIP_PACKAGE_ALTERNATIVES
    return f"""#!/bin/bash
set -Eeuo pipefail

trap 'status=$?; echo "设置失败（第 $LINENO 行，退出码 $status）" >&2; exit $status' ERR

CS2_USER='{user}'

apt_retry() {{
    local attempt
    for attempt in 1 2 3; do
        if sudo env DEBIAN_FRONTEND=noninteractive apt-get \\
            -o DPkg::Lock::Timeout=120 \\
            -o Acquire::Retries=3 \\
            -o Dpkg::Use-Pty=0 "$@"; then
            return 0
        fi
        if [ "$attempt" -lt 3 ]; then
            echo "APT 操作失败，将自动重试（$attempt/3）..." >&2
            sleep $((attempt * 2))
        fi
    done
    return 1
}}

echo "开始设置 CS2 服务器环境..."

# SteamCMD/CS2 不支持 ARM 服务器
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
case "$ARCH" in
    amd64|x86_64) ;;
    *)
        echo "不支持的服务器架构: $ARCH；SteamCMD/CS2 需要 amd64 (x86_64)。" >&2
        exit 1
        ;;
esac

# 更新包列表（自动等待锁并重试网络错误）
apt_retry update

# 安装并验证 SteamCMD 必需依赖
apt_retry install -y \\
    {required}

for package in {required}; do
    if [ "$(dpkg-query -W -f='\\${{Status}}' "$package" 2>/dev/null || true)" != "install ok installed" ]; then
        echo "必需依赖验证失败: $package" >&2
        exit 1
    fi
done

# 可选增强依赖失败不会破坏已经验证通过的 SteamCMD 运行时
if apt-cache show --no-all-versions {seven_zip_primary} >/dev/null 2>&1; then
    apt_retry install -y {optional} {seven_zip_primary} || echo "警告：可选增强依赖安装失败" >&2
elif apt-cache show --no-all-versions {seven_zip_fallback} >/dev/null 2>&1; then
    apt_retry install -y {optional} {seven_zip_fallback} || echo "警告：可选增强依赖安装失败" >&2
else
    apt_retry install -y {optional} || echo "警告：{SETUP_OPTIONAL_PACKAGES[0]} 安装失败" >&2
fi

# 创建用户
if id -u "$CS2_USER" >/dev/null 2>&1; then
    echo "用户已存在: $CS2_USER"
else
    sudo useradd -m -s /bin/bash "$CS2_USER"
fi

# 使用禁止变量展开的 heredoc，安全写入包含特殊字符的密码
sudo chpasswd <<'EOF_PASSWORD'
{user}:{password}
EOF_PASSWORD

# 创建目录
sudo mkdir -p "/home/$CS2_USER/cs2"
sudo chown -R "$CS2_USER:$CS2_USER" "/home/$CS2_USER"

echo "设置完成！"
echo "用户名: $CS2_USER"
echo "密码: {password}"
"""
