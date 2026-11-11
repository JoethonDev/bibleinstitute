#!/usr/bin/env bash
# setup_env.sh - Interactive .env setup.  Core settings plus optional Telegram
# infrastructure settings (encryption key and dedicated R2 bucket).
# Prompts domain, PostgreSQL, Django secret, TLS dir.  Rerunnable.
# Creates a minimal runtime .env on the first run or updates an existing .env
# on reruns.  Existing optional email/R2/Worker/Telegram settings are preserved.
# The Telegram bot token and webhook secret are NOT environment variables:
# they are dashboard/database-managed, encrypted, and never written here.
set -Eeuo pipefail

TMP=".env.$$.tmp"
COMPOSE_TMP=".compose.env.$$.tmp"
trap 'rm -f "$TMP" "$COMPOSE_TMP"' EXIT

die() { echo "[FATAL] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

usage() {
    cat <<'HELP'
Usage: bash deploy/setup_env.sh

  Interactive guided .env setup.  Prompts one setting at a time,
  shows existing values as defaults.  Secrets are hidden.
  Auto-generates Django secret and DB password when left blank.

  TLS certificates are NOT provisioned here - do that separately.
  Rerun at any time to update settings.

  Telegram: the optional encryption key and dedicated R2 bucket are
  prompted below.  The bot token and webhook secret are managed in the
  admin dashboard and stored encrypted in the database - they are never
  written to .env by this script.

Options:  --help, -h    Show this message.
HELP
    exit 0
}

# -- Helpers ------------------------------------------------------------

# read_value KEY PROMPT DEFAULT - show current value (or DEFAULT) as default
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

# write_value KEY VALUE - upsert KEY=VALUE in $TMP via awk
write_value() {
    local k="$1" v="$2"
    awk -v k="$k" -v v="$v" 'BEGIN{f=0} index($0,k"=")==1{print k"="v;f=1;next} {print} END{if(!f)print k"="v}' \
        "$TMP" > "${TMP}.w" && mv "${TMP}.w" "$TMP"
}

value_from_tmp() {
    local key="$1" default="$2" value
    value="$(awk -v k="$key" 'index($0, k"=")==1{sub(/^[^=]+=/, "", $0); print; exit}' "$TMP")"
    printf '%s' "${value:-$default}"
}

# read_secret_value KEY PROMPT - hidden input; never prints the current value
read_secret_value() {
    local key="$1" prompt="$2" current input force_reentry=false
    current="$(value_from_tmp "$key" "")"
    if [[ "$current" == \"*\" && "$current" == *\" ]]; then
        printf '%s' "$key has surrounding quote characters; re-enter without quotes: " >&2
        force_reentry=true
    elif [[ -n "$current" ]]; then
        printf '%s' "$prompt [press Enter to keep existing]: " >&2
    else
        printf '%s' "$prompt: " >&2
    fi
    read -r -s input
    echo >&2
    if $force_reentry && [[ -z "$input" ]]; then
        die "$key must be re-entered without surrounding quotes"
    fi
    printf '%s' "${input:-$current}"
}

write_compose_env() {
    # Compose parses its interpolation env file before it processes service
    # env_file entries.  Keep this file limited to non-secret settings so a
    # literal '$' in .env cannot be mistaken for a Compose variable.  The
    # services still receive the complete .env through their raw env_file.
    : > "$COMPOSE_TMP"
    local key default value
    while IFS='|' read -r key default; do
        value="$(value_from_tmp "$key" "$default")"
        printf '%s=%s\n' "$key" "$value" >> "$COMPOSE_TMP"
    done <<'SETTINGS'
POSTGRES_DB|lms_database
POSTGRES_USER|lms_user
POSTGRES_HOST|postgres
POSTGRES_PORT|5432
DJANGO_DEBUG|False
DJANGO_ALLOWED_HOSTS|*
NGINX_HTTP_PORT|80
NGINX_HTTPS_PORT|443
NGINX_TLS_CERT_DIR|./certs
LMS_RUNTIME_DIR|./runtime
DOZZLE_DATA_DIR|./runtime/dozzle
REDIS_URL|redis://redis:6379/0
REDIS_CACHE_URL|redis://redis:6379/1
CELERY_BROKER_URL|redis://redis:6379/0
CELERY_RESULT_BACKEND|redis://redis:6379/0
CELERY_CONCURRENCY|2
WEB_CONCURRENCY|2
WEB_THREADS|8
GUNICORN_TIMEOUT|1800
WEB_PUSH_ENABLED|false
WEB_PUSH_VAPID_PUBLIC_KEY|
WEB_PUSH_VAPID_SUBJECT|
WEB_PUSH_TIMEOUT_SECONDS|15
WEB_PUSH_BATCH_SIZE|100
WEBAUTHN_ENABLED|false
WEBAUTHN_RP_ID|localhost
WEBAUTHN_RP_NAME|Bible Institute Coptic
WEBAUTHN_EXPECTED_ORIGIN|http://localhost:8000
WEBAUTHN_CHALLENGE_TTL_SECONDS|300
PWA_MEDIA_MAX_BYTES|0
SETTINGS
    chmod 600 "$COMPOSE_TMP"
    mv "$COMPOSE_TMP" .compose.env
}

gen_secret() {
    if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
    else dd if=/dev/urandom bs=48 count=1 2>/dev/null | base64 | tr -d '\n='
    fi
}

normalize_bool() {
    case "${1,,}" in
        true|1|yes|on) printf 'true' ;;
        false|0|no|off) printf 'false' ;;
        *) die "Expected true or false, got: $1" ;;
    esac
}

normalize_positive_int() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]] || die "Expected a positive integer, got: $1"
    printf '%s' "$1"
}

normalize_nonnegative_int() {
    [[ "$1" =~ ^[0-9]+$ ]] || die "Expected a non-negative integer, got: $1"
    printf '%s' "$1"
}

generate_vapid_pair() {
    # Print PUBLIC=base64url and PRIVATE=base64url without logging the private
    # value. OpenSSL generates the EC key; the small stdlib parser only encodes
    # the P-256 scalar and uncompressed public point as VAPID expects.
    local key_file python_bin
    command -v openssl >/dev/null 2>&1 || die "openssl is required to generate VAPID keys"
    if command -v python3 >/dev/null 2>&1; then
        python_bin=python3
    elif command -v python >/dev/null 2>&1; then
        python_bin=python
    else
        die "python3 is required to encode generated VAPID keys"
    fi
    key_file="$(mktemp "${TMPDIR:-/tmp}/vapid.XXXXXX")"
    openssl ecparam -name prime256v1 -genkey -noout -out "$key_file" 2>/dev/null
    "$python_bin" - "$key_file" <<'PY'
import base64
import re
import subprocess
import sys

key_file = sys.argv[1]
text = subprocess.check_output(
    ["openssl", "ec", "-in", key_file, "-text", "-noout"],
    text=True,
    stderr=subprocess.DEVNULL,
)
private_part = text.split("priv:", 1)[1].split("pub:", 1)[0]
public_part = text.split("pub:", 1)[1]
if "ASN1 OID:" in public_part:
    public_part = public_part.split("ASN1 OID:", 1)[0]
private_hex = "".join(re.findall(r"[0-9a-fA-F]{2}", private_part)).lower().rjust(64, "0")
public_hex = "".join(re.findall(r"[0-9a-fA-F]{2}", public_part)).lower()
if len(private_hex) != 64 or len(public_hex) != 130 or not public_hex.startswith("04"):
    raise SystemExit("Could not parse generated VAPID key")
encode = lambda value: base64.urlsafe_b64encode(
    bytes.fromhex(value)
).rstrip(b"=").decode()
print("PUBLIC=" + encode(public_hex))
print("PRIVATE=" + encode(private_hex))
PY
    rm -f "$key_file"
}


# -- Preflight ----------------------------------------------------------
[[ $# -eq 0 ]] || { case "$1" in -h|--help) usage ;; *) die "Unknown: $1" ;; esac; }
[[ -f compose.infrastructure.yml ]] || die "Run from repo root"

# -- Base file ----------------------------------------------------------
echo ""
echo "Bible Institute LMS - .env Setup"
echo ""
if [[ -f .env ]]; then
    cp .env "$TMP"; info "Updating existing .env"
    HAS_EXISTING_ENV=true
else
    : > "$TMP"; info "Creating minimal .env"
    HAS_EXISTING_ENV=false
fi

# -- 1. Domain ----------------------------------------------------------
if $HAS_EXISTING_ENV; then
    raw="$(read_value DJANGO_SITE_DOMAIN "Primary domain (e.g. bibleinstitute-eg.org)" "")"
else
    read -p "Primary domain (e.g. bibleinstitute-eg.org): " raw
fi
domain="${raw#https://}"; domain="${domain#http://}"; domain="${domain%%/*}"
[[ -n "$domain" ]] || die "Domain cannot be empty"
[[ "$domain" != *[[:space:]]* ]] || die "Domain cannot contain spaces"
write_value DJANGO_ALLOWED_HOSTS "$domain,.$domain"
write_value DJANGO_CSRF_TRUSTED_ORIGINS "https://$domain,https://*.$domain"
write_value DJANGO_TRUSTED_PROXY_IPS "${LMS_NETWORK_SUBNET:-172.31.240.0/24}"
write_value DJANGO_SITE_DOMAIN "https://$domain"
write_value MONITORING_DOMAIN "monitor.$domain"
write_value DEPLOY_HEALTHCHECK_URL "https://${domain}/"
echo ""

# -- 1b. PWA, Web Push and WebAuthn ------------------------------------
# These values are written to .env. Non-secret values and the public VAPID key
# are also copied to .compose.env by write_compose_env below. The VAPID private
# key remains only in .env and is never printed.
echo "PWA / Web Push / WebAuthn"
echo ""

rp_id="$(read_value WEBAUTHN_RP_ID \
    "WebAuthn RP ID (hostname only; normally the exact site hostname)" \
    "$domain")"
[[ "$rp_id" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]] \
    || die "WEBAUTHN_RP_ID must be a hostname without scheme, path or port"
write_value WEBAUTHN_RP_ID "$rp_id"

expected_origin="$(read_value WEBAUTHN_EXPECTED_ORIGIN \
    "Exact HTTPS origin (no trailing slash)" \
    "https://$domain")"
[[ "$expected_origin" == https://* ]] || die "WEBAUTHN_EXPECTED_ORIGIN must start with https://"
[[ "$expected_origin" != */ ]] || die "WEBAUTHN_EXPECTED_ORIGIN must not end with /"
origin_host="${expected_origin#https://}"
origin_host="${origin_host%%/*}"
origin_host="${origin_host%%:*}"
[[ "$origin_host" == "$rp_id" || "$origin_host" == *."$rp_id" ]] \
    || die "WEBAUTHN_RP_ID must equal or be a parent of the expected origin host"
write_value WEBAUTHN_EXPECTED_ORIGIN "$expected_origin"

write_value WEBAUTHN_RP_NAME "$(read_value WEBAUTHN_RP_NAME \
    "Passkey display name" "Bible Institute Coptic")"

vapid_subject="$(read_value WEB_PUSH_VAPID_SUBJECT \
    "VAPID contact (mailto: or https:)" "https://$domain")"
[[ "$vapid_subject" == mailto:* || "$vapid_subject" == https://* ]] \
    || die "WEB_PUSH_VAPID_SUBJECT must start with mailto: or https://"
write_value WEB_PUSH_VAPID_SUBJECT "$vapid_subject"

write_value WEB_PUSH_ENABLED "$(normalize_bool "$(read_value WEB_PUSH_ENABLED \
    "Enable Web Push? (true/false)" "true")")"
write_value WEBAUTHN_ENABLED "$(normalize_bool "$(read_value WEBAUTHN_ENABLED \
    "Enable passkeys/WebAuthn? (true/false)" "true")")"
write_value WEB_PUSH_TIMEOUT_SECONDS "$(normalize_positive_int "$(read_value WEB_PUSH_TIMEOUT_SECONDS \
    "Web Push timeout in seconds" "15")")"
write_value WEB_PUSH_BATCH_SIZE "$(normalize_positive_int "$(read_value WEB_PUSH_BATCH_SIZE \
    "Web Push batch size" "100")")"
write_value WEBAUTHN_CHALLENGE_TTL_SECONDS "$(normalize_positive_int "$(read_value WEBAUTHN_CHALLENGE_TTL_SECONDS \
    "WebAuthn challenge lifetime in seconds" "300")")"
write_value PWA_MEDIA_MAX_BYTES "$(normalize_nonnegative_int "$(read_value PWA_MEDIA_MAX_BYTES \
    "Offline media max bytes (0 = no app cap)" "0")")"

# Preserve an existing pair. If either half is missing, generate a fresh pair;
# existing browser subscriptions then need to be re-enabled.
vapid_public="$(value_from_tmp WEB_PUSH_VAPID_PUBLIC_KEY "")"
vapid_private="$(value_from_tmp WEB_PUSH_VAPID_PRIVATE_KEY "")"
if [[ -z "$vapid_public" || -z "$vapid_private" ]]; then
    info "Generating a new VAPID key pair (private key is not displayed)"
    vapid_output="$(generate_vapid_pair)" || die "VAPID key generation failed"
    vapid_public="$(printf '%s\n' "$vapid_output" | sed -n 's/^PUBLIC=//p')"
    vapid_private="$(printf '%s\n' "$vapid_output" | sed -n 's/^PRIVATE=//p')"
    [[ -n "$vapid_public" && -n "$vapid_private" ]] || die "VAPID key generation returned empty keys"
    write_value WEB_PUSH_VAPID_PUBLIC_KEY "$vapid_public"
    write_value WEB_PUSH_VAPID_PRIVATE_KEY "$vapid_private"
else
    info "Keeping existing VAPID key pair; delete both key lines to rotate"
fi
echo ""


# -- 2. PostgreSQL ------------------------------------------------------
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
write_value REDIS_CACHE_URL redis://redis:6379/1
write_value CELERY_BROKER_URL redis://redis:6379/0
write_value CELERY_RESULT_BACKEND redis://redis:6379/0
write_value CELERY_CONCURRENCY 2
echo ""

# -- 3. Django Secret Key -----------------------------------------------
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

# -- 4. TLS directory ---------------------------------------------------
tls_dir="$(read_value NGINX_TLS_CERT_DIR "TLS certificate directory" ./certs)"
write_value NGINX_TLS_CERT_DIR "$tls_dir"
echo ""

# -- 5. Telegram (optional infrastructure) ------------------------------
# The Telegram bot token and webhook secret are dashboard/database-managed
# and encrypted; this builder never prompts for, generates, or writes them.
# The encryption key is hidden and never printed; blank preserves Django's
# documented SECRET_KEY fallback and must not be auto-generated on rerun.
enc_key="$(value_from_tmp TELEGRAM_ENCRYPTION_KEY "")"
if [[ -n "$enc_key" ]]; then
    echo -n "Telegram encryption key [press Enter to keep existing]: " >&2
    read -s enc_input; echo >&2
    enc="${enc_input:-$enc_key}"
else
    echo -n "Telegram encryption key (optional; blank = use Django SECRET_KEY; at least 16 characters if set): " >&2
    read -s enc_input; echo >&2
    enc="$enc_input"
fi
if [[ -n "$enc" && ${#enc} -lt 16 ]]; then
    die "Telegram encryption key must be at least 16 characters when set"
fi
write_value TELEGRAM_ENCRYPTION_KEY "$enc"
# Dedicated private Telegram bucket; never the academic media bucket.
# Blank disables Telegram media storage (no fallback to R2_BUCKET_NAME).
write_value TELEGRAM_R2_BUCKET_NAME "$(read_value TELEGRAM_R2_BUCKET_NAME "Dedicated private Telegram media bucket (blank = disabled)" "")"
echo ""

# -- 6. Media automation API -------------------------------------------
# The API is disabled when either value is blank. The key is server-only and
# is never printed; the client sends it later as the X-Key request header.
automation_key="$(read_secret_value AUTOMATION_API_KEY "Automation API key (blank = disabled; at least 32 characters if set)")"
if [[ -n "$automation_key" && ${#automation_key} -lt 32 ]]; then
    die "AUTOMATION_API_KEY must be at least 32 characters when set"
fi
write_value AUTOMATION_API_KEY "$automation_key"

if [[ -n "$automation_key" ]]; then
    for required_setting in R2_ENDPOINT_URL R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET_NAME; do
        [[ -n "$(value_from_tmp "$required_setting" "")" ]] \
            || die "$required_setting must be configured before enabling media automation"
    done
fi
echo ""

# -- 7. Mobile push and media Worker ------------------------------------
# Expo access tokens and Worker shared secrets are server-only. They are
# hidden during input, preserved on Enter, and never copied to .compose.env.
write_value EXPO_PUSH_ACCESS_TOKEN "$(read_secret_value EXPO_PUSH_ACCESS_TOKEN "Expo Push access token (blank = disabled)")"
write_value EXPO_PUSH_SEND_URL "$(value_from_tmp EXPO_PUSH_SEND_URL "https://exp.host/--/api/v2/push/send")"
write_value EXPO_PUSH_RECEIPTS_URL "$(value_from_tmp EXPO_PUSH_RECEIPTS_URL "https://exp.host/--/api/v2/push/getReceipts")"
write_value EXPO_PUSH_TIMEOUT_SECONDS "$(value_from_tmp EXPO_PUSH_TIMEOUT_SECONDS "15")"
write_value EXPO_PUSH_REQUESTS_PER_SECOND "$(value_from_tmp EXPO_PUSH_REQUESTS_PER_SECOND "5")"

cloud_worker="$(read_value CLOUD_WORKER "Cloudflare Worker public HTTPS URL (blank = direct/local media)" "")"
[[ -z "$cloud_worker" || "$cloud_worker" == https://* ]] || die "CLOUD_WORKER must be an HTTPS URL when set"
write_value CLOUD_WORKER "$cloud_worker"
write_value WORKER_HMAC_SECRET "$(read_secret_value WORKER_HMAC_SECRET "Worker HMAC secret (blank = disabled)")"
write_value WORKER_RECEIPT_SECRET "$(read_secret_value WORKER_RECEIPT_SECRET "Worker receipt secret (blank = disabled)")"
echo ""

# -- Production defaults ------------------------------------------------
write_value DJANGO_DEBUG False
write_value NGINX_HTTP_PORT 80
write_value NGINX_HTTPS_PORT 443
write_value GUNICORN_TIMEOUT 1800
write_value WEB_CONCURRENCY 2
write_value WEB_THREADS 8
write_value DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE 2147483648
write_value DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE 10485760

# -- Atomic write -------------------------------------------------------
chmod 600 "$TMP"
write_compose_env
mv "$TMP" .env

info ".env and .compose.env written with mode 600"
echo ""
echo "  Next steps:"
echo "    1. Provision TLS certificates at: $tls_dir"
echo "    2. Run: bash deploy/setup_infrastructure.sh"
echo "    3. Run: bash deploy/setup_monitoring.sh"
echo "    4. Run: bash deploy/blue_green_deploy.sh"
echo ""
echo "  Rerun this script at any time to update settings."
