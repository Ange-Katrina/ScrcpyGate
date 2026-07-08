#!/bin/sh
set -eu

cd "$(dirname "$0")"

if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  echo "ERROR: docker compose is not available." >&2
  exit 1
fi

if [ -n "${PUBLIC_BASE_URL:-}" ]; then
  base_no_scheme="${PUBLIC_BASE_URL#*://}"
  public_host="${base_no_scheme%%/*}"
  if [ -n "$public_host" ]; then
    export ALLOWED_HOSTS="${ALLOWED_HOSTS:-127.0.0.1,localhost,$public_host}"
  fi
  export ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-$PUBLIC_BASE_URL}"
  case "$PUBLIC_BASE_URL" in
    https://*) export SESSION_COOKIE_SECURE="${SESSION_COOKIE_SECURE:-true}" ;;
  esac
fi

export WEB_SCRCPY_BIND="${WEB_SCRCPY_BIND:-127.0.0.1}"
export WEB_SCRCPY_PORT="${WEB_SCRCPY_PORT:-5000}"
export WEB_SCRCPY_DATA_HOST="${WEB_SCRCPY_DATA_HOST:-./data}"
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:${WEB_SCRCPY_PORT}}"
if [ -z "${ALLOWED_HOSTS:-}" ]; then
  case "$WEB_SCRCPY_BIND" in
    ""|"127.0.0.1"|"localhost"|"0.0.0.0"|"::"|"::1") export ALLOWED_HOSTS="127.0.0.1,localhost" ;;
    *) export ALLOWED_HOSTS="127.0.0.1,localhost,$WEB_SCRCPY_BIND" ;;
  esac
else
  case "$WEB_SCRCPY_BIND" in
    ""|"127.0.0.1"|"localhost"|"0.0.0.0"|"::"|"::1") ;;
    *)
      case ",$ALLOWED_HOSTS," in
        *",$WEB_SCRCPY_BIND,"*) ;;
        *) export ALLOWED_HOSTS="$ALLOWED_HOSTS,$WEB_SCRCPY_BIND" ;;
      esac
      ;;
  esac
fi
export ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-$PUBLIC_BASE_URL}"
export TRUST_PROXY="${TRUST_PROXY:-false}"
export TRUSTED_PROXY_IPS="${TRUSTED_PROXY_IPS:-127.0.0.1,::1}"
export SESSION_COOKIE_SECURE="${SESSION_COOKIE_SECURE:-false}"

mkdir -p "$WEB_SCRCPY_DATA_HOST"

echo "ScrcpyGate public URL: $PUBLIC_BASE_URL"
echo "Allowed hosts: $ALLOWED_HOSTS"
echo "Allowed origins: $ALLOWED_ORIGINS"
echo "Data directory: $WEB_SCRCPY_DATA_HOST"
echo "Service bind: ${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT}"
case "$WEB_SCRCPY_BIND" in
  ""|"0.0.0.0"|"::") health_host="127.0.0.1" ;;
  *) health_host="$WEB_SCRCPY_BIND" ;;
esac
echo "SafeLine upstream can use: http://${health_host}:${WEB_SCRCPY_PORT}"

$DC -f docker-compose.v2.yml build

if command -v docker >/dev/null 2>&1; then
  app_uid="$(docker run --rm --entrypoint id web-scrcpy-v2:local -u 2>/dev/null || true)"
  app_gid="$(docker run --rm --entrypoint id web-scrcpy-v2:local -g 2>/dev/null || true)"
  if [ -n "$app_uid" ] && [ -n "$app_gid" ]; then
    chown -R "$app_uid:$app_gid" "$WEB_SCRCPY_DATA_HOST" 2>/dev/null || true
    chmod 700 "$WEB_SCRCPY_DATA_HOST" 2>/dev/null || true
  fi
fi

password="$($DC -f docker-compose.v2.yml run --rm --no-deps -e INITIAL_ADMIN_PASSWORD web-scrcpy-v2 python -m app.cli bootstrap-admin 2>/dev/null)" || {
  echo "ERROR: failed to initialize admin account." >&2
  exit 1
}
password="$(printf '%s' "$password" | tr -d '\r' | tail -n 1)"

$DC -f docker-compose.v2.yml up -d

printf 'Waiting for healthz'
i=0
while [ "$i" -lt 40 ]; do
  if command -v curl >/dev/null 2>&1 && curl -fsS "http://${health_host}:${WEB_SCRCPY_PORT}/healthz" >/dev/null 2>&1; then
    echo " ok"
    break
  fi
  if [ "$i" -eq 39 ]; then
    echo " timeout"
    echo "Container status:"
    $DC -f docker-compose.v2.yml ps || true
    echo "Recent container logs:"
    docker logs --tail=120 web-scrcpy-v2 2>/dev/null || true
  else
    printf '.'
    sleep 1
  fi
  i=$((i + 1))
done

if [ -n "$password" ]; then
  echo ""
  echo "Initial admin account:"
  echo "  username: admin"
  echo "  password: $password"
  echo ""
  echo "This password is shown once and is not saved in plaintext."
else
  echo ""
  echo "No new initial password was generated. Existing users were detected or the old users.json was migrated."
  echo "If you need a reset, run inside this directory:"
  echo "  $DC -f docker-compose.v2.yml exec -T web-scrcpy-v2 python -m app.cli reset-admin"
fi
