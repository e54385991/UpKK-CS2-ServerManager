#!/usr/bin/env bash
set -Eeuo pipefail

# Download this script and run it on a fresh Ubuntu/Debian host. It installs
# Docker/Compose when needed, creates a private .env with random secrets, and
# starts the published images without requiring any other repository files.

readonly IMAGE_REPOSITORY="e54385991/upkk-cs2-server-manager"
readonly FRONTEND_REPOSITORY="e54385991/upkk-cs2-server-manager-web"
readonly DEFAULT_COMPOSE_VERSION="main"
readonly DEFAULT_IMAGE_VERSION="latest"
readonly DEFAULT_USER="admin"
readonly DEFAULT_PASSWORD="admin123"
INSTALL_DIR="${CS2_MANAGER_DIR:-${HOME}/cs2-manager}"
# Keep downloading the installer and Compose file from the stable main branch,
# while following the release images published at the latest tag by default.
# CS2_MANAGER_VERSION remains a compatibility override for both values; the
# more specific variables can be used when they need to differ.
COMPOSE_VERSION="${CS2_MANAGER_COMPOSE_VERSION:-${CS2_MANAGER_VERSION:-${DEFAULT_COMPOSE_VERSION}}}"
IMAGE_VERSION="${CS2_MANAGER_IMAGE_VERSION:-${CS2_MANAGER_VERSION:-${DEFAULT_IMAGE_VERSION}}}"
RAW_BASE_URL="${CS2_MANAGER_RAW_BASE_URL:-https://raw.githubusercontent.com/e54385991/UpKK-CS2-ServerManager/${COMPOSE_VERSION}}"

log() { printf '[cs2-manager] %s\n' "$*"; }
fail() { printf '[cs2-manager] ERROR: %s\n' "$*" >&2; exit 1; }

as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        fail "Root or sudo access is required to install Docker"
    fi
}

random_hex() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
    fi
}

detect_host_ip() {
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [ -n "${ip:-}" ]; then
        printf '%s\n' "$ip"
        return
    fi
    ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
    if [ -n "${ip:-}" ]; then
        printf '%s\n' "$ip"
        return
    fi
    printf 'localhost\n'
}

read_env_value() {
    local key="$1"
    local env_file="${2:-.env}"
    [ -f "$env_file" ] || return 0
    awk -F= -v key="$key" '$1 == key { print $2 }' "$env_file" | tail -1 | tr -d '[:space:]'
}

ensure_env_line() {
    local env_file="$1"
    local key="$2"
    local value="$3"
    if grep -q "^${key}=" "$env_file"; then
        return
    fi
    printf '%s=%s\n' "$key" "$value" >>"$env_file"
}

sync_managed_image() {
    local env_file="$1"
    local key="$2"
    local repository="$3"
    local desired="$4"
    local current
    current="$(read_env_value "$key" "$env_file")"
    case "$current" in
        ""|"docker.io/${repository}:main"|"docker.io/${repository}:latest")
            replace_or_add_env "$env_file" "$key" "$desired"
            ;;
        *)
            log "Keeping custom ${key}=${current}"
            ;;
    esac
}

print_ready() {
    local url="$1"
    printf '\n'
    log "Deployment complete"
    log "URL       ${url}"
    log "Username  ${DEFAULT_USER}"
    log "Password  ${DEFAULT_PASSWORD}"
    printf '\n'
}

is_loopback_url() {
    case "${1:-}" in
        http://localhost|http://localhost:*|http://127.0.0.1|http://127.0.0.1:*|https://localhost|https://localhost:*|https://127.0.0.1|https://127.0.0.1:*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

replace_or_add_env() {
    local env_file="$1"
    local key="$2"
    local value="$3"
    if grep -q "^${key}=" "$env_file"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
    else
        printf '%s=%s\n' "$key" "$value" >>"$env_file"
    fi
}

sync_public_urls() {
    local env_file="$INSTALL_DIR/.env"
    local port ip public_url current key
    [ -f "$env_file" ] || return
    port="$(read_env_value HTTP_PORT "$env_file")"
    port="${port:-3000}"
    ip="$(detect_host_ip)"
    public_url="http://${ip}:${port}"
    for key in CONSOLE_PUBLIC_URL BACKEND_URL; do
        current="$(read_env_value "$key" "$env_file")"
        if [ -z "${current:-}" ] || is_loopback_url "$current"; then
            replace_or_add_env "$env_file" "$key" "$public_url"
        fi
    done
}

install_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        log "Installing Docker Engine"
        command -v curl >/dev/null 2>&1 || as_root apt-get update
        command -v curl >/dev/null 2>&1 || as_root apt-get install -y ca-certificates curl
        curl -fsSL https://get.docker.com | as_root sh
    fi
    if ! docker compose version >/dev/null 2>&1 && ! as_root docker compose version >/dev/null 2>&1; then
        log "Installing the Docker Compose plugin"
        as_root apt-get update
        as_root apt-get install -y docker-compose-plugin
    fi
    as_root systemctl enable --now docker 2>/dev/null || true
}

docker_cmd() {
    if docker info >/dev/null 2>&1; then
        docker "$@"
    else
        as_root docker "$@"
    fi
}

prepare_env() {
    local env_file="$INSTALL_DIR/.env"
    local api_image="docker.io/${IMAGE_REPOSITORY}:${IMAGE_VERSION}"
    local web_image="docker.io/${FRONTEND_REPOSITORY}:${IMAGE_VERSION}"

    if [ -f "$env_file" ]; then
        log "Keeping the existing .env file (existing secrets will not be overwritten)"
        ensure_env_line "$env_file" HTTP_PORT 3000
        sync_managed_image "$env_file" CS2_MANAGER_IMAGE "$IMAGE_REPOSITORY" "$api_image"
        sync_managed_image "$env_file" CS2_FRONTEND_IMAGE "$FRONTEND_REPOSITORY" "$web_image"
        ensure_env_line "$env_file" DEBUG False
        ensure_env_line "$env_file" RUN_MODE production
        chmod 600 "$env_file"
        return
    fi

    local postgres_password redis_password
    postgres_password="$(random_hex)"
    redis_password="$(random_hex)"

    umask 077
    cat >"$env_file" <<EOF
POSTGRES_USER=cs2_manager
POSTGRES_DATABASE=cs2_manager
POSTGRES_PASSWORD=$postgres_password
REDIS_PASSWORD=$redis_password
REDIS_DB=0
HTTP_PORT=3000
CS2_MANAGER_IMAGE=$api_image
CS2_FRONTEND_IMAGE=$web_image
DEBUG=False
RUN_MODE=production
LOG_LEVEL=INFO
EOF
    chmod 600 "$env_file"
    log "Generated a random .env file (mode 600)"
}

wait_for_console() {
    local public_port="$1"
    local login_url="http://127.0.0.1:${public_port}/login"
    local health_url="http://127.0.0.1:${public_port}/health"
    log "Waiting for the console (Next :${public_port} -> FastAPI)"
    for _ in $(seq 1 90); do
        if curl -fsS "$login_url" >/dev/null 2>&1 && curl -fsS "$health_url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

main() {
    case "$(uname -s)" in
        Linux) ;;
        *) fail "This installer supports Ubuntu/Debian Linux. Install Docker Desktop manually on other systems" ;;
    esac
    mkdir -p "$INSTALL_DIR"
    install_docker
    command -v curl >/dev/null 2>&1 || fail "curl is required to download the Compose configuration"
    curl -fsSL "$RAW_BASE_URL/docker-compose.yml" -o "$INSTALL_DIR/docker-compose.yml"
    prepare_env
    sync_public_urls
    cd "$INSTALL_DIR"
    docker_cmd compose pull
    docker_cmd compose up -d

    public_port="${HTTP_PORT:-3000}"
    parsed_port="$(read_env_value HTTP_PORT .env)"
    if [ -n "${parsed_port:-}" ]; then
        public_port="$parsed_port"
    fi
    if wait_for_console "$public_port"; then
        docker_cmd compose ps
        print_ready "http://$(detect_host_ip):${public_port}"
        exit 0
    fi
    docker_cmd compose ps
    docker_cmd compose logs --tail=80 frontend app || true
    fail "Console health check timed out. Run: cd '$INSTALL_DIR' && docker compose logs -f"
}

main "$@"
