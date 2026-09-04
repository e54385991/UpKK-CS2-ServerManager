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

trap 'status=$?; echo "Setup failed (line $LINENO, exit code $status)" >&2; exit $status' ERR

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
            echo "APT operation failed; retrying automatically (attempt $attempt/3)..." >&2
            sleep $((attempt * 2))
        fi
    done
    return 1
}}

echo "Starting CS2 server environment setup..."

# SteamCMD/CS2 does not support ARM servers
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
case "$ARCH" in
    amd64|x86_64) ;;
    *)
        echo "Unsupported server architecture: $ARCH; SteamCMD/CS2 requires amd64 (x86_64)." >&2
        exit 1
        ;;
esac

# Update package lists (wait for locks and retry network errors automatically)
apt_retry update

# Install and verify required SteamCMD dependencies
apt_retry install -y \\
    {required}

for package in {required}; do
    if [ "$(dpkg-query -W -f='\\${{Status}}' "$package" 2>/dev/null || true)" != "install ok installed" ]; then
        echo "Required dependency verification failed: $package" >&2
        exit 1
    fi
done

# Optional dependency failures do not invalidate the verified SteamCMD runtime
if apt-cache show --no-all-versions {seven_zip_primary} >/dev/null 2>&1; then
    apt_retry install -y {optional} {seven_zip_primary} || echo "Warning: optional dependencies failed to install" >&2
elif apt-cache show --no-all-versions {seven_zip_fallback} >/dev/null 2>&1; then
    apt_retry install -y {optional} {seven_zip_fallback} || echo "Warning: optional dependencies failed to install" >&2
else
    apt_retry install -y {optional} || echo "Warning: {SETUP_OPTIONAL_PACKAGES[0]} installation failed" >&2
fi

# Create the user
if id -u "$CS2_USER" >/dev/null 2>&1; then
    echo "User already exists: $CS2_USER"
else
    sudo useradd -m -s /bin/bash "$CS2_USER"
fi

# Use a non-expanding heredoc to safely write a password containing special characters
sudo chpasswd <<'EOF_PASSWORD'
{user}:{password}
EOF_PASSWORD

# Create the game directory
sudo mkdir -p "/home/$CS2_USER/cs2"
sudo chown -R "$CS2_USER:$CS2_USER" "/home/$CS2_USER"

echo "Setup complete!"
echo "Username: $CS2_USER"
echo "Password: {password}"
"""
