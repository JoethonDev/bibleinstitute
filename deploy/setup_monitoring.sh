#!/usr/bin/env bash
# setup_monitoring.sh — create Dozzle credentials and start the read-only
# Docker log viewer behind the existing Nginx HTTPS proxy.
set -Eeuo pipefail

INFRA_COMPOSE="compose.infrastructure.yml"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.compose.env}"
DOZZLE_IMAGE="amir20/dozzle:v10.7.2@sha256:01f9018ffdaa0ec523f9a91dea3eff65b25cdb5f0566ac6d5a2cb4cf591e35e9"
DATA_DIR_DEFAULT="./runtime/dozzle"
HEALTH_ATTEMPTS=30
HEALTH_INTERVAL=2

die() { echo "[FATAL] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

env_val() {
    local key="$1" default="$2" val
    [[ -f ".env" ]] || { printf '%s' "$default"; return; }
    val="$(awk -v k="$key" -F= 'index($0, k"=")==1 {sub(/^[^=]+=/, "", $0); gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0); gsub(/^"|"$/, "", $0); print; exit}' .env)"
    printf '%s' "${val:-$default}"
}

usage() {
    cat <<'HELP'
Usage: bash deploy/setup_monitoring.sh

Creates the Dozzle simple-auth users file interactively if it does not exist,
then starts the isolated Docker socket proxy and Dozzle log viewer.

The viewer is available at https://monitor.<site-domain>/ after Nginx is
running. Existing credentials are never overwritten by this script.
HELP
}

if [[ $# -gt 0 ]]; then
    case "$1" in
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
fi

[[ -f "$INFRA_COMPOSE" ]] || die "Run from the repository root (missing $INFRA_COMPOSE)"
[[ -f ".env" ]] || die "Missing .env — run 'bash deploy/setup_env.sh' first"
[[ -f "$COMPOSE_ENV_FILE" ]] || die "Missing $COMPOSE_ENV_FILE — run 'bash deploy/setup_env.sh' first"
command -v docker >/dev/null 2>&1 || die "Docker is not installed"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"

DATA_DIR="$(env_val DOZZLE_DATA_DIR "$DATA_DIR_DEFAULT")"
USERS_FILE="$DATA_DIR/users.yml"
umask 077
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"

if [[ -e "$USERS_FILE" && ! -f "$USERS_FILE" ]]; then
    die "$USERS_FILE exists but is not a regular file"
fi

if [[ ! -f "$USERS_FILE" ]]; then
    read -r -p "Dozzle username [admin]: " username
    username="${username:-admin}"
    [[ "$username" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die "Username must contain only letters, numbers, dot, underscore, or hyphen"

    read -r -p "Dozzle display name [LMS Administrator]: " display_name
    display_name="${display_name:-LMS Administrator}"
    read -r -p "Dozzle email (optional): " email

    temp_file="${USERS_FILE}.tmp.$$"
    trap 'rm -f "$temp_file"' EXIT
    info "Generating the bcrypt Dozzle password interactively; it will not be printed or stored in shell history."
    generate_args=("$username" --name "$display_name")
    [[ -n "$email" ]] && generate_args+=(--email "$email")
    docker run --rm -it "$DOZZLE_IMAGE" generate "${generate_args[@]}" > "$temp_file" \
        || die "Dozzle credential generation failed"
    grep -Eq '^users:[[:space:]]*$' "$temp_file" || die "Generated Dozzle users file is invalid"
    grep -Eq '^[[:space:]]+password:[[:space:]]+\$2' "$temp_file" || die "Generated Dozzle password hash is missing"
    chmod 600 "$temp_file"
    mv "$temp_file" "$USERS_FILE"
    trap - EXIT
    info "Created $USERS_FILE with mode 600."
else
    chmod 600 "$USERS_FILE"
    grep -Eq '^users:[[:space:]]*$' "$USERS_FILE" || die "$USERS_FILE is missing the users section"
    grep -Eq '^[[:space:]]+password:[[:space:]]+\$2' "$USERS_FILE" || die "$USERS_FILE does not contain a bcrypt password hash"
    info "Existing Dozzle credentials preserved; no password was changed."
fi

export DOZZLE_DATA_DIR="$DATA_DIR"
info "Validating infrastructure Compose configuration…"
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" config >/dev/null \
    || die "Compose configuration is invalid"

info "Starting the isolated Docker socket proxy and Dozzle…"
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" up -d dozzle-socket-proxy dozzle \
    || die "Failed to start Dozzle monitoring services"

for svc in dozzle-socket-proxy dozzle; do
    healthy=false
    for _ in $(seq 1 "$HEALTH_ATTEMPTS"); do
        cid="$(docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" ps -q "$svc" 2>/dev/null || true)"
        state=""
        health=""
        if [[ -n "$cid" ]]; then
            state="$(docker inspect --format='{{.State.Status}}' "$cid" 2>/dev/null || true)"
            health="$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null || true)"
        fi
        if [[ "$state" == "running" && "$health" == "healthy" ]]; then
            healthy=true
            break
        fi
        sleep "$HEALTH_INTERVAL"
    done
    if ! $healthy; then
        docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" ps "$svc" || true
        docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" logs --tail=100 "$svc" || true
        die "$svc did not become healthy within $((HEALTH_ATTEMPTS * HEALTH_INTERVAL))s"
    fi
done

site_domain="$(env_val DJANGO_SITE_DOMAIN "")"
site_domain="${site_domain#https://}"
site_domain="${site_domain#http://}"
site_domain="${site_domain%%/*}"
monitoring_domain="$(env_val MONITORING_DOMAIN "")"
monitoring_domain="${monitoring_domain#https://}"
monitoring_domain="${monitoring_domain#http://}"
monitoring_domain="${monitoring_domain%%/*}"
monitoring_domain="${monitoring_domain:-monitor.${site_domain}}"
info "Dozzle is healthy and available at https://${monitoring_domain}/"
