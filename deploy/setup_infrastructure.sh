#!/usr/bin/env bash
# setup_infrastructure.sh — Phase 1: create external Docker resources, start
# PostgreSQL + Redis.  If TLS certs exist, also start Nginx.  Never
# destructive (no volume removal, no database delete, no app deploy).
# Prerequisites: .env, Docker Compose v2, compose YAMLs in repo root.
set -Eeuo pipefail

INFRA_COMPOSE="compose.infrastructure.yml"
NETWORK_NAME="lms_network"
VOLUME_NAME="lms_static_data"

# Tiny .env key reader — never sources the file.
env_val() {
    local key="$1" default="$2" val
    [[ -f ".env" ]] || { printf '%s' "$default"; return; }
    val="$(awk -v k="$key" -F= 'index($0, k"=")==1 {sub(/^[^=]+=/, "", $0); gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0); gsub(/^"|"$/, "", $0); print; exit}' .env)"
    printf '%s' "${val:-$default}"
}

RUNTIME_DIR="$(env_val LMS_RUNTIME_DIR ./runtime)"
TLS_DIR="$(env_val NGINX_TLS_CERT_DIR ./certs)"

die() { echo "[FATAL] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

if [[ $# -gt 0 ]]; then
    case "$1" in
        -h|--help)
            echo "Usage: bash deploy/setup_infrastructure.sh"
            echo "Starts PostgreSQL and Redis; starts Nginx only after TLS files exist."
            exit 0
            ;;
        *) die "Unknown option: $1" ;;
    esac
fi

# ── Preflight ─────────────────────────────────────────────────────────────
[[ -f "$INFRA_COMPOSE" ]] || die "Run from repo root (missing $INFRA_COMPOSE)"
[[ -f ".env"          ]] || die "Missing .env — run 'bash deploy/setup_env.sh' first"

command -v docker >/dev/null 2>&1 || die "Docker is not installed"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"

info "Validating Compose config…"
docker compose --env-file .env -f "$INFRA_COMPOSE" config >/dev/null \
    || die "Compose config is invalid for $INFRA_COMPOSE"

# ── External resources (create only when absent — never remove) ─────────
info "Ensuring external Docker resources…"
if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    info "Network '$NETWORK_NAME' already exists."
else
    docker network create "$NETWORK_NAME" >/dev/null \
        || die "Failed to create network '$NETWORK_NAME'"
    info "Created network '$NETWORK_NAME'."
fi

if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    info "Volume '$VOLUME_NAME' already exists."
else
    docker volume create "$VOLUME_NAME" >/dev/null \
        || die "Failed to create volume '$VOLUME_NAME'"
    info "Created volume '$VOLUME_NAME'."
fi

# ── Start PostgreSQL + Redis ────────────────────────────────────────────
info "Starting PostgreSQL and Redis…"
docker compose --env-file .env -f "$INFRA_COMPOSE" up -d postgres redis \
    || die "Failed to start PostgreSQL or Redis"

info "Waiting for health (up to 60s)…"
for svc in postgres redis; do
    ok=false
    for i in $(seq 1 30); do
        if docker compose --env-file .env -f "$INFRA_COMPOSE" ps --status healthy --format '{{.Service}}' 2>/dev/null | grep -qFx "$svc"; then
            ok=true; break
        fi
        sleep 2
    done
    $ok || die "$svc not healthy within 60s — check 'docker compose logs $svc'"
done
info "PostgreSQL and Redis are healthy."

# ── Optional Nginx (only with TLS) ───────────────────────────────────────
CERT_FILE="$TLS_DIR/fullchain.pem"
KEY_FILE="$TLS_DIR/privkey.pem"

if [[ -r "$CERT_FILE" && -r "$KEY_FILE" ]]; then
    info "TLS certificates found. Preparing Nginx runtime state…"
    mkdir -p -m 700 "$RUNTIME_DIR"

    # Never overwrite existing valid state.
    if [[ ! -f "$RUNTIME_DIR/active-slot" ]]; then
        echo -n "blue" > "$RUNTIME_DIR/active-slot"
        info "Initialised active-slot to 'blue'."
    else
        info "active-slot already exists — not overwriting."
    fi
    if [[ ! -f "$RUNTIME_DIR/nginx-active-upstream.conf" ]]; then
        echo 'set $lms_app_upstream http://lms-app-blue:8000;' > "$RUNTIME_DIR/nginx-active-upstream.conf"
        info "Created upstream file for lms-app-blue."
    else
        info "Upstream file already exists — not overwriting."
    fi

    info "Starting Nginx…"
    docker compose --env-file .env -f "$INFRA_COMPOSE" up -d nginx || die "Failed to start Nginx"
    info "Validating Nginx config…"
    docker compose --env-file .env -f "$INFRA_COMPOSE" exec -T nginx nginx -t || die "Nginx config test FAILED"
    info "Nginx is running."
else
    info "TLS certificates NOT found at $TLS_DIR — Nginx intentionally not started."
fi

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
info "Infrastructure setup complete."
info "  Running: postgres, redis"
if [[ -r "$CERT_FILE" && -r "$KEY_FILE" ]]; then
    info "  Running: nginx"
else
    info "  Stopped: nginx (no TLS — provision $TLS_DIR/fullchain.pem + privkey.pem)"
fi
echo ""
info "Next steps:"
echo "  1. Provision TLS certificates  (skip if already done)"
echo "  2. Run forward migrations and deploy the application"
echo ""
echo "  To restart later: docker compose --env-file .env -f $INFRA_COMPOSE up -d postgres redis"
