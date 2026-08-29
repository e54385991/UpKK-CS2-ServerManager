#!/usr/bin/env bash
set -Eeuo pipefail

# Download this script and run it on a fresh Ubuntu/Debian host. It installs
# Docker/Compose when needed, creates a private .env with random secrets, and
# starts the published images without requiring any other repository files.

readonly IMAGE_REPOSITORY="e54385991/upkk-cs2-server-manager"
readonly DEFAULT_VERSION="main"
INSTALL_DIR="${CS2_MANAGER_DIR:-${HOME}/cs2-manager}"
VERSION="${CS2_MANAGER_VERSION:-${DEFAULT_VERSION}}"
RAW_BASE_URL="${CS2_MANAGER_RAW_BASE_URL:-https://raw.githubusercontent.com/e54385991/UpKK-CS2-ServerManager/${VERSION}}"

log() { printf '[cs2-manager] %s\n' "$*"; }
fail() { printf '[cs2-manager] ERROR: %s\n' "$*" >&2; exit 1; }

as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        fail "需要 root 或 sudo 权限来安装 Docker"
    fi
}

random_hex() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
    fi
}

install_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        log "安装 Docker Engine"
        command -v curl >/dev/null 2>&1 || as_root apt-get update
        command -v curl >/dev/null 2>&1 || as_root apt-get install -y ca-certificates curl
        curl -fsSL https://get.docker.com | as_root sh
    fi
    if ! docker compose version >/dev/null 2>&1 && ! as_root docker compose version >/dev/null 2>&1; then
        log "安装 Docker Compose 插件"
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
    if [ -f "$env_file" ]; then
        log "保留已有 .env（不会覆盖现有密钥）"
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
DEBUG=False
LOG_LEVEL=INFO
EOF
    chmod 600 "$env_file"
    log "已生成随机 .env（权限 600）"
}

wait_for_console() {
    local public_port="$1"
    local login_url="http://127.0.0.1:${public_port}/login"
    local health_url="http://127.0.0.1:${public_port}/health"
    log "等待控制台就绪（Next :${public_port} → FastAPI）"
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
        *) fail "此安装脚本支持 Ubuntu/Debian Linux；其他系统请手动安装 Docker Desktop" ;;
    esac
    mkdir -p "$INSTALL_DIR"
    install_docker
    command -v curl >/dev/null 2>&1 || fail "缺少 curl，无法下载 Compose 配置"
    curl -fsSL "$RAW_BASE_URL/docker-compose.yml" -o "$INSTALL_DIR/docker-compose.yml"
    prepare_env
    cd "$INSTALL_DIR"
    docker_cmd compose up -d

    public_port="${HTTP_PORT:-3000}"
    if [ -f .env ]; then
        parsed_port="$(awk -F= '/^HTTP_PORT=/{print $2}' .env | tail -1 | tr -d '[:space:]')"
        if [ -n "$parsed_port" ]; then
            public_port="$parsed_port"
        fi
    fi
    if wait_for_console "$public_port"; then
        log "部署完成：http://$(hostname -I 2>/dev/null | awk '{print $1}' || printf 'localhost'):${public_port}"
        log "浏览器只访问 Next 控制台；FastAPI / PostgreSQL / Redis 留在容器网内"
        log "首次登录：admin / admin123（登录后请立即修改密码）"
        docker_cmd compose ps
        exit 0
    fi
    docker_cmd compose ps
    docker_cmd compose logs --tail=80 frontend app || true
    fail "控制台健康检查超时，请运行：cd '$INSTALL_DIR' && docker compose logs -f"
}

main "$@"
