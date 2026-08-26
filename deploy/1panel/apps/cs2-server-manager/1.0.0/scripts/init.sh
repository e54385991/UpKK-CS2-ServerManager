#!/bin/sh
set -eu

# The published image runs as UID/GID 10001.  1Panel executes init.sh before
# Compose startup, so the relative persistent directory is writable on the
# first installation as well as after a rebuild.
# Generated from official installation evidence: Dockerfile USER app and
# adduser -u 10001 in https://github.com/e54385991/UpKK-CS2-ServerManager/blob/main/Dockerfile
chown -R 10001:10001 ./data

# 1Panel's `random: true` form helper appends a short six-character suffix.
# Replace that value on first install with a full 256-bit (64 hex character)
# secret. Existing values of at least 32 characters are retained so upgrades do
# not unexpectedly rotate an operator-provided key.
generate_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        dd if=/dev/urandom bs=32 count=1 2>/dev/null | sha256sum | awk '{print $1}'
    fi
}

ensure_secret() {
    key=$1
    [ -f .env ] || return 0
    current=$(sed -n "s/^${key}=//p" .env | head -n 1)
    if [ "${#current}" -ge 32 ]; then
        return 0
    fi
    replacement=$(generate_secret)
    temporary_env=$(mktemp .env.XXXXXX)
    awk -v key="$key" -v replacement="$replacement" '
        BEGIN { updated = 0 }
        index($0, key "=") == 1 { print key "=" replacement; updated = 1; next }
        { print }
        END { if (!updated) print key "=" replacement }
    ' .env > "$temporary_env"
    mv "$temporary_env" .env
}

ensure_secret SECRET_KEY
ensure_secret JWT_SECRET_KEY
