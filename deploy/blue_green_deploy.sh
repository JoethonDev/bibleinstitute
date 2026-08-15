#!/usr/bin/env bash
# ============================================================================
# blue_green_deploy.sh — Blue/green deployment for the Bible Institute LMS
#
# This script is invoked by the GitHub Actions workflow (or manually) from
# the repository root on the production server.  It:
#
# 1. Acquires a lock (/tmp/lms-blue-green.lock) to prevent concurrent deploys.
# 2. Resolves the target slot (inactive blue or green), loads runtime state.
# 3. Verifies infrastructure containers (postgres, redis, nginx) are running.
# 4. Runs forward migrations and collectstatic in the target slot.
# 5. Builds and starts the inactive app slot + celery + media-worker with the new image.
# 6. Waits for the new slot's Docker healthcheck to pass.
# 7. Atomically switches the Nginx upstream pointer to the new slot.
# 8. Verifies HTTPS reachability through Nginx (curl smoke with retries).
# 9. Optionally stops the old web slot (STOP_OLD_SLOT=true).
# 10. On any failure before switch, aborts without changing the upstream.
#
# ⚠  MIGRATION COMPATIBILITY
#    Forward migrations are assumed backward-compatible with the still-running
#    active slot.  The script does NOT roll back migrations on failure — only
#    the Nginx pointer is reverted.  If a migration is destructive, the deploy
#    may break the active slot temporarily; test migrations on a staging
#    environment first.
# ============================================================================
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOCK_FILE="/tmp/lms-blue-green.lock"
LOCK_TIMEOUT=300  # seconds — a deploy should never take longer than 5 min

read_env_setting() {
    local key="$1" configured
    [[ -f ".env" ]] || return 0
    configured="$(awk -v key="$key" -F= '$1 ~ "^[[:space:]]*" key "[[:space:]]*$" {sub(/^[[:space:]]*/, "", $2); sub(/[[:space:]]*$/, "", $2); print $2; exit}' .env)"
    configured="${configured%$'\r'}"
    configured="${configured#\"}"
    configured="${configured%\"}"
    configured="${configured#\'}"
    configured="${configured%\'}"
    printf '%s' "$configured"
}

resolve_runtime_dir() {
    local configured="${LMS_RUNTIME_DIR:-}"
    if [[ -z "$configured" ]]; then
        configured="$(read_env_setting LMS_RUNTIME_DIR)"
    fi
    printf '%s' "${configured:-./runtime}"
}

RUNTIME_DIR="$(resolve_runtime_dir)"
ACTIVE_SLOT_FILE="$RUNTIME_DIR/active-slot"
UPSTREAM_FILE="$RUNTIME_DIR/nginx-active-upstream.conf"
TMP_UPSTREAM_FILE="${UPSTREAM_FILE}.tmp"
PRIOR_SLOT_FILE="${RUNTIME_DIR}/.prior-active-slot"
PRIOR_UPSTREAM_FILE="${RUNTIME_DIR}/.prior-upstream.conf"
TMP_SLOT_FILE="${ACTIVE_SLOT_FILE}.tmp"

INFRA_COMPOSE="compose.infrastructure.yml"
APP_COMPOSE="compose.application.yml"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.compose.env}"

HEALTHCHECK_URL="${DEPLOY_HEALTHCHECK_URL:-$(read_env_setting DEPLOY_HEALTHCHECK_URL)}"
HTTPS_PORT="${NGINX_HTTPS_PORT:-$(read_env_setting NGINX_HTTPS_PORT)}"
HTTPS_PORT="${HTTPS_PORT:-443}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-https://127.0.0.1:${HTTPS_PORT}/}"
HEALTH_RETRIES=5
HEALTH_INTERVAL=5  # seconds
STOP_OLD_SLOT="${STOP_OLD_SLOT:-$(read_env_setting STOP_OLD_SLOT)}"
STOP_OLD_SLOT="${STOP_OLD_SLOT:-false}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
die() { echo "[FATAL] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*"; }

cleanup() {
    # Remove any temporary artifacts — never clean up the lock here
    # because we want to keep it for the whole deploy lifecycle.
    rm -f "$TMP_UPSTREAM_FILE" "$TMP_SLOT_FILE"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
[[ -f "$INFRA_COMPOSE" ]] || die "Run this script from the repository root."
[[ -f "$APP_COMPOSE" ]] || die "Missing $APP_COMPOSE."
[[ -f ".env" ]] || die "Missing .env — deployment requires it for Compose."
[[ -f "$COMPOSE_ENV_FILE" ]] || die "Missing $COMPOSE_ENV_FILE — run 'bash deploy/setup_env.sh' first."

# Required commands
for cmd in docker curl git flock; do
    command -v "$cmd" >/dev/null 2>&1 || die "Missing required command: $cmd"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."

# Reject dirty working tree — untracked files inside the runtime directory
# are expected and acceptable, but tracked-file modifications are not.
if ! git diff --quiet HEAD 2>/dev/null; then
    die "Working tree has uncommitted changes. Refusing to deploy."
fi
if [[ -n "$(git ls-files --others --exclude-standard 2>/dev/null | grep -v "^${RUNTIME_DIR#./}/" || true)" ]]; then
    die "Working tree has untracked files outside the runtime directory. Refusing to deploy."
fi
info "Working tree is clean."

# Give every deployment a unique static URL key. Nginx does not maintain a
# proxy cache for /static/, but browsers and the CDN cache immutable asset
# URLs. The shell environment overrides the interpolation env file for all
# target build/run/start commands below.
STATIC_ASSET_VERSION="$(git rev-parse --short=12 HEAD)-$(date -u +%Y%m%d%H%M%S)"
export STATIC_ASSET_VERSION
info "Static asset release: $STATIC_ASSET_VERSION"

# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------
info "Acquiring deployment lock (timeout: ${LOCK_TIMEOUT}s)…"
exec 200>"$LOCK_FILE"
flock -w "$LOCK_TIMEOUT" 200 || die "Could not acquire lock within ${LOCK_TIMEOUT}s — another deploy may be running."
info "Lock acquired."

# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------
mkdir -p -m 700 "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"

# active-slot. A missing state is a first deployment: blue is the first target
# and no old slot is stopped or treated as a rollback source.
INITIAL_DEPLOY=false
if [[ ! -f "$ACTIVE_SLOT_FILE" ]]; then
    INITIAL_DEPLOY=true
    ACTIVE_SLOT=""
    TARGET_SLOT="blue"
    warn "No active-slot found — bootstrapping blue as the first target."
else
    ACTIVE_SLOT=$(tr -d '[:space:]' < "$ACTIVE_SLOT_FILE")
    case "$ACTIVE_SLOT" in
        blue|green)
            ACTIVE_RUNNING=$(docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" ps --status running --format '{{.Service}}' 2>/dev/null | grep -Fx "lms-app-${ACTIVE_SLOT}" || true)
            if [[ -z "$ACTIVE_RUNNING" ]]; then
                INITIAL_DEPLOY=true
                TARGET_SLOT="$ACTIVE_SLOT"
                warn "Configured active slot '$ACTIVE_SLOT' is not running — treating this as the first application deployment."
            elif [[ "$ACTIVE_SLOT" == "blue" ]]; then
                TARGET_SLOT="green"
            else
                TARGET_SLOT="blue"
            fi
            ;;
        *) die "Invalid active-slot value: '$ACTIVE_SLOT'. Expected 'blue' or 'green'." ;;
    esac
fi

if $INITIAL_DEPLOY; then
    info "First deployment target: $TARGET_SLOT"
else
    info "Active slot: $ACTIVE_SLOT → Target slot: $TARGET_SLOT"
fi

# Upstream file
if [[ -f "$UPSTREAM_FILE" ]]; then
    CONTENT=$(tr -d '[:space:]' < "$UPSTREAM_FILE")
    VALID=false
    for slot in blue green; do
        if [[ "$CONTENT" == "set\$lms_app_upstreamhttp://lms-app-${slot}:8000;" ]]; then
            VALID=true
            break
        fi
    done
    $VALID || die "Upstream file has unexpected content — refusing to deploy."
    [[ -n "$ACTIVE_SLOT" ]] || die "Upstream state exists but active-slot is missing — manual reconciliation required."
else
    $INITIAL_DEPLOY || die "Upstream file missing — run deploy/setup_infrastructure.sh first."
fi

# Verify active-slot matches upstream file
ACTIVE_UPSTREAM_SLOT=""
if ! $INITIAL_DEPLOY; then
    for slot in blue green; do
        if echo "$CONTENT" | grep -q "lms-app-${slot}:8000"; then
            ACTIVE_UPSTREAM_SLOT="$slot"
            break
        fi
    done
    if [[ "$ACTIVE_UPSTREAM_SLOT" != "$ACTIVE_SLOT" ]]; then
        die "active-slot ($ACTIVE_SLOT) and upstream file ($ACTIVE_UPSTREAM_SLOT) disagree — manual fix required."
    fi
fi

# ---------------------------------------------------------------------------
# Verify infrastructure is running
# ---------------------------------------------------------------------------
info "Verifying infrastructure containers…"
for svc in postgres redis nginx; do
    CID="$(docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" ps -q "$svc" 2>/dev/null || true)"
    [[ -n "$CID" ]] || die "Infrastructure service '$svc' has no container. Start it first via deploy/setup_infrastructure.sh."
    STATE="$(docker inspect --format='{{.State.Status}}' "$CID" 2>/dev/null || true)"
    [[ "$STATE" == "running" ]] || die "Infrastructure service '$svc' is not running (state: ${STATE:-unknown})."
    if [[ "$svc" == "postgres" || "$svc" == "redis" ]]; then
        HEALTH="$(docker inspect --format='{{.State.Health.Status}}' "$CID" 2>/dev/null || true)"
        [[ "$HEALTH" == "healthy" ]] || die "Infrastructure service '$svc' is not healthy (health: ${HEALTH:-unknown})."
    fi
done
info "All infrastructure services are running and healthy (Nginx running, Postgres/Redis healthy)."

# Also verify the target app slot is NOT currently running (it should be inactive)
TARGET_RUNNING=$(docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" ps --status running --format '{{.Service}}' 2>/dev/null | grep -Fx "lms-app-${TARGET_SLOT}" || true)
if [[ -n "$TARGET_RUNNING" ]]; then
    warn "Target slot lms-app-${TARGET_SLOT} is already running — stopping it first."
    docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" stop "lms-app-${TARGET_SLOT}" \
        || die "Failed to stop already-running target slot."
fi

# ---------------------------------------------------------------------------
# Save prior state for rollback
# ---------------------------------------------------------------------------
if ! $INITIAL_DEPLOY; then
    printf '%s' "$ACTIVE_SLOT" > "$PRIOR_SLOT_FILE"
    cat "$UPSTREAM_FILE" > "$PRIOR_UPSTREAM_FILE"
fi
info "Saved prior state for rollback."

# ---------------------------------------------------------------------------
# Step 1 — Build the target slot, celery, and media-worker images first
# ---------------------------------------------------------------------------
info "Building lms-app-${TARGET_SLOT}, celery, and media-worker images…"
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" build \
    "lms-app-${TARGET_SLOT}" celery media-worker \
    || die "Image build failed — aborting."

# ---------------------------------------------------------------------------
# Step 2 — Run migrations and collectstatic from the newly built image
# ---------------------------------------------------------------------------
info "Running forward migrations (target: lms-app-${TARGET_SLOT})…"
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" run --rm --no-deps \
    "lms-app-${TARGET_SLOT}" \
    python manage.py migrate --noinput \
    || die "Migration failed — aborting. No upstream change was made."

info "Running collectstatic…"
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" run --rm --no-deps \
    "lms-app-${TARGET_SLOT}" \
    python manage.py collectstatic --noinput \
    || die "collectstatic failed — aborting."

info "Verifying collected responsive CSS…"
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" run --rm --no-deps \
    "lms-app-${TARGET_SLOT}" \
    python -c 'from pathlib import Path; p=Path("/app/collectstatic/css/app_admin.css"); text=p.read_text(); required=(".admin-sidebar.ad-rail {\n    display: flex;", "#mobileAdminMenu", "grid-column: 1 / -1", "grid-template-columns: minmax(0, 1fr)"); missing=[marker for marker in required if marker not in text]; raise SystemExit("stale or incomplete app_admin.css: " + ", ".join(missing)) if missing else None' \
    || die "Collected CSS verification failed — aborting."

# ---------------------------------------------------------------------------
# Step 3 — Start the target slot + celery + media-worker from the already-built
# image. media-worker is shared between the blue/green web slots; its command,
# queue, concurrency, volume, and environment are defined only in
# compose.application.yml and are never duplicated here.
# ---------------------------------------------------------------------------
info "Starting lms-app-${TARGET_SLOT}, celery, and media-worker…"
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" up -d --force-recreate --no-build \
    "lms-app-${TARGET_SLOT}" celery media-worker \
    || die "Failed to start target slot, celery, and media-worker."

# ---------------------------------------------------------------------------
# Step 4 — Wait for target healthcheck
# ---------------------------------------------------------------------------
info "Waiting for lms-app-${TARGET_SLOT} healthcheck…"
TARGET_CID=$(docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" ps -q "lms-app-${TARGET_SLOT}" 2>/dev/null || true)
if [[ -z "$TARGET_CID" ]]; then
    die "Could not find container ID for lms-app-${TARGET_SLOT}."
fi

HEALTHY=false
for i in $(seq 1 "$HEALTH_RETRIES"); do
    HEALTH_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$TARGET_CID" 2>/dev/null || echo "starting")
    if [[ "$HEALTH_STATUS" == "healthy" ]]; then
        HEALTHY=true
        info "lms-app-${TARGET_SLOT} is healthy after ~$(( i * 10 ))s."
        break
    fi
    if [[ "$HEALTH_STATUS" == "unhealthy" ]]; then
        die "lms-app-${TARGET_SLOT} became unhealthy — aborting. No upstream change made."
    fi
    sleep "$HEALTH_INTERVAL"
done

if ! $HEALTHY; then
    # Collect logs before failing
    docker logs "$TARGET_CID" --tail 30 2>&1 || true
    die "lms-app-${TARGET_SLOT} did not become healthy within timeout. Check logs above."
fi
info "Target slot healthcheck passed."

# Celery has no HTTP health endpoint. Confirm its selected container is running
# after the image replacement; task-level monitoring remains operational work.
CELERY_CID="$(docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" ps -q celery 2>/dev/null || true)"
[[ -n "$CELERY_CID" ]] || die "Could not find the Celery container after startup."
CELERY_STATE="$(docker inspect --format='{{.State.Status}}' "$CELERY_CID" 2>/dev/null || true)"
[[ "$CELERY_STATE" == "running" ]] || die "Celery is not running after startup (state: ${CELERY_STATE:-unknown})."
info "Celery container is running."

# ---------------------------------------------------------------------------
# Step 5 — Atomic upstream switch
# ---------------------------------------------------------------------------
info "Writing new upstream file (pointing to lms-app-${TARGET_SLOT})…"
cat > "$TMP_UPSTREAM_FILE" <<-NGINX
set \$lms_app_upstream http://lms-app-${TARGET_SLOT}:8000;
NGINX

# Validate the temporary file content before moving
TMP_CONTENT=$(tr -d '[:space:]' < "$TMP_UPSTREAM_FILE")
EXPECTED="set\$lms_app_upstreamhttp://lms-app-${TARGET_SLOT}:8000;"
[[ "$TMP_CONTENT" == "$EXPECTED" ]] || die "Temporary upstream file content is malformed — aborting."

# Atomic move
mv "$TMP_UPSTREAM_FILE" "$UPSTREAM_FILE"
info "Upstream file updated atomically."

# ---------------------------------------------------------------------------
# Step 6 — Nginx reload
# ---------------------------------------------------------------------------
info "Validating Nginx configuration inside the container…"
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" exec -T nginx nginx -t \
    || {
        warn "Nginx config test FAILED after upstream switch — restoring prior state."
        $INITIAL_DEPLOY && die "CRITICAL: First deployment has no prior upstream; manual intervention required."
        cp "$PRIOR_UPSTREAM_FILE" "$UPSTREAM_FILE"
        docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" exec -T nginx nginx -t \
            || die "CRITICAL: Rollback upstream also failed Nginx config test — manual intervention required."
        docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" exec -T nginx nginx -s reload \
            || die "CRITICAL: Rollback reload also failed — manual Nginx reload required."
        die "Deploy aborted — Nginx config test failed after upstream switch. Prior state restored."
    }

info "Reloading Nginx…"
docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" exec -T nginx nginx -s reload \
    || {
        warn "Nginx reload FAILED — restoring prior upstream."
        $INITIAL_DEPLOY && die "CRITICAL: First deployment has no prior upstream; manual intervention required."
        cp "$PRIOR_UPSTREAM_FILE" "$UPSTREAM_FILE"
        docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" exec -T nginx nginx -t \
            || die "CRITICAL: Rollback upstream also failed Nginx config test."
        docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" exec -T nginx nginx -s reload \
            || die "CRITICAL: Rollback reload also failed."
        die "Deploy aborted — Nginx reload failed. Prior state restored."
    }
info "Nginx reloaded successfully."

# ---------------------------------------------------------------------------
# Step 7 — HTTP smoke check through Nginx
# ---------------------------------------------------------------------------
info "Running HTTP smoke check against ${HEALTHCHECK_URL}…"
SMOKE_OK=false
for i in $(seq 1 "$HEALTH_RETRIES"); do
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 "$HEALTHCHECK_URL" 2>/dev/null || echo "000")
    # Accept any 2xx or 3xx (login redirect is expected for protected pages)
    if [[ "$HTTP_CODE" =~ ^[23][0-9][0-9]$ ]]; then
        SMOKE_OK=true
        info "Smoke check passed (HTTP ${HTTP_CODE}) after ~$(( i * 10 ))s."
        break
    fi
    if [[ "$HTTP_CODE" == "000" ]]; then
        info "Smoke attempt ${i}: connection refused or timeout."
    else
        info "Smoke attempt ${i}: HTTP ${HTTP_CODE}."
    fi
    sleep "$HEALTH_INTERVAL"
done

if ! $SMOKE_OK; then
    warn "Smoke check against new slot FAILED — rolling back upstream pointer."
    if $INITIAL_DEPLOY; then
        die "Deploy aborted — first-deployment smoke failed; target remains available for manual diagnosis."
    fi
    cp "$PRIOR_UPSTREAM_FILE" "$UPSTREAM_FILE"
    docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" exec -T nginx nginx -t \
        || die "CRITICAL: Rollback upstream Nginx config test failed."
    docker compose --env-file "$COMPOSE_ENV_FILE" -f "$INFRA_COMPOSE" exec -T nginx nginx -s reload \
        || die "CRITICAL: Rollback Nginx reload failed."
    # Stop the failed new slot to avoid confusion
    docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" stop "lms-app-${TARGET_SLOT}" \
        || warn "Could not stop failed new slot — manual stop may be required."
    info "Upstream restored to lms-app-${ACTIVE_SLOT}. New slot stopped."
    die "Deploy aborted — smoke check failed after upstream switch."
fi

# ---------------------------------------------------------------------------
# Step 8 — Mark active slot
# ---------------------------------------------------------------------------
printf '%s' "$TARGET_SLOT" > "$TMP_SLOT_FILE"
mv "$TMP_SLOT_FILE" "$ACTIVE_SLOT_FILE"
info "Active slot updated to '$TARGET_SLOT'."

# ---------------------------------------------------------------------------
# Step 9 — Optionally stop old web slot
# ---------------------------------------------------------------------------
if [[ "${STOP_OLD_SLOT,,}" == "true" && "$INITIAL_DEPLOY" == "false" ]]; then
    info "Stopping old web slot lms-app-${ACTIVE_SLOT}…"
    docker compose --env-file "$COMPOSE_ENV_FILE" -f "$APP_COMPOSE" stop "lms-app-${ACTIVE_SLOT}" \
        || warn "Could not stop old web slot — manual stop may be required."
    info "Old web slot stopped."
else
    if $INITIAL_DEPLOY; then
        info "First deployment completed; no previous web slot existed."
    else
        info "Old web slot lms-app-${ACTIVE_SLOT} remains running for rollback."
        info "Set STOP_OLD_SLOT=true to automatically stop it on future deploys."
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
info "╔═══════════════════════════════════════════════════════════════╗"
info "║  Blue/green deployment completed successfully!               ║"
info "║  New slot: lms-app-${TARGET_SLOT} (was lms-app-${ACTIVE_SLOT})"
info "║  Celery was rebuilt and restarted with the new image.        ║"
info "╚═══════════════════════════════════════════════════════════════╝"
info ""
info "⚠  Migration compatibility note:"
info "   Forward migrations were run against the database shared by"
info "   both slots.  If the old slot (lms-app-${ACTIVE_SLOT}) is still"
info "   running, it must remain compatible with the migrated schema."
info "   No automatic migration rollback is attempted."

# Clean exit — lock is released by the shell when fd 200 closes
exit 0
