#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

C_RESET=""
C_BOLD=""
C_DIM=""
C_RED=""
C_GREEN=""
C_YELLOW=""
C_CYAN=""
C_GRAY=""

setup_colors() {
  use_color=0
  case "${SCRCPYGATE_COLOR:-auto}" in
    always|yes|1) use_color=1 ;;
    never|no|0) ;;
    *)
      if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != dumb ]; then
        use_color=1
      fi
      ;;
  esac
  [ "$use_color" = 1 ] || return 0
  C_RESET=$(printf '\033[0m')
  C_BOLD=$(printf '\033[1m')
  C_DIM=$(printf '\033[2m')
  C_RED=$(printf '\033[31m')
  C_GREEN=$(printf '\033[32m')
  C_YELLOW=$(printf '\033[33m')
  C_CYAN=$(printf '\033[36m')
  C_GRAY=$(printf '\033[90m')
}

setup_colors

log() {
  printf '%s\n' "$*"
}

success_msg() {
  printf '%s[OK]%s %s\n' "$C_GREEN" "$C_RESET" "$*"
}

warn_msg() {
  printf '%s[WARN]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2
}

error_msg() {
  printf '%s[ERR]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2
}

die() {
  error_msg "$*"
  exit 1
}

print_rule() {
  printf '%s--------------------------------------------------%s\n' "$C_GRAY" "$C_RESET"
}

panel_top() {
  printf '\n%s%s%s\n' "${C_BOLD}${C_CYAN}" "$1" "$C_RESET"
  print_rule
}

panel_line() {
  printf '  %s%s%s : %s\n' "$C_DIM" "$1" "$C_RESET" "$2"
}

menu_group() {
  printf '\n%s%s%s\n' "$C_BOLD" "$1" "$C_RESET"
}

menu_item() {
  number=$1
  text=$2
  color=${3:-}
  printf '  %s%2s)%s %s%s%s\n' "$C_GRAY" "$number" "$C_RESET" "$color" "$text" "$C_RESET"
}

is_interactive() {
  [ "${SCRCPYGATE_FORCE_INTERACTIVE:-0}" = 1 ] || { [ -t 0 ] && [ -t 1 ]; }
}

require_interactive() {
  is_interactive || die "此操作需要交互式终端 / this action requires an interactive terminal"
}

pause_menu() {
  is_interactive || return 0
  printf '\n%s>%s 按 Enter 继续 / Press Enter to continue...' "$C_YELLOW" "$C_RESET"
  IFS= read -r _pause || true
}

usage() {
  cat <<'EOF'
ScrcpyGate 引导式安装与管理脚本

直接运行:
  ./deploy.sh               交互式终端进入管理菜单；非交互环境自动安装

安装与配置:
  ./deploy.sh --menu        打开管理菜单
  ./deploy.sh --configure   打开部署配置向导
  ./deploy.sh --install     使用当前配置安装或更新
  ./deploy.sh --pull        更新基础镜像后安装或更新
  ./deploy.sh --skip-build  复用已有 scrcpygate:local 镜像

服务管理:
  ./deploy.sh --start | --stop | --restart
  ./deploy.sh --status | --logs | --reset-admin

其他:
  ./deploy.sh --help

Existing .env files, databases, users, and passwords are never overwritten.
EOF
}

ACTION=auto
skip_build=false
pull_images=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --menu) ACTION=menu ;;
    --configure) ACTION=configure ;;
    --install) ACTION=install ;;
    --pull) ACTION=install; pull_images=true ;;
    --skip-build) ACTION=install; skip_build=true ;;
    --start) ACTION=start ;;
    --stop) ACTION=stop ;;
    --restart) ACTION=restart ;;
    --status) ACTION=status ;;
    --logs) ACTION=logs ;;
    --reset-admin) ACTION=reset_admin ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    *) die "未知参数 / unknown option: $1 (use --help)" ;;
  esac
  shift
done
[ "$#" -eq 0 ] || die "多余参数 / unexpected argument: $1"
if [ "$skip_build" = true ] && [ "$pull_images" = true ]; then
  die "--pull and --skip-build cannot be used together"
fi

ensure_env_file() {
  if [ ! -f .env ]; then
    [ -f .env.example ] || die ".env.example is missing"
    cp .env.example .env
    chmod 600 .env 2>/dev/null || warn_msg "无法限制 .env 权限 / could not restrict .env permissions"
    success_msg "已从 .env.example 创建 .env"
  fi
}

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

set_env_value() {
  key=$1
  value=$2
  tmp_file=".env.tmp.$$"
  awk -v wanted="$key" -v replacement="$value" '
    BEGIN { updated = 0 }
    {
      line = $0
      probe = line
      sub(/\r$/, "", probe)
      separator = index(probe, "=")
      if (separator) {
        name = substr(probe, 1, separator - 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
        if (name == wanted) {
          if (!updated) print wanted "=" replacement
          updated = 1
          next
        }
      }
      print line
    }
    END { if (!updated) print wanted "=" replacement }
  ' .env > "$tmp_file" || { rm -f "$tmp_file"; die "无法更新 .env / failed to update .env"; }
  mv "$tmp_file" .env
}

load_settings() {
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
}

validate_settings() {
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
        warn_msg "HTTPS 地址需要安全 Cookie，已临时启用 SESSION_COOKIE_SECURE=true"
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
}

prompt_value() {
  label=$1
  default_value=$2
  printf '\n%s>%s %s [%s]: ' "$C_YELLOW" "$C_RESET" "$label" "$default_value"
  IFS= read -r answer || return 1
  answer=$(printf '%s' "$answer" | tr -d '\r')
  PROMPT_RESULT=${answer:-$default_value}
}

prompt_confirm() {
  label=$1
  printf '\n%s>%s %s [Y/n]: ' "$C_YELLOW" "$C_RESET" "$label"
  IFS= read -r answer || return 1
  answer=$(printf '%s' "$answer" | tr -d '\r')
  case "$answer" in
    ''|y|Y|yes|YES|是) return 0 ;;
    *) return 1 ;;
  esac
}

configure_wizard() {
  require_interactive
  ensure_env_file
  load_settings

  panel_top "ScrcpyGate 部署配置向导 / Configuration Wizard"
  panel_line "当前地址" "$PUBLIC_BASE_URL"
  panel_line "监听" "${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT}"
  panel_line "数据目录" "$WEB_SCRCPY_DATA_HOST"
  print_rule
  menu_item 1 "仅本机访问（默认、安全）" "$C_GREEN"
  menu_item 2 "局域网直接访问" "$C_CYAN"
  menu_item 3 "反向代理 / HTTPS" "$C_YELLOW"
  menu_item 4 "自定义现有参数" "$C_GRAY"

  default_mode=1
  [ "$WEB_SCRCPY_BIND" = 0.0.0.0 ] && default_mode=2
  [ "$TRUST_PROXY" = true ] && default_mode=3
  prompt_value "选择部署模式" "$default_mode" || return 1
  mode=$PROMPT_RESULT
  case "$mode" in 1|2|3|4) ;; *) error_msg "无效部署模式"; return 1 ;; esac

  prompt_value "服务端口" "$WEB_SCRCPY_PORT" || return 1
  new_port=$PROMPT_RESULT
  case "$new_port" in ''|*[!0-9]*) error_msg "端口必须是整数"; return 1 ;; esac
  if [ "$new_port" -lt 1 ] || [ "$new_port" -gt 65535 ]; then
    error_msg "端口必须在 1-65535 之间"
    return 1
  fi
  prompt_value "数据目录" "$WEB_SCRCPY_DATA_HOST" || return 1
  new_data=$PROMPT_RESULT
  case "$new_data" in ''|'/'|'.'|'./') error_msg "请选择独立的数据目录"; return 1 ;; esac

  new_timeout=$SCRCPYGATE_HEALTH_TIMEOUT
  new_trusted=$TRUSTED_PROXY_IPS
  case "$mode" in
    1)
      new_bind=127.0.0.1
      new_public="http://127.0.0.1:${new_port}"
      new_hosts="127.0.0.1,localhost"
      new_origins=$new_public
      new_trust=false
      new_secure=false
      ;;
    2)
      current_host=${PUBLIC_BASE_URL#*://}
      current_host=${current_host%%/*}
      current_host=${current_host%%:*}
      case "$current_host" in ''|127.0.0.1|localhost) current_host=192.168.1.10 ;; esac
      prompt_value "服务器局域网 IP 或域名（不含协议和端口）" "$current_host" || return 1
      lan_host=$PROMPT_RESULT
      new_bind=0.0.0.0
      prompt_value "用户访问地址" "http://${lan_host}:${new_port}" || return 1
      new_public=$PROMPT_RESULT
      new_hosts="127.0.0.1,localhost,${lan_host}"
      new_origins=$new_public
      new_trust=false
      new_secure=false
      ;;
    3)
      proxy_bind=$WEB_SCRCPY_BIND
      case "$proxy_bind" in 0.0.0.0|'') proxy_bind=127.0.0.1 ;; esac
      prompt_value "反向代理连接的监听地址" "$proxy_bind" || return 1
      new_bind=$PROMPT_RESULT
      prompt_value "外部访问地址（建议 HTTPS）" "$PUBLIC_BASE_URL" || return 1
      new_public=$PROMPT_RESULT
      prompt_value "可信代理 IP/CIDR，逗号分隔" "$TRUSTED_PROXY_IPS" || return 1
      new_trusted=$PROMPT_RESULT
      new_hosts="127.0.0.1,localhost"
      new_origins=$new_public
      new_trust=true
      case "$new_public" in https://*) new_secure=true ;; *) new_secure=false ;; esac
      ;;
    4)
      prompt_value "宿主机监听地址" "$WEB_SCRCPY_BIND" || return 1
      new_bind=$PROMPT_RESULT
      prompt_value "用户访问地址" "$PUBLIC_BASE_URL" || return 1
      new_public=$PROMPT_RESULT
      prompt_value "允许的 Host" "$ALLOWED_HOSTS" || return 1
      new_hosts=$PROMPT_RESULT
      prompt_value "允许的 Origin" "$ALLOWED_ORIGINS" || return 1
      new_origins=$PROMPT_RESULT
      prompt_value "是否信任代理头（true/false）" "$TRUST_PROXY" || return 1
      new_trust=$PROMPT_RESULT
      prompt_value "可信代理 IP/CIDR" "$TRUSTED_PROXY_IPS" || return 1
      new_trusted=$PROMPT_RESULT
      case "$new_public" in https://*) new_secure=true ;; *) new_secure=$SESSION_COOKIE_SECURE ;; esac
      ;;
  esac

  case "$new_public" in http://*|https://*) ;; *) error_msg "访问地址必须以 http:// 或 https:// 开头"; return 1 ;; esac

  panel_top "确认配置 / Confirm"
  panel_line "模式" "$mode"
  panel_line "监听" "${new_bind}:${new_port}"
  panel_line "访问地址" "$new_public"
  panel_line "数据目录" "$new_data"
  panel_line "信任代理" "$new_trust"
  panel_line "安全 Cookie" "$new_secure"
  print_rule
  prompt_confirm "保存以上配置？" || { warn_msg "已取消"; return 1; }

  set_env_value WEB_SCRCPY_BIND "$new_bind"
  set_env_value WEB_SCRCPY_PORT "$new_port"
  set_env_value WEB_SCRCPY_DATA_HOST "$new_data"
  set_env_value PUBLIC_BASE_URL "$new_public"
  set_env_value ALLOWED_HOSTS "$new_hosts"
  set_env_value ALLOWED_ORIGINS "$new_origins"
  set_env_value TRUST_PROXY "$new_trust"
  set_env_value TRUSTED_PROXY_IPS "$new_trusted"
  set_env_value SESSION_COOKIE_SECURE "$new_secure"
  set_env_value SCRCPYGATE_HEALTH_TIMEOUT "$new_timeout"
  chmod 600 .env 2>/dev/null || true
  WEB_SCRCPY_BIND=$new_bind
  WEB_SCRCPY_PORT=$new_port
  WEB_SCRCPY_DATA_HOST=$new_data
  PUBLIC_BASE_URL=$new_public
  ALLOWED_HOSTS=$new_hosts
  ALLOWED_ORIGINS=$new_origins
  TRUST_PROXY=$new_trust
  TRUSTED_PROXY_IPS=$new_trusted
  SESSION_COOKIE_SECURE=$new_secure
  success_msg "配置已保存到 $SCRIPT_DIR/.env"
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker 未安装或不在 PATH 中"
  docker info >/dev/null 2>&1 || die "无法连接 Docker；请启动 Docker 或授予当前用户访问权限"
  if docker compose version >/dev/null 2>&1; then
    compose() { docker compose "$@"; }
  elif command -v docker-compose >/dev/null 2>&1; then
    warn_msg "正在使用旧版 docker-compose，建议安装 Compose plugin"
    compose() { docker-compose "$@"; }
  else
    die "Docker Compose 未安装"
  fi
}

service_status_line() {
  if ! command -v docker >/dev/null 2>&1; then
    printf '%s[Docker 未安装]%s' "$C_RED" "$C_RESET"
    return 0
  fi
  if ! docker info >/dev/null 2>&1; then
    printf '%s[Docker 不可用]%s' "$C_RED" "$C_RESET"
    return 0
  fi
  state=$(docker inspect --format '{{.State.Status}}' scrcpygate 2>/dev/null || true)
  health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' scrcpygate 2>/dev/null || true)
  case "$state:$health" in
    running:healthy) printf '%s[运行中 / healthy]%s' "$C_GREEN" "$C_RESET" ;;
    running:*) printf '%s[启动中 / %s]%s' "$C_YELLOW" "${health:-running}" "$C_RESET" ;;
    :) printf '%s[未安装]%s' "$C_GRAY" "$C_RESET" ;;
    *) printf '%s[%s]%s' "$C_RED" "$state" "$C_RESET" ;;
  esac
}

prepare_deployment() {
  ensure_env_file
  load_settings
  validate_settings
  require_docker
  compose config >/dev/null || die "compose.yaml 或 .env 配置无效"
}

prepare_data_directory() {
  data_created=false
  if [ ! -d "$WEB_SCRCPY_DATA_HOST" ]; then
    mkdir -p "$WEB_SCRCPY_DATA_HOST" || die "无法创建数据目录: $WEB_SCRCPY_DATA_HOST"
    data_created=true
  fi
  DATA_DIR=$(CDPATH= cd -- "$WEB_SCRCPY_DATA_HOST" && pwd -P)
  case "$DATA_DIR" in '/'|"$SCRIPT_DIR") die "拒绝使用不安全的数据目录: $DATA_DIR" ;; esac
  if [ "$data_created" = true ]; then
    chmod 700 "$DATA_DIR" 2>/dev/null || warn_msg "无法限制数据目录权限"
  fi
}

build_image() {
  if [ "$skip_build" = true ]; then
    docker image inspect scrcpygate:local >/dev/null 2>&1 || die "scrcpygate:local 不存在，请不要使用 --skip-build"
    success_msg "复用镜像 scrcpygate:local"
  elif [ "$pull_images" = true ]; then
    log "正在更新基础镜像并构建 ScrcpyGate..."
    compose build --pull
  else
    log "正在构建 ScrcpyGate..."
    compose build
  fi
}

ensure_data_permissions() {
  app_uid=$(docker run --rm --entrypoint id scrcpygate:local -u 2>/dev/null) || die "无法读取容器 UID"
  app_gid=$(docker run --rm --entrypoint id scrcpygate:local -g 2>/dev/null) || die "无法读取容器 GID"
  if ! docker run --rm -v "$DATA_DIR:/app/data" --entrypoint sh scrcpygate:local -c 'test -w /app/data' >/dev/null 2>&1; then
    log "正在修复数据目录权限（容器 UID ${app_uid}:${app_gid}）..."
    docker run --rm --user 0 -v "$DATA_DIR:/app/data" --entrypoint chown scrcpygate:local -R "${app_uid}:${app_gid}" /app/data \
      || die "无法让容器写入数据目录"
  fi
}

initialize_admin() {
  log "正在初始化管理员账号..."
  if [ -n "$INITIAL_ADMIN_PASSWORD" ]; then
    export INITIAL_ADMIN_PASSWORD
    bootstrap_output=$(compose run --rm --no-deps -e INITIAL_ADMIN_PASSWORD scrcpygate python -m app.cli bootstrap-admin) \
      || die "管理员初始化失败"
  else
    bootstrap_output=$(compose run --rm --no-deps scrcpygate python -m app.cli bootstrap-admin) \
      || die "管理员初始化失败"
  fi
  unset INITIAL_ADMIN_PASSWORD
  password=$(printf '%s\n' "$bootstrap_output" | tr -d '\r' | awk 'NF { line=$0 } END { print line }')
  if [ -n "$password" ]; then
    panel_top "初始管理员账号（请立即保存）"
    panel_line "用户名" "admin"
    panel_line "密码" "$password"
    print_rule
  else
    success_msg "保留现有管理员账号"
  fi
}

health_url_for_settings() {
  case "$WEB_SCRCPY_BIND" in ''|'0.0.0.0'|'::'|'::1') health_host=127.0.0.1 ;; *) health_host=$WEB_SCRCPY_BIND ;; esac
  case "$health_host" in
    *:*) health_url="http://[${health_host}]:${WEB_SCRCPY_PORT}/healthz" ;;
    *) health_url="http://${health_host}:${WEB_SCRCPY_PORT}/healthz" ;;
  esac
}

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
  log "容器状态 / Container status:"
  compose ps || true
  log "最近日志 / Recent logs:"
  docker logs --tail=120 scrcpygate 2>/dev/null || true
}

wait_for_health() {
  health_url_for_settings
  printf '等待健康检查 / Waiting for health check'
  started_at=$(date +%s)
  healthy=false
  while :; do
    if http_health_ok; then
      printf ' %sok%s\n' "$C_GREEN" "$C_RESET"
      healthy=true
      break
    fi
    state=$(docker inspect --format '{{.State.Status}}' scrcpygate 2>/dev/null || true)
    case "$state" in
      exited|dead)
        printf ' %sfailed%s\n' "$C_RED" "$C_RESET"
        show_diagnostics
        die "ScrcpyGate 容器在启动期间停止"
        ;;
    esac
    now=$(date +%s)
    elapsed=$((now - started_at))
    [ "$elapsed" -lt "$SCRCPYGATE_HEALTH_TIMEOUT" ] || break
    sleep 1
    printf '.'
  done
  if [ "$healthy" != true ]; then
    printf ' %stimeout%s\n' "$C_RED" "$C_RESET"
    show_diagnostics
    die "服务未在 ${SCRCPYGATE_HEALTH_TIMEOUT} 秒内就绪"
  fi
}

show_install_summary() {
  panel_top "ScrcpyGate 安装信息"
  panel_line "访问地址" "$PUBLIC_BASE_URL"
  panel_line "服务监听" "${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT}"
  panel_line "数据目录" "$DATA_DIR"
  print_rule
}

install_service() {
  prepare_deployment
  prepare_data_directory
  show_install_summary
  build_image
  ensure_data_permissions
  initialize_admin
  log "正在启动 ScrcpyGate..."
  compose up -d
  wait_for_health
  success_msg "安装/更新完成：$PUBLIC_BASE_URL"
  log "  状态: docker compose ps"
  log "  日志: docker logs --tail=120 scrcpygate"
}

start_service() {
  prepare_deployment
  compose up -d
  wait_for_health
  success_msg "ScrcpyGate 已启动"
}

stop_service() {
  prepare_deployment
  compose stop
  success_msg "ScrcpyGate 已停止"
}

restart_service() {
  prepare_deployment
  compose restart
  wait_for_health
  success_msg "ScrcpyGate 已重启"
}

show_status() {
  prepare_deployment
  compose ps
}

show_logs() {
  prepare_deployment
  docker logs --tail=120 scrcpygate 2>/dev/null || warn_msg "暂无容器日志"
}

reset_admin() {
  prepare_deployment
  password=$(compose exec -T scrcpygate python -m app.cli reset-admin) || die "管理员密码重置失败"
  password=$(printf '%s\n' "$password" | tr -d '\r' | awk 'NF { line=$0 } END { print line }')
  panel_top "管理员密码已重置"
  panel_line "用户名" "admin"
  panel_line "新密码" "$password"
  print_rule
}

show_menu() {
  require_interactive
  ensure_env_file
  while :; do
    load_settings

    panel_top "ScrcpyGate 安装与管理面板"
    panel_line "状态" "$(service_status_line)"
    panel_line "访问地址" "$PUBLIC_BASE_URL"
    panel_line "监听" "${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT}"
    panel_line "数据目录" "$WEB_SCRCPY_DATA_HOST"
    print_rule

    menu_group "安装与更新"
    menu_item 1 "引导配置并安装" "$C_GREEN"
    menu_item 2 "使用当前配置安装/更新" "$C_GREEN"
    menu_item 3 "更新基础镜像并重新安装" "$C_YELLOW"

    menu_group "服务管理"
    menu_item 4 "启动服务" "$C_GREEN"
    menu_item 5 "停止服务" "$C_RED"
    menu_item 6 "重启服务" "$C_YELLOW"
    menu_item 7 "查看状态" "$C_CYAN"
    menu_item 8 "查看最近日志" "$C_CYAN"

    menu_group "配置与账号"
    menu_item 9 "修改部署配置" "$C_CYAN"
    menu_item 10 "重置管理员密码" "$C_RED"
    menu_item 11 "查看命令帮助" "$C_GRAY"
    menu_item 0 "退出" "$C_GRAY"

    printf '\n%s>%s 请选择 / Choose: ' "$C_YELLOW" "$C_RESET"
    IFS= read -r choice || exit 1
    choice=$(printf '%s' "$choice" | tr -d '\r')
    case "$choice" in
      1)
        if configure_wizard; then (skip_build=false; pull_images=false; install_service) || true; fi
        pause_menu
        ;;
      2) (skip_build=false; pull_images=false; install_service) || true; pause_menu ;;
      3) (skip_build=false; pull_images=true; install_service) || true; pause_menu ;;
      4) (start_service) || true; pause_menu ;;
      5) (stop_service) || true; pause_menu ;;
      6) (restart_service) || true; pause_menu ;;
      7) (show_status) || true; pause_menu ;;
      8) (show_logs) || true; pause_menu ;;
      9) configure_wizard || true; pause_menu ;;
      10)
        if prompt_confirm "确认重置 admin 密码？"; then (reset_admin) || true; fi
        pause_menu
        ;;
      11) usage; pause_menu ;;
      0|q|Q|quit|exit) exit 0 ;;
      *) error_msg "无效选项 / invalid choice: $choice"; pause_menu ;;
    esac
  done
}

if [ "$ACTION" = auto ]; then
  if is_interactive; then ACTION=menu; else ACTION=install; fi
fi

case "$ACTION" in
  menu) show_menu ;;
  configure) configure_wizard ;;
  install) install_service ;;
  start) start_service ;;
  stop) stop_service ;;
  restart) restart_service ;;
  status) show_status ;;
  logs) show_logs ;;
  reset_admin) reset_admin ;;
  *) die "unsupported action: $ACTION" ;;
esac
