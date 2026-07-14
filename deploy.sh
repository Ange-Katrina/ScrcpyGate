#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

log() {
  printf '%s\n' "$*"
}

warn() {
  printf 'WARNING: %s\n' "$*" >&2
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
ScrcpyGate installer and deployment helper

Usage:
  ./deploy.sh [--pull | --skip-build]
  ./deploy.sh --help

Options:
  --pull        Refresh base images while building.
  --skip-build  Reuse the existing scrcpygate:local image.
  -h, --help    Show this help message.

Configuration:
  The installer creates .env from .env.example when it is missing.
  Existing .env files, databases, users, and passwords are never overwritten.
EOF
}

skip_build=false
pull_images=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-build) skip_build=true ;;
    --pull) pull_images=true ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    *) die "unknown option: $1 (use --help)" ;;
  esac
  shift
done
[ "$#" -eq 0 ] || die "unexpected argument: $1"
if [ "$skip_build" = true ] && [ "$pull_images" = true ]; then
  die "--pull and --skip-build cannot be used together"
fi

if [ ! -f .env ]; then
  [ -f .env.example ] || die ".env.example is missing"
  cp .env.example .env
  chmod 600 .env 2>/dev/null || warn "could not restrict .env permissions"
  log "Created .env from .env.example."
fi

dotenv_value() {
  wanted=$1
  awk -v wanted="$wanted" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    {
      line = $0
      sub(/\r$/, "", line)
      separator = index(line, "=")
      if (!separator) next
      name = substr(line, 1, separator - 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name != wanted) next
      value = substr(line, separator + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (length(value) >= 2) {
        first = substr(value, 1, 1)
        last = substr(value, length(value), 1)
        if ((first == "\"" && last == "\"") || (first == "\047" && last == "\047")) {
          value = substr(value, 2, length(value) - 2)
        }
      }
      result = value
      found = 1
    }
    END { if (found) print result }
  ' .env
}

WEB_SCRCPY_BIND=${WEB_SCRCPY_BIND:-$(dotenv_value WEB_SCRCPY_BIND)}
WEB_SCRCPY_PORT=${WEB_SCRCPY_PORT:-$(dotenv_value WEB_SCRCPY_PORT)}
WEB_SCRCPY_DATA_HOST=${WEB_SCRCPY_DATA_HOST:-$(dotenv_value WEB_SCRCPY_DATA_HOST)}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-$(dotenv_value PUBLIC_BASE_URL)}
ALLOWED_HOSTS=${ALLOWED_HOSTS:-$(dotenv_value ALLOWED_HOSTS)}
ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-$(dotenv_value ALLOWED_ORIGINS)}
TRUST_PROXY=${TRUST_PROXY:-$(dotenv_value TRUST_PROXY)}
TRUSTED_PROXY_IPS=${TRUSTED_PROXY_IPS:-$(dotenv_value TRUSTED_PROXY_IPS)}
SESSION_COOKIE_SECURE=${SESSION_COOKIE_SECURE:-$(dotenv_value SESSION_COOKIE_SECURE)}
SCRCPYGATE_HEALTH_TIMEOUT=${SCRCPYGATE_HEALTH_TIMEOUT:-$(dotenv_value SCRCPYGATE_HEALTH_TIMEOUT)}
INITIAL_ADMIN_PASSWORD=${INITIAL_ADMIN_PASSWORD:-$(dotenv_value INITIAL_ADMIN_PASSWORD)}

WEB_SCRCPY_BIND=${WEB_SCRCPY_BIND:-127.0.0.1}
WEB_SCRCPY_PORT=${WEB_SCRCPY_PORT:-5000}
WEB_SCRCPY_DATA_HOST=${WEB_SCRCPY_DATA_HOST:-./data}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-http://127.0.0.1:${WEB_SCRCPY_PORT}}
ALLOWED_HOSTS=${ALLOWED_HOSTS:-127.0.0.1,localhost}
ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-$PUBLIC_BASE_URL}
TRUST_PROXY=${TRUST_PROXY:-false}
TRUSTED_PROXY_IPS=${TRUSTED_PROXY_IPS:-127.0.0.1,::1}
SESSION_COOKIE_SECURE=${SESSION_COOKIE_SECURE:-false}
SCRCPYGATE_HEALTH_TIMEOUT=${SCRCPYGATE_HEALTH_TIMEOUT:-90}

case "$WEB_SCRCPY_PORT" in
  ''|*[!0-9]*) die "WEB_SCRCPY_PORT must be an integer" ;;
esac
if [ "$WEB_SCRCPY_PORT" -lt 1 ] || [ "$WEB_SCRCPY_PORT" -gt 65535 ]; then
  die "WEB_SCRCPY_PORT must be between 1 and 65535"
fi
case "$SCRCPYGATE_HEALTH_TIMEOUT" in
  ''|*[!0-9]*) die "SCRCPYGATE_HEALTH_TIMEOUT must be an integer" ;;
esac
if [ "$SCRCPYGATE_HEALTH_TIMEOUT" -lt 10 ] || [ "$SCRCPYGATE_HEALTH_TIMEOUT" -gt 600 ]; then
  die "SCRCPYGATE_HEALTH_TIMEOUT must be between 10 and 600 seconds"
fi
case "$PUBLIC_BASE_URL" in
  https://*)
    if [ "$SESSION_COOKIE_SECURE" != true ]; then
      warn "HTTPS public URL requires secure session cookies; enabling SESSION_COOKIE_SECURE=true"
      SESSION_COOKIE_SECURE=true
    fi
    ;;
  http://*) ;;
  *) die "PUBLIC_BASE_URL must start with http:// or https://" ;;
esac
case "$WEB_SCRCPY_DATA_HOST" in
  ''|'/'|'.'|'./') die "WEB_SCRCPY_DATA_HOST must point to a dedicated data directory" ;;
esac

export WEB_SCRCPY_BIND WEB_SCRCPY_PORT WEB_SCRCPY_DATA_HOST
export PUBLIC_BASE_URL ALLOWED_HOSTS ALLOWED_ORIGINS
export TRUST_PROXY TRUSTED_PROXY_IPS SESSION_COOKIE_SECURE

command -v docker >/dev/null 2>&1 || die "Docker is not installed or not available in PATH"
if ! docker info >/dev/null 2>&1; then
  die "cannot connect to the Docker daemon; start Docker or grant this user Docker access"
fi

if docker compose version >/dev/null 2>&1; then
  compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
  warn "using legacy docker-compose; install the Docker Compose plugin when possible"
  compose() { docker-compose "$@"; }
else
  die "Docker Compose is not installed"
fi

compose config >/dev/null || die "compose.yaml or .env contains an invalid configuration"

data_created=false
if [ ! -d "$WEB_SCRCPY_DATA_HOST" ]; then
  mkdir -p "$WEB_SCRCPY_DATA_HOST" || die "could not create data directory: $WEB_SCRCPY_DATA_HOST"
  data_created=true
fi
DATA_DIR=$(CDPATH= cd -- "$WEB_SCRCPY_DATA_HOST" && pwd -P)
case "$DATA_DIR" in
  '/'|"$SCRIPT_DIR") die "refusing to use unsafe data directory: $DATA_DIR" ;;
esac
if [ "$data_created" = true ]; then
  chmod 700 "$DATA_DIR" 2>/dev/null || warn "could not restrict data directory permissions"
fi

log "ScrcpyGate installer"
log "  Public URL:     $PUBLIC_BASE_URL"
log "  Service bind:  ${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT}"
log "  Data directory: $DATA_DIR"

if [ "$skip_build" = true ]; then
  docker image inspect scrcpygate:local >/dev/null 2>&1 || die "scrcpygate:local does not exist; run without --skip-build"
  log "Reusing image scrcpygate:local."
elif [ "$pull_images" = true ]; then
  log "Building ScrcpyGate and refreshing base images..."
  compose build --pull
else
  log "Building ScrcpyGate..."
  compose build
fi

app_uid=$(docker run --rm --entrypoint id scrcpygate:local -u 2>/dev/null) || die "could not read the image application UID"
app_gid=$(docker run --rm --entrypoint id scrcpygate:local -g 2>/dev/null) || die "could not read the image application GID"
if ! docker run --rm -v "$DATA_DIR:/app/data" --entrypoint sh scrcpygate:local -c 'test -w /app/data' >/dev/null 2>&1; then
  log "Adjusting data directory ownership for container UID ${app_uid}:${app_gid}..."
  docker run --rm --user 0 -v "$DATA_DIR:/app/data" --entrypoint chown scrcpygate:local -R "${app_uid}:${app_gid}" /app/data \
    || die "could not make the data directory writable by the container"
fi

log "Initializing the administrator account..."
if [ -n "$INITIAL_ADMIN_PASSWORD" ]; then
  export INITIAL_ADMIN_PASSWORD
  bootstrap_output=$(compose run --rm --no-deps -e INITIAL_ADMIN_PASSWORD scrcpygate python -m app.cli bootstrap-admin) \
    || die "failed to initialize the administrator account"
else
  bootstrap_output=$(compose run --rm --no-deps scrcpygate python -m app.cli bootstrap-admin) \
    || die "failed to initialize the administrator account"
fi
unset INITIAL_ADMIN_PASSWORD
password=$(printf '%s\n' "$bootstrap_output" | tr -d '\r' | awk 'NF { line=$0 } END { print line }')

if [ -n "$password" ]; then
  log ""
  log "Initial admin account (save this password now):"
  log "  username: admin"
  log "  password: $password"
  log ""
else
  log "Existing administrator account preserved."
fi

log "Starting ScrcpyGate..."
compose up -d

case "$WEB_SCRCPY_BIND" in
  ''|'0.0.0.0'|'::'|'::1') health_host=127.0.0.1 ;;
  *) health_host=$WEB_SCRCPY_BIND ;;
esac
case "$health_host" in
  *:*) health_url="http://[${health_host}]:${WEB_SCRCPY_PORT}/healthz" ;;
  *) health_url="http://${health_host}:${WEB_SCRCPY_PORT}/healthz" ;;
esac

container_health() {
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' scrcpygate 2>/dev/null || true
}

http_health_ok() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 3 "$health_url" >/dev/null 2>&1 && return 0
  elif command -v wget >/dev/null 2>&1; then
    wget -q -T 3 -O /dev/null "$health_url" >/dev/null 2>&1 && return 0
  fi
  [ "$(container_health)" = healthy ]
}

show_diagnostics() {
  log "Container status:"
  compose ps || true
  log "Recent container logs:"
  docker logs --tail=120 scrcpygate 2>/dev/null || true
}

printf 'Waiting for health check'
started_at=$(date +%s)
healthy=false
while :; do
  if http_health_ok; then
    printf ' ok\n'
    healthy=true
    break
  fi
  state=$(docker inspect --format '{{.State.Status}}' scrcpygate 2>/dev/null || true)
  case "$state" in
    exited|dead)
      printf ' failed\n'
      show_diagnostics
      die "ScrcpyGate container stopped during startup"
      ;;
  esac
  now=$(date +%s)
  elapsed=$((now - started_at))
  if [ "$elapsed" -ge "$SCRCPYGATE_HEALTH_TIMEOUT" ]; then
    break
  fi
  sleep 1
  printf '.'
done
if [ "$healthy" != true ]; then
  printf ' timeout\n'
  show_diagnostics
  die "ScrcpyGate did not become healthy within ${SCRCPYGATE_HEALTH_TIMEOUT} seconds"
fi

log ""
log "ScrcpyGate installation completed."
log "  Open: $PUBLIC_BASE_URL"
log "  Status: docker compose ps"
log "  Logs: docker logs --tail=120 scrcpygate"
log "  Reset admin: docker compose exec -T scrcpygate python -m app.cli reset-admin"
