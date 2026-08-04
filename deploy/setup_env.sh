#!/usr/bin/env bash
# setup_env.sh — Interactive .env setup. Phase 1: core settings only.
# Prompts domain, PostgreSQL, Django secret, TLS dir.  Rerunnable.
# Creates a minimal runtime .env on the first run or updates an existing .env
# on reruns.  Existing optional email/R2/Worker settings are preserved.
set -Eeuo pipefail

TMP=".env.$$.tmp"
trap 'rm -f "$TMP"' EXIT

die() { echo "[FATAL] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

usage() {
    cat <<'HELP'
Usage: bash deploy/setup_env.sh

  Interactive guided .env setup.  Prompts one setting at a time,
  shows existing values as defaults.  Secrets are hidden.
  Auto-generates Django secret and DB password when left blank.

  TLS certificates are NOT provisioned here — do that separately.
  Rerun at any time to update settings.

Options:  --help, -h    Show this message.
HELP
    exit 0
}

# ── Helpers ────────────────────────────────────────────────────────────

# read_value KEY PROMPT DEFAULT — show current value (or DEFAULT) as default
read_value() {
    local key="$1" prompt="$2" default="$3" cur input
    cur="$(awk -v k="$key" 'index($0, k"=")==1{sub(/^[^=]+=/, "", $0); print; exit}' "$TMP")"
    cur="${cur:-$default}"
    if [[ -n "$cur" ]]; then
        read -p "$prompt [$cur]: " input; echo "${input:-$cur}"
    else
        read -p "$prompt: " input; echo "$input"
    fi
}

# write_value KEY VALUE — upsert KEY=VALUE in $TMP via awk
write_value() {
    local k="$1" v="$2"
    awk -v k="$k" -v v="$v" 'BEGIN{f=0} index($0,k"=")==1{print k"="v;f=1;next} {print} END{if(!f)print k"="v}' \
        "$TMP" > "${TMP}.w" && mv "${TMP}.w" "$TMP"
}

gen_secret() {
    if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
    else dd if=/dev/urandom bs=48 count=1 2>/dev/null | base64 | tr -d '\n='
    fi
}

# ── Preflight ──────────────────────────────────────────────────────────
[[ $# -eq 0 ]] || { case "$1" in -h|--help) usage ;; *) die "Unknown: $1" ;; esac; }
[[ -f compose.infrastructure.yml ]] || die "Run from repo root"

# ── Base file ──────────────────────────────────────────────────────────
echo ""
echo "Bible Institute LMS — .env Setup"
echo ""
if [[ -f .env ]]; then
    cp .env "$TMP"; info "Updating existing .env"
    HAS_EXISTING_ENV=true
else
    : > "$TMP"; info "Creating minimal .env"
    HAS_EXISTING_ENV=false
fi

# ── 1. Domain ──────────────────────────────────────────────────────────
if $HAS_EXISTING_ENV; then
    raw="$(read_value DJANGO_SITE_DOMAIN "Primary domain (e.g. bibleinstitute-eg.org)" "")"
else
    read -p "Primary domain (e.g. bibleinstitute-eg.org): " raw
fi
domain="${raw#https://}"; domain="${domain#http://}"; domain="${domain%%/*}"
[[ -n "$domain" ]] || die "Domain cannot be empty"
[[ "$domain" != *[[:space:]]* ]] || die "Domain cannot contain spaces"
write_value DJANGO_ALLOWED_HOSTS "$domain"
write_value DJANGO_CSRF_TRUSTED_ORIGINS "https://$domain"
write_value DJANGO_SITE_DOMAIN "https://$domain"
write_value DEPLOY_HEALTHCHECK_URL "https://${domain}/"
echo ""

# ── 2. PostgreSQL ──────────────────────────────────────────────────────
write_value POSTGRES_DB "$(read_value POSTGRES_DB "Database name" lms_database)"
write_value POSTGRES_USER "$(read_value POSTGRES_USER "Database user" lms_user)"
write_value POSTGRES_HOST postgres
write_value POSTGRES_PORT 5432
existing_pass="$(awk -v k="POSTGRES_PASSWORD" 'index($0,k"=")==1{sub(/^[^=]+=/, "", $0); print; exit}' "$TMP")"
# Treat template placeholder as empty
if [[ "$existing_pass" == "dev_password_change_me" ]]; then existing_pass=""; fi
if [[ -n "$existing_pass" ]]; then
    echo -n "Database password [press Enter to keep existing]: " >&2
    read -s pass_input; echo >&2
    pass="${pass_input:-$existing_pass}"
else
    echo -n "Database password (blank = auto-generate): " >&2
    read -s pass_input; echo >&2
    pass="${pass_input:-$(gen_secret)}"
fi
write_value POSTGRES_PASSWORD "$pass"
write_value REDIS_URL redis://redis:6379/0
write_value CELERY_BROKER_URL redis://redis:6379/0
write_value CELERY_RESULT_BACKEND redis://redis:6379/0
write_value CELERY_CONCURRENCY 2
echo ""

# ── 3. Django Secret Key ───────────────────────────────────────────────
existing_key="$(awk -v k="DJANGO_SECRET_KEY" 'index($0,k"=")==1{sub(/^[^=]+=/, "", $0); print; exit}' "$TMP")"
# Treat template placeholder as empty
if [[ "$existing_key" == "insecure_dev_key_replace_me" ]]; then existing_key=""; fi
if [[ -n "$existing_key" ]]; then
    echo -n "Django secret key [press Enter to keep existing]: " >&2
    read -s key_input; echo >&2
    key="${key_input:-$existing_key}"
else
    echo -n "Django secret key (blank = auto-generate): " >&2
    read -s key_input; echo >&2
    key="${key_input:-$(gen_secret)}"
fi
write_value DJANGO_SECRET_KEY "$key"
echo ""

# ── 4. TLS directory ───────────────────────────────────────────────────
tls_dir="$(read_value NGINX_TLS_CERT_DIR "TLS certificate directory" ./certs)"
write_value NGINX_TLS_CERT_DIR "$tls_dir"
echo ""

# ── Production defaults ────────────────────────────────────────────────
write_value DJANGO_DEBUG False
write_value NGINX_HTTP_PORT 80
write_value NGINX_HTTPS_PORT 443
write_value GUNICORN_TIMEOUT 1800
write_value WEB_CONCURRENCY 2
write_value WEB_THREADS 8
write_value DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE 2147483648
write_value DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE 10485760

# ── Atomic write ───────────────────────────────────────────────────────
chmod 600 "$TMP"
mv "$TMP" .env

info ".env written with mode 600"
echo ""
echo "  Next steps:"
echo "    1. Provision TLS certificates at: $tls_dir"
echo "    2. Run: bash deploy/setup_infrastructure.sh"
echo "    3. Deploy the application after TLS is ready"
echo ""
echo "  Rerun this script at any time to update settings."
