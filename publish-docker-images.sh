#!/usr/bin/env bash
# Build linux/amd64 + linux/arm64 images and push them to Docker Hub.
#
# Usage:
#   ./publish-docker-images.sh              # API + web
#   ./publish-docker-images.sh api
#   ./publish-docker-images.sh web
#   ./publish-docker-images.sh --no-push    # compile only (stays in the builder)
#
# Env:
#   DOCKERHUB_USERNAME   default e54385991
#   IMAGE_TAG            default latest
#   DOCKER_BUILDER       default upkk-multi
#   DOCKER_PLATFORMS     default linux/amd64,linux/arm64
#   DEPLOYMENT_ID        optional frontend release id (defaults to GIT_SHA;
#                        dirty worktrees get a unique suffix)
#   NEXT_SERVER_ACTIONS_ENCRYPTION_KEY  optional stable Base64 AES key for
#                                      independently built/multi-instance web
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

API_REPO="upkk-cs2-server-manager"
WEB_REPO="upkk-cs2-server-manager-web"
USERNAME="${DOCKERHUB_USERNAME:-e54385991}"
TAG="${IMAGE_TAG:-latest}"
BUILDER="${DOCKER_BUILDER:-upkk-multi}"
PLATFORMS="${DOCKER_PLATFORMS:-linux/amd64,linux/arm64}"
GIT_SHA_VALUE="${GIT_SHA:-$(git rev-parse HEAD 2>/dev/null || printf '%s' unknown)}"
BUILD_TIME_VALUE="${BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
if [ -n "${DEPLOYMENT_ID:-}" ]; then
    DEPLOYMENT_ID_VALUE="$DEPLOYMENT_ID"
else
    DEPLOYMENT_ID_VALUE="$GIT_SHA_VALUE"
    if ! git diff --quiet || ! git diff --cached --quiet; then
        DEPLOYMENT_ID_VALUE="${GIT_SHA_VALUE}-dirty-$(date -u +%Y%m%d%H%M%S)"
    fi
fi
TARGET="all"
PUSH=1

usage() {
    sed -n '2,16p' "$0"
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

log() {
    printf '\n==> %s\n' "$*"
}

for arg in "$@"; do
    case "$arg" in
        api | web | all) TARGET="$arg" ;;
        --no-push) PUSH=0 ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            fail "unknown argument: $arg (try --help)"
            ;;
    esac
done

[ -f "$ROOT/Dockerfile" ] || fail "run this from the repository root (missing Dockerfile)"
[ -f "$ROOT/frontend/Dockerfile" ] || fail "missing frontend/Dockerfile"

command -v docker >/dev/null 2>&1 || fail "docker is not installed or not on PATH"
docker info >/dev/null 2>&1 || fail "cannot talk to the Docker daemon (start Docker Desktop / Colima / dockerd)"
docker buildx version >/dev/null 2>&1 || fail "docker buildx is required"

if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
    log "creating buildx builder ${BUILDER}"
    docker buildx create --name "$BUILDER" --driver docker-container --bootstrap
fi

if [ "$PUSH" -eq 1 ]; then
    log "pushing as ${USERNAME} (run 'docker login' first if this fails)"
fi

publish() {
    local name="$1"
    local file="$2"
    local context="$3"
    local image="docker.io/${USERNAME}/${name}:${TAG}"
    local extra=()
    if [ "$PUSH" -eq 1 ]; then
        extra+=(--push)
    fi
    local build_secrets=()
    local build_args=(--build-arg "GIT_SHA=${GIT_SHA_VALUE}" --build-arg "BUILD_TIME=${BUILD_TIME_VALUE}")
    if [ "$name" = "$WEB_REPO" ]; then
        build_args+=(--build-arg "DEPLOYMENT_ID=${DEPLOYMENT_ID_VALUE}")
    fi
    if [ -n "${NEXT_SERVER_ACTIONS_ENCRYPTION_KEY:-}" ] && [ "$name" = "$WEB_REPO" ]; then
        build_secrets+=(--secret "id=next_server_actions_key,env=NEXT_SERVER_ACTIONS_ENCRYPTION_KEY")
    fi
    log "building ${image} (${PLATFORMS})"
    local command=(
        docker buildx build
        --builder "$BUILDER" \
        --platform "$PLATFORMS" \
        --provenance=false \
        --sbom=false \
        "${build_args[@]}"
    )
    if ((${#build_secrets[@]})); then
        command+=("${build_secrets[@]}")
    fi
    command+=( -f "$file" -t "$image" )
    if ((${#extra[@]})); then
        command+=("${extra[@]}")
    fi
    command+=("$context")
    "${command[@]}"
    if [ "$PUSH" -eq 1 ]; then
        docker buildx imagetools inspect "$image" --format "{{.Manifest.Digest}}"
    fi
}

case "$TARGET" in
    api)
        publish "$API_REPO" "$ROOT/Dockerfile" "$ROOT"
        ;;
    web)
        publish "$WEB_REPO" "$ROOT/frontend/Dockerfile" "$ROOT/frontend"
        ;;
    all)
        publish "$API_REPO" "$ROOT/Dockerfile" "$ROOT"
        publish "$WEB_REPO" "$ROOT/frontend/Dockerfile" "$ROOT/frontend"
        ;;
esac

log "done"
if [ "$PUSH" -eq 1 ]; then
    printf 'Pull on the host, then recreate (do not uninstall, do not delete volumes):\n'
    if [ "$TARGET" = "all" ] || [ "$TARGET" = "api" ]; then
        printf '  docker pull docker.io/%s/%s:%s\n' "$USERNAME" "$API_REPO" "$TAG"
    fi
    if [ "$TARGET" = "all" ] || [ "$TARGET" = "web" ]; then
        printf '  docker pull docker.io/%s/%s:%s\n' "$USERNAME" "$WEB_REPO" "$TAG"
    fi
fi
