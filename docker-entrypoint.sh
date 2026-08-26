#!/bin/sh
set -eu

umask 077
DATA_DIR="${APP_DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR"

generate_secret() {
    python -c 'import secrets; print(secrets.token_urlsafe(48))'
}

load_or_create_secret() {
    variable_name="$1"
    file_name="$2"
    eval "current_value=\${$variable_name:-}"

    if [ -n "$current_value" ] \
        && [ "$current_value" != "your-secret-key-change-this-in-production" ] \
        && [ "$current_value" != "your-jwt-secret-key-change-this-in-production" ]; then
        return
    fi

    secret_file="$DATA_DIR/$file_name"
    if [ -s "$secret_file" ]; then
        generated_value=$(cat "$secret_file")
    else
        generated_value=$(generate_secret)
        printf '%s\n' "$generated_value" > "$secret_file"
    fi
    export "$variable_name=$generated_value"
}

load_or_create_secret SECRET_KEY .secret_key
load_or_create_secret JWT_SECRET_KEY .jwt_secret_key

exec "$@"
