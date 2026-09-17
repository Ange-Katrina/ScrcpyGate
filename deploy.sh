#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

# Keep deployment metadata and transient files private by default.  The
# setting only affects this process and does not change the application's
# runtime file permissions.
umask 077

DEPLOY_LOCK_DIR="$SCRIPT_DIR/.scrcpygate-deploy.lock"
DEPLOY_LOCK_OWNER_FILE="$DEPLOY_LOCK_DIR/owner"
DEPLOY_LOCK_HELD=false
ENV_TMP_FILE=""
TOKEN_KEY_TMP_FILE=""
PULL_PID=""
BACKUP_OUTPUT=""
BACKUP_STAGING_DIR=""
RESTORE_ARCHIVE=""
RESTORE_SAFETY_DIR=""
PRE_RESTORE_ARCHIVE=""
ASSUME_YES=false
NO_RESTART=false
DATA_ONLY=false
BACKUP_KEEP=""

cleanup_deploy_state() {
  exit_status=$?
  trap - 0 HUP INT TERM

  # A signal can arrive while the portable background pull is waiting.  Stop
  # only the child owned by this invocation, then reap it before exiting.
  if [ -n "${PULL_PID:-}" ]; then
    kill "$PULL_PID" 2>/dev/null || true
    wait "$PULL_PID" 2>/dev/null || true
    PULL_PID=""
  fi

  if [ -n "${ENV_TMP_FILE:-}" ]; then
    rm -f "$ENV_TMP_FILE" 2>/dev/null || true
    ENV_TMP_FILE=""
  fi

  if [ -n "${TOKEN_KEY_TMP_FILE:-}" ]; then
    rm -f "$TOKEN_KEY_TMP_FILE" 2>/dev/null || true
    TOKEN_KEY_TMP_FILE=""
  fi

  # Backup/restore staging holds a copy of the database (and the ALAS key file),
  # so it must never outlive the process, including on failure or signal.
  if [ -n "${BACKUP_STAGING_DIR:-}" ]; then
    rm -rf "$BACKUP_STAGING_DIR" 2>/dev/null || true
    BACKUP_STAGING_DIR=""
  fi

  if [ "${DEPLOY_LOCK_HELD:-false}" = true ]; then
    rm -f "$DEPLOY_LOCK_OWNER_FILE" 2>/dev/null || true
    rmdir "$DEPLOY_LOCK_DIR" 2>/dev/null || true
    DEPLOY_LOCK_HELD=false
  fi

  exit "$exit_status"
}

trap cleanup_deploy_state 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# The base image can be overridden through the environment or .env.  Mirrors
# are only used when the configured image cannot be pulled; keeping the list
# configurable avoids silently trusting an unreviewed registry in production.
DEFAULT_PYTHON_IMAGE="python:3.12-alpine"
DEFAULT_PYTHON_IMAGE_MIRRORS="docker.m.daocloud.io/library/python:3.12-alpine,docker.1ms.run/library/python:3.12-alpine"

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

acquire_deploy_lock() {
  [ "${DEPLOY_LOCK_HELD:-false}" = true ] && return 0

  [ -L "$DEPLOY_LOCK_DIR" ] && die "部署锁路径不能是符号链接 / deployment lock must not be a symlink"

  if mkdir "$DEPLOY_LOCK_DIR" 2>/dev/null; then
    DEPLOY_LOCK_HELD=true
    if ! printf '%s\n' "$$" > "$DEPLOY_LOCK_OWNER_FILE"; then
      rmdir "$DEPLOY_LOCK_DIR" 2>/dev/null || true
      die "无法写入部署锁 / could not write deployment lock"
    fi
    return 0
  fi

  lock_pid=""
  if [ -f "$DEPLOY_LOCK_OWNER_FILE" ]; then
    lock_pid=$(sed -n '1p' "$DEPLOY_LOCK_OWNER_FILE" 2>/dev/null || true)
  fi
  case "$lock_pid" in
    ''|*[!0-9]*)
      die "检测到已有部署锁：$DEPLOY_LOCK_DIR；请确认没有其他 deploy.sh 正在运行后移除该锁"
      ;;
  esac
  if kill -0 "$lock_pid" 2>/dev/null; then
    die "另一个 deploy.sh 正在运行（PID $lock_pid）；请稍后重试"
  fi

  # The recorded process is gone, so this is a stale lock left by an
  # interrupted invocation.  Remove only the exact lock directory and retry
  # the atomic mkdir; if another process wins the race, fail closed.
  rm -f "$DEPLOY_LOCK_OWNER_FILE" 2>/dev/null || true
  if ! rmdir "$DEPLOY_LOCK_DIR" 2>/dev/null; then
    die "无法清理失效部署锁 / could not clear stale deployment lock"
  fi
  if ! mkdir "$DEPLOY_LOCK_DIR" 2>/dev/null; then
    die "部署锁刚被其他进程占用，请稍后重试 / deployment lock is busy"
  fi
  DEPLOY_LOCK_HELD=true
  if ! printf '%s\n' "$$" > "$DEPLOY_LOCK_OWNER_FILE"; then
    rmdir "$DEPLOY_LOCK_DIR" 2>/dev/null || true
    die "无法写入部署锁 / could not write deployment lock"
  fi
}

release_deploy_lock() {
  [ "${DEPLOY_LOCK_HELD:-false}" = true ] || return 0
  rm -f "$DEPLOY_LOCK_OWNER_FILE" 2>/dev/null || true
  if ! rmdir "$DEPLOY_LOCK_DIR" 2>/dev/null; then
    error_msg "无法释放部署锁：$DEPLOY_LOCK_DIR"
    return 1
  fi
  DEPLOY_LOCK_HELD=false
}

run_locked() {
  acquire_deploy_lock
  action_status=0
  if "$@"; then
    :
  else
    action_status=$?
  fi
  if ! release_deploy_lock; then
    [ "$action_status" -ne 0 ] || action_status=1
  fi
  return "$action_status"
}

cleanup_menu_action_lock() {
  # POSIX shells keep $$ unchanged in a subshell.  A menu action therefore
  # records the parent menu PID in the lock file even when the action exits
  # early via die().  The foreground action has finished by this point, so a
  # lock owned by this exact menu process is safe to remove defensively.
  [ -d "$DEPLOY_LOCK_DIR" ] || return 0
  [ -f "$DEPLOY_LOCK_OWNER_FILE" ] || return 0
  menu_lock_pid=$(sed -n '1p' "$DEPLOY_LOCK_OWNER_FILE" 2>/dev/null || true)
  [ "$menu_lock_pid" = "$$" ] || return 0
  rm -f "$DEPLOY_LOCK_OWNER_FILE" 2>/dev/null || true
  rmdir "$DEPLOY_LOCK_DIR" 2>/dev/null || true
}

run_menu_action() {
  menu_action_status=0
  if (run_locked "$@"); then
    :
  else
    menu_action_status=$?
  fi
  cleanup_menu_action_lock
  return "$menu_action_status"
}

print_rule() {
  printf '%s--------------------------------------------------%s\n' "$C_GRAY" "$C_RESET"
}

safe_display() {
  value=${1:-}
  case "${SCRCPYGATE_SHOW_PRIVATE_IPS:-true}" in 0|false|FALSE|no|NO|off|OFF) ;; *) printf '%s\n' "$value"; return 0 ;; esac
  printf '%s\n' "$value" | awk '
    {
      remaining=$0
      gsub(/[fF][cCdD][0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f:]+/, "<private-ipv6>", remaining)
      gsub(/[fF][eE][89aAbB][0-9A-Fa-f]:[0-9A-Fa-f:]+/, "<private-ipv6>", remaining)
      output=""
      while (match(remaining, /[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/)) {
        prefix=substr(remaining, 1, RSTART-1)
        ip=substr(remaining, RSTART, RLENGTH)
        split(ip, octet, ".")
        private=(octet[1] == 10 || (octet[1] == 172 && octet[2] >= 16 && octet[2] <= 31) || (octet[1] == 192 && octet[2] == 168) || (octet[1] == 169 && octet[2] == 254))
        output=output prefix (private ? "<private-ip>" : ip)
        remaining=substr(remaining, RSTART+RLENGTH)
      }
      print output remaining
    }
  '
}

panel_top() {
  printf '\n%s%s%s\n' "${C_BOLD}${C_CYAN}" "$1" "$C_RESET"
  print_rule
}

panel_line() {
  displayed_value=$(safe_display "$2")
  printf '  %s%s%s : %s\n' "$C_DIM" "$1" "$C_RESET" "$displayed_value"
}

# 首次安装/配置确认环节和常规面板默认回显实际地址,便于核对;
# 如需在终端隐藏域名或内网 IP,将对应 SCRCPYGATE_SHOW_* 设置为 false。
panel_line_raw() {
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
  ./deploy.sh --menu         打开管理菜单
  ./deploy.sh --configure    打开部署配置向导
  ./deploy.sh --install      使用当前配置安装或更新
  ./deploy.sh --install-deps 自动安装缺失的 Docker/Compose 后继续安装
  ./deploy.sh --pull         更新基础镜像后安装或更新
  ./deploy.sh --skip-build   复用已有 scrcpygate:local 镜像

更新到已发布的镜像:
  ./deploy.sh --update [--image <引用>] [--skip-update-backup]
      从 GHCR 拉取已发布镜像并原地更新（**不重新构建源码**）：
      先备份数据目录与 .env，写入 SCRCPYGATE_IMAGE/SCRCPYGATE_VERSION，
      再 docker compose up -d --no-build 并过健康检查；
      拉取失败或健康检查不通过时保留配置与数据，自动回滚到更新前的镜像，
      不会退回源码构建。--image 支持 tag（ghcr.io/owner/scrcpygate:v1.2.3）
      或 digest（ghcr.io/owner/scrcpygate@sha256:...）；默认取 .env 的
      SCRCPYGATE_UPDATE_IMAGE，未设置时用 ghcr.io/ange-katrina/scrcpygate:latest。
      更新前备份默认开启，--skip-update-backup 可跳过。

服务管理:
  ./deploy.sh --start | --stop | --restart
  ./deploy.sh --status | --logs [行数] | --reset-admin
  ./deploy.sh --uninstall [--purge]   卸载服务；默认保留数据和配置
  ./deploy.sh --uninstall --purge      卸载服务并删除镜像、数据和配置（不可恢复）

凭据与候选制品:
  ./deploy.sh --token-status       输出脱敏的 ALAS Token 迁移状态
  ./deploy.sh --migrate-alas-token 执行迁移/轮换并输出脱敏状态
  ./deploy.sh --clear-alas-token   清空换不回密钥的 ALAS Token（之后在 ALAS 设置里重新填写）
  ./deploy.sh --candidate-manifest [文件] 生成清洁 checkout 的文件哈希与镜像 digest

备份与恢复:
  ./deploy.sh --backup [路径|目录] [--keep N]
      备份数据目录（在线 SQLite 快照 + ALAS 密钥 + 清单 + 校验和）；
      --keep N 只保留最近 N 份（默认值可用 SCRCPYGATE_BACKUP_KEEP 设置）
  ./deploy.sh --list-backups        列出备份与恢复前归档（含类别与创建时间）
  ./deploy.sh --restore <归档> [--yes] [--no-restart] [--data-only]
      恢复备份：默认先停容器，把当前数据另存为恢复前归档
      （backups/scrcpygate-pre-restore-*.tar.gz），恢复后启动并过健康门；
      失败自动回滚到临时副本。--yes 跳过确认（非交互），--no-restart 恢复后
      不启动服务，--data-only 完全不触碰容器。
  备份目录默认 $PWD/backups，可用 SCRCPYGATE_BACKUP_DIR 覆盖

其他:
  ./deploy.sh --check         环境自检（.env/Docker/Compose/容器/镜像/健康检查，不做任何修改）
  ./deploy.sh --check-conflicts 检查监听端口和旧 ScrcpyGate 容器（可交互确认停止/移除）
  ./deploy.sh --check-production 生产边界校验（只读；不会显示或修改密钥）
  ./deploy.sh --help

交互模式会在安装系统软件前显示命令并请求确认。
非交互模式仅在使用 --install-deps 或设置
SCRCPYGATE_AUTO_INSTALL_DEPS=true 时安装系统软件。

界面默认显示已配置的域名与内网 IP;设置 SCRCPYGATE_SHOW_PUBLIC_HOST=false
或 SCRCPYGATE_SHOW_PRIVATE_IPS=false 可分别隐藏对应地址。
首次安装的"确认配置"与"开始安装"计划会直接显示您填写的真实值,便于核对。

镜像网络故障时可配置:
  SCRCPYGATE_REGISTRY_IP_FAMILY=auto|ipv6|ipv4
  SCRCPYGATE_PYTHON_IMAGE_MIRRORS=host/path:tag[,host/path:tag...]
  SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS=8
  SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS=120

Install preserves existing accounts and passwords. Normal uninstall keeps data,
its ALAS key, .env and the local image; --purge explicitly removes local data.
Missing keys for encrypted data must be recovered before automatic provisioning.
EOF
}

ACTION=auto
skip_build=false
pull_images=false
auto_install_deps=${SCRCPYGATE_AUTO_INSTALL_DEPS:-false}
LOG_LINES=""
CANDIDATE_MANIFEST=""
UPDATE_IMAGE=""
UPDATE_SKIP_BACKUP=false
PURGE=false
PORT_OCCUPANCY_STATUS=unknown
PORT_OCCUPANCY_TOOL=""
PORT_OCCUPANCY_DETAILS=""
PORT_OCCUPANCY_PIDS=""
PORT_OCCUPANCY_CONTAINERS=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --menu) ACTION=menu ;;
    --configure) ACTION=configure ;;
    --install) ACTION=install ;;
    --install-deps) ACTION=install; auto_install_deps=true ;;
    --pull) ACTION=install; pull_images=true ;;
    --skip-build) ACTION=install; skip_build=true ;;
    --update) ACTION=update ;;
    --image)
      ACTION=update
      if [ "$#" -gt 1 ]; then
        case "$2" in
          -*) die "--image 需要镜像引用 / --image requires an image reference" ;;
          *) UPDATE_IMAGE=$2; shift ;;
        esac
      else
        die "--image 需要镜像引用 / --image requires an image reference"
      fi
      ;;
    --image=*)
      ACTION=update
      UPDATE_IMAGE=${1#--image=}
      [ -n "$UPDATE_IMAGE" ] || die "--image 需要镜像引用 / --image requires an image reference"
      ;;
    --skip-update-backup) UPDATE_SKIP_BACKUP=true ;;
    --start) ACTION=start ;;
    --stop) ACTION=stop ;;
    --restart) ACTION=restart ;;
    --status) ACTION=status ;;
    --check) ACTION=check ;;
    --check-conflicts) ACTION=check_conflicts ;;
    --check-production) ACTION=check_production ;;
    --logs)
      ACTION=logs
      if [ "$#" -gt 1 ]; then
        case "$2" in
          -*) ;;
          ''|*[!0-9]*) die "--logs 需要整数行数 / --logs expects an integer line count" ;;
          *) LOG_LINES=$2; shift ;;
        esac
      fi
      ;;
    --logs=*)
      ACTION=logs
      LOG_LINES=${1#--logs=}
      case "$LOG_LINES" in ''|*[!0-9]*) die "--logs 需要整数行数 / --logs expects an integer line count" ;; esac
      ;;
    --token-status) ACTION=token_status ;;
    --migrate-alas-token) ACTION=migrate_alas_token ;;
    --clear-alas-token) ACTION=clear_alas_token ;;
    --candidate-manifest)
      ACTION=candidate_manifest
      if [ "$#" -gt 1 ]; then
        case "$2" in -*) ;; *) CANDIDATE_MANIFEST=$2; shift ;; esac
      fi
      ;;
    --reset-admin) ACTION=reset_admin ;;
    --backup)
      ACTION=backup
      if [ "$#" -gt 1 ]; then
        case "$2" in
          -*) ;;
          *) BACKUP_OUTPUT=$2; shift ;;
        esac
      fi
      ;;
    --list-backups) ACTION=list_backups ;;
    --restore)
      ACTION=restore
      if [ "$#" -gt 1 ]; then
        case "$2" in
          -*) ;;
          *) RESTORE_ARCHIVE=$2; shift ;;
        esac
      fi
      ;;
    --yes) ASSUME_YES=true ;;
    --no-restart) NO_RESTART=true ;;
    --data-only) DATA_ONLY=true ;;
    --keep)
      if [ "$#" -gt 1 ]; then
        case "$2" in
          -*) ;;
          *) BACKUP_KEEP=$2; shift ;;
        esac
      fi
      ;;
    --uninstall) ACTION=uninstall ;;
    --purge) PURGE=true ;;
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

reject_persisted_secrets() {
  [ -f .env ] || return 0
  for secret_name in \
    ALAS_TOKEN_ENCRYPTION_KEY \
    ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS \
    ALAS_GYRE_TOKEN \
    INITIAL_ADMIN_PASSWORD
  do
    if [ -n "$(dotenv_value "$secret_name")" ]; then
      case "$secret_name" in
        ALAS_TOKEN_ENCRYPTION_KEY)
          die "ALAS_TOKEN_ENCRYPTION_KEY must be injected through the external environment; secrets must not be stored in .env"
          ;;
        *)
          die "$secret_name must be injected through the external environment; secrets must not be stored in .env"
          ;;
      esac
    fi
  done
}

reject_legacy_data_token() {
  data_path=$(configured_data_path_from_disk 2>/dev/null || true)
  [ -n "$data_path" ] || return 0
  legacy_file="$data_path/.env"
  if [ -L "$legacy_file" ]; then
    die "数据目录中的 legacy .env 不能是符号链接；请先完成 ALAS Token 迁移"
  fi
  [ -f "$legacy_file" ] || return 0
  [ -z "$(dotenv_value_from_file "$legacy_file" ALAS_GYRE_TOKEN)" ] \
    || die "数据目录中的 legacy ALAS_GYRE_TOKEN 尚未清除；请先完成 ALAS Token 迁移"
}

ensure_env_file() {
  if [ ! -f .env ]; then
    [ -f .env.example ] || die ".env.example is missing"
    cp .env.example .env
    chmod 600 .env 2>/dev/null || warn_msg "无法限制 .env 权限 / could not restrict .env permissions"
    success_msg "已从 .env.example 创建 .env"
    if ! is_interactive; then
      warn_msg "非交互模式自动生成的 .env 仅监听 127.0.0.1:5000(仅本机可访问);请使用 ./deploy.sh --configure 或环境变量调整后再对外提供服务"
    fi
  fi
  reject_persisted_secrets
}

dotenv_value_from_file() {
  file=$1
  wanted=$2
  [ -f "$file" ] || return 0
  # 经 ENVIRON 传参而非 awk -v：-v 会把值里的反斜杠当转义序列解析。
  AWK_WANTED=$wanted awk '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    {
      line = $0
      sub(/\r$/, "", line)
      separator = index(line, "=")
      if (!separator) next
      name = substr(line, 1, separator - 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      sub(/^export[[:space:]]+/, "", name)
      if (name != ENVIRON["AWK_WANTED"]) next
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
  ' "$file"
}

dotenv_value() {
  dotenv_value_from_file .env "$1"
}

set_env_value() {
  key=$1
  value=$2
  ENV_TMP_FILE=$(mktemp .env.XXXXXX) || { ENV_TMP_FILE=""; die "无法创建 .env 临时文件"; }
  # 经 ENVIRON 传参而非 awk -v：-v 会把值里的反斜杠当转义序列解析。
  AWK_WANTED=$key AWK_REPLACEMENT=$value awk '
    BEGIN { updated = 0 }
    {
      line = $0
      probe = line
      sub(/\r$/, "", probe)
      separator = index(probe, "=")
      if (separator) {
        name = substr(probe, 1, separator - 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
        sub(/^export[[:space:]]+/, "", name)
        if (name == ENVIRON["AWK_WANTED"]) {
          if (!updated) print ENVIRON["AWK_WANTED"] "=" ENVIRON["AWK_REPLACEMENT"]
          updated = 1
          next
        }
      }
      print line
    }
    END { if (!updated) print ENVIRON["AWK_WANTED"] "=" ENVIRON["AWK_REPLACEMENT"] }
  ' .env > "$ENV_TMP_FILE" || { rm -f "$ENV_TMP_FILE"; ENV_TMP_FILE=""; die "无法更新 .env / failed to update .env"; }
  mv "$ENV_TMP_FILE" .env || { rm -f "$ENV_TMP_FILE"; ENV_TMP_FILE=""; die "无法更新 .env / failed to update .env"; }
  ENV_TMP_FILE=""
}

load_settings() {
  PYTHON_IMAGE=${PYTHON_IMAGE:-$(dotenv_value PYTHON_IMAGE)}
  SCRCPYGATE_PYTHON_IMAGE_MIRRORS=${SCRCPYGATE_PYTHON_IMAGE_MIRRORS:-$(dotenv_value SCRCPYGATE_PYTHON_IMAGE_MIRRORS)}
  SCRCPYGATE_REGISTRY_IP_FAMILY=${SCRCPYGATE_REGISTRY_IP_FAMILY:-$(dotenv_value SCRCPYGATE_REGISTRY_IP_FAMILY)}
  SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS=${SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS:-$(dotenv_value SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS)}
  SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS=${SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS:-$(dotenv_value SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS)}
  WEB_SCRCPY_BIND=${WEB_SCRCPY_BIND:-$(dotenv_value WEB_SCRCPY_BIND)}
  WEB_SCRCPY_PORT=${WEB_SCRCPY_PORT:-$(dotenv_value WEB_SCRCPY_PORT)}
  WEB_SCRCPY_DATA_HOST=${WEB_SCRCPY_DATA_HOST:-$(dotenv_value WEB_SCRCPY_DATA_HOST)}
  PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-$(dotenv_value PUBLIC_BASE_URL)}
  # Display controls are intentionally non-secret, but they must be loaded
  # from .env as well as the process environment so the management panel can
  # honor an explicit local configuration.
  SCRCPYGATE_SHOW_PUBLIC_HOST=${SCRCPYGATE_SHOW_PUBLIC_HOST:-$(dotenv_value SCRCPYGATE_SHOW_PUBLIC_HOST)}
  SCRCPYGATE_SHOW_PRIVATE_IPS=${SCRCPYGATE_SHOW_PRIVATE_IPS:-$(dotenv_value SCRCPYGATE_SHOW_PRIVATE_IPS)}
  ALAS_EMBED_ORIGIN=${ALAS_EMBED_ORIGIN:-$(dotenv_value ALAS_EMBED_ORIGIN)}
  ALLOWED_HOSTS=${ALLOWED_HOSTS:-$(dotenv_value ALLOWED_HOSTS)}
  ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-$(dotenv_value ALLOWED_ORIGINS)}
  ALLOW_NULL_ORIGIN=${ALLOW_NULL_ORIGIN:-$(dotenv_value ALLOW_NULL_ORIGIN)}
  ALLOW_MISSING_WEBSOCKET_ORIGIN=${ALLOW_MISSING_WEBSOCKET_ORIGIN:-$(dotenv_value ALLOW_MISSING_WEBSOCKET_ORIGIN)}
  TRUST_PROXY=${TRUST_PROXY:-$(dotenv_value TRUST_PROXY)}
  TRUSTED_PROXY_IPS=${TRUSTED_PROXY_IPS:-$(dotenv_value TRUSTED_PROXY_IPS)}
  SESSION_COOKIE_SECURE=${SESSION_COOKIE_SECURE:-$(dotenv_value SESSION_COOKIE_SECURE)}
  ENABLE_API_DOCS=${ENABLE_API_DOCS:-$(dotenv_value ENABLE_API_DOCS)}
  ALAS_ALLOWED_HOSTS=${ALAS_ALLOWED_HOSTS:-$(dotenv_value ALAS_ALLOWED_HOSTS)}
  ALAS_ALLOWED_CIDRS=${ALAS_ALLOWED_CIDRS:-$(dotenv_value ALAS_ALLOWED_CIDRS)}
  SCRCPYGATE_HEALTH_TIMEOUT=${SCRCPYGATE_HEALTH_TIMEOUT:-$(dotenv_value SCRCPYGATE_HEALTH_TIMEOUT)}
  SCRCPYGATE_WS_MAX_SIZE=${SCRCPYGATE_WS_MAX_SIZE:-$(dotenv_value SCRCPYGATE_WS_MAX_SIZE)}
  # Secrets deliberately do not fall back to .env.  The bare environment
  # entries in compose.yaml pass only values injected by the process running
  # this script.
  ALAS_TOKEN_ENCRYPTION_KEY=${ALAS_TOKEN_ENCRYPTION_KEY:-}
  ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS=${ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS:-}
  INITIAL_ADMIN_PASSWORD=${INITIAL_ADMIN_PASSWORD:-}

  WEB_SCRCPY_BIND=${WEB_SCRCPY_BIND:-127.0.0.1}
  WEB_SCRCPY_PORT=${WEB_SCRCPY_PORT:-5000}
  WEB_SCRCPY_DATA_HOST=${WEB_SCRCPY_DATA_HOST:-./data}
  PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-http://127.0.0.1:${WEB_SCRCPY_PORT}}
  ALAS_EMBED_ORIGIN=${ALAS_EMBED_ORIGIN:-}
  ALLOWED_HOSTS=${ALLOWED_HOSTS:-127.0.0.1,localhost}
  ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-$PUBLIC_BASE_URL}
  ALLOW_NULL_ORIGIN=${ALLOW_NULL_ORIGIN:-false}
  ALLOW_MISSING_WEBSOCKET_ORIGIN=${ALLOW_MISSING_WEBSOCKET_ORIGIN:-false}
  TRUST_PROXY=${TRUST_PROXY:-false}
  TRUSTED_PROXY_IPS=${TRUSTED_PROXY_IPS:-127.0.0.1,::1}
  SESSION_COOKIE_SECURE=${SESSION_COOKIE_SECURE:-false}
  ENABLE_API_DOCS=${ENABLE_API_DOCS:-false}
  ALAS_ALLOWED_HOSTS=${ALAS_ALLOWED_HOSTS:-}
  ALAS_ALLOWED_CIDRS=${ALAS_ALLOWED_CIDRS:-}
  SCRCPYGATE_HEALTH_TIMEOUT=${SCRCPYGATE_HEALTH_TIMEOUT:-90}
  SCRCPYGATE_WS_MAX_SIZE=${SCRCPYGATE_WS_MAX_SIZE:-65536}
  PYTHON_IMAGE=${PYTHON_IMAGE:-$DEFAULT_PYTHON_IMAGE}
  SCRCPYGATE_PYTHON_IMAGE_MIRRORS=${SCRCPYGATE_PYTHON_IMAGE_MIRRORS:-$DEFAULT_PYTHON_IMAGE_MIRRORS}
  SCRCPYGATE_REGISTRY_IP_FAMILY=${SCRCPYGATE_REGISTRY_IP_FAMILY:-auto}
  SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS=${SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS:-8}
  SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS=${SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS:-120}
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
  case "$SCRCPYGATE_WS_MAX_SIZE" in
    ''|*[!0-9]*) die "SCRCPYGATE_WS_MAX_SIZE must be an integer" ;;
  esac
  if [ "$SCRCPYGATE_WS_MAX_SIZE" -lt 1024 ] || [ "$SCRCPYGATE_WS_MAX_SIZE" -gt 67108864 ]; then
    die "SCRCPYGATE_WS_MAX_SIZE must be between 1024 and 67108864 bytes"
  fi
  case "$PYTHON_IMAGE" in
    ''|*[[:space:]]*) die "PYTHON_IMAGE must be a single image reference" ;;
  esac
  case "$SCRCPYGATE_PYTHON_IMAGE_MIRRORS" in
    *[[:space:]]*) die "SCRCPYGATE_PYTHON_IMAGE_MIRRORS must be comma-separated image references without spaces" ;;
  esac
  case "$SCRCPYGATE_REGISTRY_IP_FAMILY" in
    auto|ipv4|ipv6) ;;
    *) die "SCRCPYGATE_REGISTRY_IP_FAMILY must be auto, ipv6 or ipv4" ;;
  esac
  case "$SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS" in
    ''|*[!0-9]*) die "SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS must be an integer" ;;
  esac
  if [ "$SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS" -lt 1 ] || [ "$SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS" -gt 60 ]; then
    die "SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS must be between 1 and 60 seconds"
  fi
  case "$SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS" in
    ''|*[!0-9]*) die "SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS must be an integer" ;;
  esac
  if [ "$SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS" -lt 10 ] || [ "$SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS" -gt 900 ]; then
    die "SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS must be between 10 and 900 seconds"
  fi
  case "$PUBLIC_BASE_URL" in
    https://*)
      if [ "$SESSION_COOKIE_SECURE" != true ]; then
        if [ "${DEPLOY_SETTINGS_READ_ONLY:-false}" = true ]; then
          die "HTTPS 配置必须预先设置 SESSION_COOKIE_SECURE=true；只读命令不会修改 .env"
        fi
        SESSION_COOKIE_SECURE=true
        if [ -f .env ]; then
          set_env_value SESSION_COOKIE_SECURE true
        fi
        warn_msg "HTTPS 地址需要安全 Cookie，已启用 SESSION_COOKIE_SECURE=true（已写入 .env）"
      fi
      ;;
    http://*) ;;
    *) die "PUBLIC_BASE_URL must start with http:// or https://" ;;
  esac
  valid_bind_address "$WEB_SCRCPY_BIND" \
    || die "WEB_SCRCPY_BIND must be a valid IPv4 bind address"
  if [ -n "$ALAS_EMBED_ORIGIN" ]; then
    case "$ALAS_EMBED_ORIGIN" in
      http://*|https://*) ;;
      *) die "ALAS_EMBED_ORIGIN must start with http:// or https://" ;;
    esac
  fi
  case "$WEB_SCRCPY_DATA_HOST" in
    ''|'/'|'.'|'./') die "WEB_SCRCPY_DATA_HOST must point to a dedicated data directory" ;;
  esac
  valid_host_list "$ALLOWED_HOSTS" || die "ALLOWED_HOSTS 必须为逗号分隔的主机名或 IP,且不含空格、斜杠或 @"
  if [ -n "$ALLOWED_ORIGINS" ]; then
    valid_origin_list "$ALLOWED_ORIGINS" || die "ALLOWED_ORIGINS 必须为逗号分隔的 http(s)://主机[:端口],且不能包含路径"
  fi

  export WEB_SCRCPY_BIND WEB_SCRCPY_PORT WEB_SCRCPY_DATA_HOST
  export PUBLIC_BASE_URL ALAS_EMBED_ORIGIN ALLOWED_HOSTS ALLOWED_ORIGINS ALLOW_NULL_ORIGIN
  export ALLOW_MISSING_WEBSOCKET_ORIGIN ENABLE_API_DOCS ALAS_ALLOWED_HOSTS ALAS_ALLOWED_CIDRS
  export TRUST_PROXY TRUSTED_PROXY_IPS SESSION_COOKIE_SECURE
  export SCRCPYGATE_WS_MAX_SIZE
  export PYTHON_IMAGE SCRCPYGATE_PYTHON_IMAGE_MIRRORS SCRCPYGATE_REGISTRY_IP_FAMILY
  export SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS
  export ALAS_TOKEN_ENCRYPTION_KEY ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS
}

validate_production_boundary() {
  [ -f .env ] || die "生产边界校验需要 .env；不会自动创建或修改配置"
  load_settings
  reject_persisted_secrets
  reject_legacy_data_token

  case "$PUBLIC_BASE_URL" in
    https://*) ;;
    *) die "生产边界要求 PUBLIC_BASE_URL 使用 https://" ;;
  esac
  valid_origin_list "$PUBLIC_BASE_URL" || die "PUBLIC_BASE_URL 必须是无路径的完整 HTTPS Origin"

  [ -z "$ALAS_EMBED_ORIGIN" ] || die "Gateway 已关闭时 ALAS_EMBED_ORIGIN 必须为空"
  valid_host_list "$ALLOWED_HOSTS" || die "ALLOWED_HOSTS 格式无效"
  valid_origin_list "$ALLOWED_ORIGINS" || die "ALLOWED_ORIGINS 格式无效"
  public_host=$(public_host_from_url "$PUBLIC_BASE_URL")
  csv_contains_exact "$public_host" "$ALLOWED_HOSTS" || die "ALLOWED_HOSTS 必须包含 PUBLIC_BASE_URL 的主机"
  csv_contains_exact "$PUBLIC_BASE_URL" "$ALLOWED_ORIGINS" || die "ALLOWED_ORIGINS 必须包含 PUBLIC_BASE_URL"
  case "$ALLOWED_HOSTS" in *\**) die "生产边界列表不允许使用通配符" ;; esac
  case "$ALLOWED_ORIGINS" in *\**) die "生产边界列表不允许使用通配符" ;; esac
  case "$TRUSTED_PROXY_IPS" in *\**) die "生产边界列表不允许使用通配符" ;; esac

  [ "$ALLOW_NULL_ORIGIN" = false ] || die "生产边界要求 ALLOW_NULL_ORIGIN=false"
  [ "$ALLOW_MISSING_WEBSOCKET_ORIGIN" = false ] || die "生产边界要求 ALLOW_MISSING_WEBSOCKET_ORIGIN=false"
  [ "$SESSION_COOKIE_SECURE" = true ] || die "HTTPS 生产边界要求 SESSION_COOKIE_SECURE=true"
  [ "$ENABLE_API_DOCS" = false ] || die "生产边界要求 ENABLE_API_DOCS=false"
  case "$SCRCPYGATE_WS_MAX_SIZE" in
    ''|*[!0-9]*) die "SCRCPYGATE_WS_MAX_SIZE must be an integer" ;;
  esac
  if [ "$SCRCPYGATE_WS_MAX_SIZE" -lt 1024 ] || [ "$SCRCPYGATE_WS_MAX_SIZE" -gt 67108864 ]; then
    die "SCRCPYGATE_WS_MAX_SIZE must be between 1024 and 67108864 bytes"
  fi

  [ "$TRUST_PROXY" = true ] || die "WAF 终止 TLS 时必须设置 TRUST_PROXY=true"
  valid_production_proxy_list "$TRUSTED_PROXY_IPS" || die "TRUSTED_PROXY_IPS 必须是非空且不能覆盖全部地址空间的 IP/CIDR 列表"
  [ "$WEB_SCRCPY_BIND" = 127.0.0.1 ] || die "生产边界要求 WEB_SCRCPY_BIND=127.0.0.1"
  grep -Eq -- '--workers[[:space:]]+1([[:space:]]|$)' docker-entrypoint.sh \
    || die "docker-entrypoint.sh 未固定为单 worker"
  grep -Eq -- '--ws-max-size[[:space:]]+"\$max_size"' docker-entrypoint.sh \
    || die "docker-entrypoint.sh 未统一使用 SCRCPYGATE_WS_MAX_SIZE"
  validate_env_file_permissions

  [ -n "$ALAS_ALLOWED_CIDRS" ] || die "生产边界要求 ALAS_ALLOWED_CIDRS 使用 Runtime 固定地址"
  [ -z "$ALAS_ALLOWED_HOSTS" ] || die "固定 IP 模式下 ALAS_ALLOWED_HOSTS 必须为空"
  valid_private_single_host_cidrs "$ALAS_ALLOWED_CIDRS" \
    || die "ALAS_ALLOWED_CIDRS 必须只包含内网固定 /32 或 /128 地址"
  [ -n "$ALAS_TOKEN_ENCRYPTION_KEY" ] || die "缺少外部注入的 ALAS_TOKEN_ENCRYPTION_KEY"
  valid_token_encryption_key || die "ALAS_TOKEN_ENCRYPTION_KEY 必须解码为 32 字节"
  if [ -n "$ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS" ]; then
    valid_token_encryption_key_value "$ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS" \
      || die "ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS 必须解码为 32 字节"
    die "生产边界不允许继续注入 ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS；迁移窗口已结束"
  fi

  success_msg "生产边界校验通过（未显示或修改密钥）"
}

prompt_value() {
  label=$1
  default_value=$2
  displayed_default=$(safe_display "$default_value")
  printf '\n%s>%s %s [%s]: ' "$C_YELLOW" "$C_RESET" "$label" "$displayed_default"
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

prompt_confirm_no() {
  label=$1
  printf '\n%s>%s %s [y/N]: ' "$C_YELLOW" "$C_RESET" "$label"
  IFS= read -r answer || return 1
  answer=$(printf '%s' "$answer" | tr -d '\r')
  case "$answer" in y|Y|yes|YES|是) return 0 ;; *) return 1 ;; esac
}

prompt_optional() {
  label=$1
  printf '\n%s>%s %s: ' "$C_YELLOW" "$C_RESET" "$label"
  IFS= read -r answer || return 1
  PROMPT_RESULT=$(printf '%s' "$answer" | tr -d '\r')
}

parse_single_output_line() {
  printf '%s\n' "$1" | tr -d '\r' | awk '
    NF { count++; value=$0 }
    END {
      if (count > 1) exit 1
      if (count == 1) print value
    }
  '
}

detect_primary_ip() {
  if [ -n "${SCRCPYGATE_DETECTED_IP:-}" ]; then
    printf '%s\n' "$SCRCPYGATE_DETECTED_IP"
    return 0
  fi
  detected=""
  if command -v ip >/dev/null 2>&1; then
    detected=$(ip route get 1.1.1.1 2>/dev/null | awk '{ for (i=1; i<=NF; i++) if ($i == "src") { print $(i+1); exit } }' || true)
  fi
  if [ -z "$detected" ] && command -v hostname >/dev/null 2>&1; then
    detected=$(hostname -I 2>/dev/null | awk '{ for (i=1; i<=NF; i++) if ($i !~ /^127\./ && $i !~ /:/) { print $i; exit } }' || true)
  fi
  if [ -z "$detected" ] && command -v ifconfig >/dev/null 2>&1; then
    # BusyBox ifconfig（Alpine 等）输出 "inet addr:1.2.3.4"，GNU net-tools 输出 "inet 1.2.3.4"。
    detected=$(ifconfig 2>/dev/null | awk '
      /inet / {
        addr=$2
        sub(/^addr:/, "", addr)
        if (addr != "127.0.0.1" && addr ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) { print addr; exit }
      }
    ' || true)
  fi
  case "$detected" in
    ''|0.0.0.0|127.*|169.254.*) return 1 ;;
  esac
  printf '%s\n' "$detected"
}

valid_host_input() {
  value=$1
  case "$value" in ''|*' '*|*'/'*|*'@'*|*':'*|*[!A-Za-z0-9.-]*) return 1 ;; esac
  printf '%s\n' "$value" | awk -F. '
    length($0) > 253 { exit 1 }
    {
      for (i=1; i<=NF; i++) {
        if ($i == "" || length($i) > 63) exit 1
        if (substr($i,1,1) !~ /[A-Za-z0-9]/ || substr($i,length($i),1) !~ /[A-Za-z0-9]/) exit 1
      }
    }
  '
}

valid_ipv4() {
  printf '%s\n' "$1" | awk -F. '
    NF != 4 { exit 1 }
    {
      for (i=1; i<=4; i++) {
        if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1
      }
    }
  '
}

valid_bind_address() {
  value=$1
  case "$value" in
    ''|*[[:space:]]*|*,*|*/*) return 1 ;;
  esac
  # compose.yaml publishes the host binding in IPv4 host:port syntax.  Fail
  # early for IPv6/hostname values instead of letting Compose report an
  # ambiguous port mapping later in the install.
  valid_ipv4 "$value"
}

valid_proxy_list() {
  value=$1
  [ -n "$value" ] || return 1
  printf '%s\n' "$value" | awk -F, '
    {
      for (i=1; i<=NF; i++) {
        item=$i
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", item)
        if (item == "") exit 1
        parts=split(item, network, "/")
        if (parts > 2) exit 1
        address=network[1]
        prefix=(parts == 2 ? network[2] : "")
        if (index(address, ":")) {
          if (address !~ /^[0-9A-Fa-f:]+$/ || address ~ /:::/) exit 1
          compression=index(address, "::")
          if (compression && index(substr(address, compression+2), "::")) exit 1
          hextet_count=split(address, hextet, ":")
          populated=0
          for (j=1; j<=hextet_count; j++) {
            if (hextet[j] == "") continue
            if (length(hextet[j]) > 4 || hextet[j] !~ /^[0-9A-Fa-f]+$/) exit 1
            populated++
          }
          if ((compression && populated >= 8) || (!compression && populated != 8)) exit 1
          if (parts == 2 && (prefix !~ /^[0-9]+$/ || prefix < 0 || prefix > 128)) exit 1
        } else {
          count=split(address, octet, ".")
          if (count != 4) exit 1
          for (j=1; j<=count; j++) if (octet[j] !~ /^[0-9]+$/ || octet[j] < 0 || octet[j] > 255) exit 1
          if (parts == 2 && (prefix !~ /^[0-9]+$/ || prefix < 0 || prefix > 32)) exit 1
        }
      }
    }
  '
}

valid_production_proxy_list() {
  value=$1
  valid_proxy_list "$value" || return 1
  key_validator=""
  if command -v python >/dev/null 2>&1; then
    key_validator=python
  elif command -v python3 >/dev/null 2>&1; then
    key_validator=python3
  else
    return 1
  fi
  BOUNDARY_PROXY_CIDRS=$value "$key_validator" - <<'PY'
import ipaddress
import os

for raw in os.environ.get("BOUNDARY_PROXY_CIDRS", "").split(","):
    item = raw.strip()
    try:
        network = ipaddress.ip_network(item, strict=False)
    except ValueError:
        raise SystemExit(1)
    if network.prefixlen == 0 or network.network_address.is_unspecified:
        raise SystemExit(1)
PY
}

valid_host_list() {
  [ -n "$1" ] || return 1
  printf '%s\n' "$1" | awk -F, '
    {
      for (i=1; i<=NF; i++) {
        item=$i
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", item)
        if (item == "" || item ~ /[[:space:]\/@]/) exit 1
        probe=item
        sub(/^\[/, "", probe)
        sub(/\]$/, "", probe)
        if (probe == "" || probe ~ /[^A-Za-z0-9.:_-]/) exit 1
      }
    }
  '
}

valid_origin_list() {
  value=$1
  [ -z "$value" ] && return 0
  printf '%s\n' "$value" | awk -F, '
    {
      for (i=1; i<=NF; i++) {
        item=$i
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", item)
        if (item !~ /^https?:\/\/[A-Za-z0-9.-]+(:[0-9]+)?$/) exit 1
        if (item ~ /:[0-9]+$/) {
          port=item
          sub(/^.*:/, "", port)
          if (port < 1 || port > 65535) exit 1
        }
      }
    }
  '
}

csv_contains_exact() {
  wanted=$1
  values=$2
  [ -n "$wanted" ] || return 1
  printf '%s\n' "$values" | awk -F, -v wanted="$wanted" '
    {
      for (i=1; i<=NF; i++) {
        item=$i
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", item)
        if (item == wanted) found=1
      }
    }
    END { exit(found ? 0 : 1) }
  '
}

valid_single_host_cidrs() {
  value=$1
  valid_proxy_list "$value" || return 1
  printf '%s\n' "$value" | awk -F, '
    {
      for (i=1; i<=NF; i++) {
        item=$i
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", item)
        parts=split(item, network, "/")
        if (parts != 2 || (network[2] != "32" && network[2] != "128")) exit 1
      }
    }
  '
}

valid_private_single_host_cidrs() {
  value=$1
  valid_single_host_cidrs "$value" || return 1
  key_validator=""
  if command -v python >/dev/null 2>&1; then
    key_validator=python
  elif command -v python3 >/dev/null 2>&1; then
    key_validator=python3
  else
    return 1
  fi
  BOUNDARY_CIDRS=$value "$key_validator" - <<'PY'
import ipaddress
import os

for raw in os.environ.get("BOUNDARY_CIDRS", "").split(","):
    item = raw.strip()
    try:
        network = ipaddress.ip_network(item, strict=True)
    except ValueError:
        raise SystemExit(1)
    address = network.network_address
    if (
        network.prefixlen != network.max_prefixlen
        or not address.is_private
        or address.is_unspecified
        or address.is_link_local
        or address.is_multicast
    ):
        raise SystemExit(1)
PY
}

valid_token_encryption_key_value() {
  value=$1
  key_validator=""
  if command -v python >/dev/null 2>&1; then
    key_validator=python
  elif command -v python3 >/dev/null 2>&1; then
    key_validator=python3
  else
    # A minimal host may only provide the POSIX shell and OpenSSL/od.  The
    # deploy-generated format is canonical 64-character hexadecimal, which
    # can be checked without importing a host Python runtime.  Base64 keys
    # still require the normal Python validator and fail closed here.
    printf '%s\n' "$value" | awk '
      length($0) == 64 && $0 ~ /^[0-9A-Fa-f]+$/ { found = 1 }
      END { exit(found ? 0 : 1) }
    '
    return $?
  fi
  BOUNDARY_KEY=$value "$key_validator" - <<'PY'
import base64
import binascii
import os
import re

raw = os.environ.get("BOUNDARY_KEY", "").strip()
try:
    if len(raw) == 64 and all(char in "0123456789abcdefABCDEF" for char in raw):
        decoded = bytes.fromhex(raw)
    else:
        if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", raw):
            raise ValueError("invalid base64")
        unpadded = raw.rstrip("=")
        padding = len(raw) - len(unpadded)
        if len(unpadded) % 4 == 1:
            raise ValueError("invalid base64")
        expected_padding = (-len(unpadded)) % 4
        if padding not in (0, expected_padding):
            raise ValueError("invalid base64")
        padded = unpadded + "=" * expected_padding
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != unpadded:
            raise ValueError("invalid base64")
except (ValueError, UnicodeError, binascii.Error):
    raise SystemExit(1)
raise SystemExit(0 if len(decoded) == 32 else 1)
PY
}

valid_token_encryption_key() {
  valid_token_encryption_key_value "$ALAS_TOKEN_ENCRYPTION_KEY"
}

validate_env_file_permissions() {
  case "$(uname -s 2>/dev/null || true)" in
    MINGW*|MSYS*|CYGWIN*)
      warn_msg "Windows shell 无法可靠读取 POSIX .env 权限；生产 Linux 环境必须使用 600"
      return 0
      ;;
  esac
  mode=""
  if command -v stat >/dev/null 2>&1; then
    mode=$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env 2>/dev/null || true)
  fi
  [ "$mode" = 600 ] || die ".env 必须设置为 600（仅文件所有者可读写）"
}

merge_csv_unique() {
  printf '%s\n' "$1,$2" | awk -F, '
    {
      output=""
      for (i=1; i<=NF; i++) {
        item=$i
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", item)
        if (item != "" && !seen[item]++) output=output (output == "" ? "" : ",") item
      }
      print output
    }
  '
}

public_host_from_url() {
  value=${1#*://}
  value=${value%%/*}
  value=${value%%:*}
  printf '%s\n' "$value"
}

# 用于界面回显:默认显示域名、公网 IP 和内网 IP;
# 将 SCRCPYGATE_SHOW_PUBLIC_HOST=false 或 SCRCPYGATE_SHOW_PRIVATE_IPS=false
# 设置为 false 时分别恢复对应的终端脱敏。
display_url() {
  value=${1:-}
  displayed=$(safe_display "$value")
  if is_truthy "${SCRCPYGATE_SHOW_PUBLIC_HOST:-true}"; then
    printf '%s\n' "$displayed"
    return 0
  fi
  host=$(public_host_from_url "$displayed")
  case "$host" in
    127.0.0.1|localhost|::1|'<private-ip>'|'<private-ipv6>'|'') printf '%s\n' "$displayed" ;;
    *) printf '%s\n' "$displayed" | sed "s#$(printf '%s' "$host" | sed 's/\./[.]/g')#<hidden-host>#" ;;
  esac
}

build_public_url() {
  scheme=$1
  host=$2
  port=$3
  case "$scheme:$port" in
    https:443|http:80) printf '%s://%s\n' "$scheme" "$host" ;;
    *) printf '%s://%s:%s\n' "$scheme" "$host" "$port" ;;
  esac
}

configure_wizard() {
  require_interactive
  ensure_env_file
  load_settings
  system_ip=$(detect_primary_ip || true)
  system_ip_display=${system_ip:-未检测到（请手动输入）}

  panel_top "ScrcpyGate 部署配置向导 / Configuration Wizard"
  panel_line "检测到系统 IPv4" "$system_ip_display"
  panel_line "当前地址" "$(display_url "$PUBLIC_BASE_URL")"
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

  prompt_optional "服务端口（当前 ${WEB_SCRCPY_PORT}；留空使用默认端口 5000）" || return 1
  new_port=$PROMPT_RESULT
  if [ -z "$new_port" ]; then
    new_port=5000
    success_msg "已使用默认服务端口 5000"
  fi
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
  new_allow_null=$ALLOW_NULL_ORIGIN
  new_allow_missing_ws_origin=$ALLOW_MISSING_WEBSOCKET_ORIGIN
  case "$mode" in
    1)
      new_bind=127.0.0.1
      new_public="http://127.0.0.1:${new_port}"
      new_hosts="127.0.0.1,localhost"
      new_origins=$new_public
      new_allow_null=false
      new_allow_missing_ws_origin=false
      new_trust=false
      new_secure=false
      ;;
    2)
      current_host=$(public_host_from_url "$PUBLIC_BASE_URL")
      case "$current_host" in ''|127.0.0.1|localhost) current_host=$system_ip ;; esac
      prompt_optional "服务器局域网 IP 或域名（当前值不回显；留空保持不变；不含协议、路径和端口）" || return 1
      lan_host=$PROMPT_RESULT
      if [ -z "$lan_host" ]; then
        lan_host=$current_host
      fi
      valid_host_input "$lan_host" || { error_msg "请输入纯 IP 或域名，不要包含协议、路径或端口"; return 1; }
      new_bind=0.0.0.0
      new_public=$(build_public_url http "$lan_host" "$new_port")
      new_hosts="127.0.0.1,localhost,${lan_host}"
      new_origins=$new_public
      new_allow_null=false
      new_allow_missing_ws_origin=false
      new_trust=false
      new_secure=false
      ;;
    3)
      # Prefer the detected primary LAN IPv4 so an independently hosted WAF
      # can connect without requiring the operator to retype it.  A same-host
      # WAF must still use 127.0.0.1, and an existing specific reverse-proxy
      # bind remains authoritative when this profile is edited.
      proxy_bind=127.0.0.1
      if valid_ipv4 "${system_ip:-}"; then
        proxy_bind=$system_ip
      fi
      if [ "$TRUST_PROXY" = true ]; then
        case "$WEB_SCRCPY_BIND" in
          ''|0.0.0.0|::) ;;
          *) valid_ipv4 "$WEB_SCRCPY_BIND" && proxy_bind=$WEB_SCRCPY_BIND ;;
        esac
      fi
      prompt_value "反向代理连接的内部绑定 IP（已自动检测：${system_ip_display}；同机 WAF 请使用 127.0.0.1，独立 WAF 可使用检测到的内网 IP）" "$proxy_bind" || return 1
      new_bind=$PROMPT_RESULT
      valid_ipv4 "$new_bind" || { error_msg "内部绑定地址必须是有效 IPv4"; return 1; }

      proxy_host=$(public_host_from_url "$PUBLIC_BASE_URL")
      case "$proxy_host" in ''|127.0.0.1|localhost|"$system_ip") proxy_host="" ;; esac
      if [ -n "$proxy_host" ]; then
        prompt_optional "外部域名或 IP（当前已配置域名不回显；留空保持不变；示例：example.com；不要输入 http://、https://、路径或端口）" || return 1
        if [ -z "$PROMPT_RESULT" ]; then
          PROMPT_RESULT=$proxy_host
        fi
      else
        prompt_optional "外部域名或 IP（必填；示例：example.com；不要输入 http://、https://、路径或端口）" || return 1
      fi
      proxy_host=$PROMPT_RESULT
      valid_host_input "$proxy_host" || { error_msg "请输入纯域名或 IP，不要包含协议、路径或端口"; return 1; }

      if [ "$TRUST_PROXY" = true ]; then
        case "$PUBLIC_BASE_URL" in http://*) proxy_scheme=http ;; *) proxy_scheme=https ;; esac
      else
        proxy_scheme=https
      fi
      prompt_value "外部访问协议（http/https）" "$proxy_scheme" || return 1
      proxy_scheme=$PROMPT_RESULT
      case "$proxy_scheme" in http|https) ;; *) error_msg "外部协议只能是 http 或 https"; return 1 ;; esac

      if [ "$proxy_scheme" = https ]; then proxy_port=443; else proxy_port=80; fi
      prompt_optional "外部端口（留空使用 ${proxy_scheme} 默认端口）" || return 1
      [ -n "$PROMPT_RESULT" ] && proxy_port=$PROMPT_RESULT
      case "$proxy_port" in ''|*[!0-9]*) error_msg "外部端口必须是整数"; return 1 ;; esac
      if [ "$proxy_port" -lt 1 ] || [ "$proxy_port" -gt 65535 ]; then
        error_msg "外部端口必须在 1-65535 之间"
        return 1
      fi
      new_public=$(build_public_url "$proxy_scheme" "$proxy_host" "$proxy_port")

      prompt_value "可信代理 IP/CIDR（填写日志 remote= 的地址；例如 192.0.2.15）" "$TRUSTED_PROXY_IPS" || return 1
      new_trusted=$PROMPT_RESULT
      valid_proxy_list "$new_trusted" || { error_msg "可信代理不能为空，且每项必须是 IP 或 CIDR"; return 1; }
      prompt_optional "额外允许的 Origin（可选，多个用逗号分隔）" || return 1
      extra_origins=$PROMPT_RESULT
      valid_origin_list "$extra_origins" || { error_msg "Origin 必须是完整的 http(s)://主机[:端口]，不能包含路径"; return 1; }
      if [ "$ALLOW_NULL_ORIGIN" = true ]; then
        warn_msg "当前配置允许 Origin: null；反向代理模式默认关闭。只有确认 WAF/客户端确实发送 null 且已完成 CSRF 风险评估时才输入 true"
      fi
      # A WAF should preserve the browser's real Origin.  Accepting null is
      # an explicit compatibility exception, never the reverse-proxy default.
      prompt_value "是否允许 WAF 的 Origin: null（true/false，默认 false；通常不要开启）" "false" || return 1
      new_allow_null=$PROMPT_RESULT
      case "$new_allow_null" in true|false) ;; *) error_msg "ALLOW_NULL_ORIGIN 只能是 true 或 false"; return 1 ;; esac
      new_allow_missing_ws_origin=false
      new_hosts="127.0.0.1,localhost,${proxy_host}"
      [ "$new_bind" = 127.0.0.1 ] || new_hosts="${new_hosts},${new_bind}"
      new_origins=$(merge_csv_unique "$new_public" "$extra_origins")
      new_trust=true
      case "$proxy_scheme" in https) new_secure=true ;; *) new_secure=false ;; esac
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
      prompt_value "是否允许 Origin: null（true/false）" "$ALLOW_NULL_ORIGIN" || return 1
      new_allow_null=$PROMPT_RESULT
      case "$new_allow_null" in true|false) ;; *) error_msg "ALLOW_NULL_ORIGIN 只能是 true 或 false"; return 1 ;; esac
      prompt_value "是否信任代理头（true/false）" "$TRUST_PROXY" || return 1
      new_trust=$PROMPT_RESULT
      prompt_value "可信代理 IP/CIDR" "$TRUSTED_PROXY_IPS" || return 1
      new_trusted=$PROMPT_RESULT
      case "$new_public" in https://*) new_secure=true ;; *) new_secure=$SESSION_COOKIE_SECURE ;; esac
      ;;
  esac

  case "$new_public" in http://*|https://*) ;; *) error_msg "访问地址必须以 http:// 或 https:// 开头"; return 1 ;; esac

  panel_top "确认配置 / Confirm"
  panel_line_raw "模式" "$mode"
  panel_line_raw "监听" "${new_bind}:${new_port}"
  panel_line_raw "访问地址" "$new_public"
  panel_line_raw "数据目录" "$new_data"
  panel_line_raw "信任代理" "$new_trust"
  panel_line_raw "允许 Null Origin" "$new_allow_null"
  panel_line_raw "允许缺少 WebSocket Origin" "$new_allow_missing_ws_origin"
  panel_line_raw "安全 Cookie" "$new_secure"
  print_rule
  prompt_confirm "保存以上配置？" || { warn_msg "已取消"; return 1; }

  set_env_value WEB_SCRCPY_BIND "$new_bind"
  set_env_value WEB_SCRCPY_PORT "$new_port"
  set_env_value WEB_SCRCPY_DATA_HOST "$new_data"
  set_env_value PUBLIC_BASE_URL "$new_public"
  set_env_value ALLOWED_HOSTS "$new_hosts"
  set_env_value ALLOWED_ORIGINS "$new_origins"
  set_env_value ALLOW_NULL_ORIGIN "$new_allow_null"
  set_env_value ALLOW_MISSING_WEBSOCKET_ORIGIN "$new_allow_missing_ws_origin"
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
  ALLOW_NULL_ORIGIN=$new_allow_null
  ALLOW_MISSING_WEBSOCKET_ORIGIN=$new_allow_missing_ws_origin
  TRUST_PROXY=$new_trust
  TRUSTED_PROXY_IPS=$new_trusted
  SESSION_COOKIE_SECURE=$new_secure
  success_msg "配置已保存到 $SCRIPT_DIR/.env"
}

is_truthy() {
  case "${1:-}" in 1|true|TRUE|yes|YES|on|ON) return 0 ;; *) return 1 ;; esac
}

run_as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    if is_interactive; then
      sudo "$@"
    else
      sudo -n true >/dev/null 2>&1 || die "非交互安装无法取得 root 权限；请使用 root 运行或预先配置免密 sudo"
      sudo -n "$@"
    fi
  else
    die "安装系统软件需要 root 权限；请使用 root 运行，或先安装 sudo"
  fi
}

detect_package_manager() {
  if [ -n "${SCRCPYGATE_PACKAGE_MANAGER:-}" ]; then
    printf '%s\n' "$SCRCPYGATE_PACKAGE_MANAGER"
    return 0
  fi
  for manager in apt-get apk dnf yum pacman zypper; do
    if command -v "$manager" >/dev/null 2>&1; then
      printf '%s\n' "$manager"
      return 0
    fi
  done
  return 1
}

detect_os_name() {
  if [ -r /etc/os-release ]; then
    awk -F= '$1 == "PRETTY_NAME" { value=substr($0, index($0, "=")+1); gsub(/^"|"$/, "", value); print value; exit }' /etc/os-release
  else
    uname -s 2>/dev/null || printf '%s\n' "unknown"
  fi
}

show_dependency_install_plan() {
  case "$1" in
    apt-get)
      panel_line "将执行" "apt-get update"
      panel_line "将执行" "apt-get install -y ca-certificates curl"
      [ "$need_docker" = true ] && panel_line "Docker 命令" "apt-get install -y docker.io"
      [ "$need_compose" = true ] && panel_line "Compose 候选" "docker-compose-v2；失败则 docker-compose-plugin；再失败则 docker-compose"
      ;;
    dnf|yum)
      panel_line "将执行" "$1 install -y ca-certificates curl"
      [ "$need_docker" = true ] && panel_line "Docker 候选" "$1 install -y docker；失败则 moby-engine"
      [ "$need_compose" = true ] && panel_line "Compose 候选" "$1 install -y docker-compose-plugin；失败则 docker-compose"
      ;;
    apk|pacman|zypper)
      case "$1" in
        apk) plan_command="apk add"; docker_package=docker; compose_package=docker-cli-compose ;;
        pacman) plan_command="pacman -Syu --needed --noconfirm"; docker_package=docker; compose_package=docker-compose ;;
        zypper) plan_command="zypper --non-interactive install"; docker_package=docker; compose_package=docker-compose ;;
      esac
      plan_packages="ca-certificates curl"
      [ "$need_docker" = true ] && plan_packages="$plan_packages $docker_package"
      [ "$need_compose" = true ] && plan_packages="$plan_packages $compose_package"
      panel_line "将执行" "$plan_command $plan_packages"
      ;;
  esac
  return 0
}

compose_command_available() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return 0
  fi
  command -v docker-compose >/dev/null 2>&1
}

runtime_dependencies_present() {
  command -v docker >/dev/null 2>&1 && compose_command_available
}

start_docker_daemon() {
  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    run_as_root systemctl enable --now docker || return 1
  elif command -v rc-service >/dev/null 2>&1; then
    run_as_root rc-update add docker default >/dev/null 2>&1 || true
    run_as_root rc-service docker start || return 1
  elif command -v service >/dev/null 2>&1; then
    run_as_root service docker start || return 1
  else
    return 1
  fi
}

install_apt_dependencies() {
  run_as_root apt-get update || die "apt 软件索引更新失败"
  run_as_root apt-get install -y ca-certificates curl || die "基础软件安装失败"
  if ! command -v docker >/dev/null 2>&1; then
    run_as_root apt-get install -y docker.io || die "Docker 安装失败"
  fi
  if ! compose_command_available; then
    if run_as_root apt-get install -y docker-compose-v2; then
      return 0
    fi
    if run_as_root apt-get install -y docker-compose-plugin; then
      return 0
    fi
    run_as_root apt-get install -y docker-compose || die "Docker Compose 安装失败"
  fi
}

install_rpm_dependencies() {
  manager=$1
  run_as_root "$manager" install -y ca-certificates curl || die "基础软件安装失败"
  if ! command -v docker >/dev/null 2>&1; then
    if ! run_as_root "$manager" install -y docker; then
      run_as_root "$manager" install -y moby-engine || die "Docker 安装失败；当前软件源可能未提供相关软件包"
    fi
  fi
  if ! compose_command_available; then
    if run_as_root "$manager" install -y docker-compose-plugin; then
      return 0
    fi
    run_as_root "$manager" install -y docker-compose || die "Docker Compose 安装失败；当前软件源可能未提供相关软件包"
  fi
}

install_single_command_dependencies() {
  manager=$1
  docker_package=$2
  compose_package=$3
  packages="ca-certificates curl"
  command -v docker >/dev/null 2>&1 || packages="$packages $docker_package"
  compose_command_available || packages="$packages $compose_package"
  case "$manager" in
    apk) run_as_root apk add $packages || die "Docker/Compose 安装失败" ;;
    pacman) run_as_root pacman -Syu --needed --noconfirm $packages || die "Docker/Compose 安装失败" ;;
    zypper) run_as_root zypper --non-interactive install $packages || die "Docker/Compose 安装失败" ;;
  esac
}

install_system_dependencies() {
  package_manager=$(detect_package_manager) || die "不支持当前系统的软件包管理器；请手动安装 Docker 与 Docker Compose"
  os_name=$(detect_os_name)
  need_docker=false
  need_compose=false
  command -v docker >/dev/null 2>&1 || need_docker=true
  compose_command_available || need_compose=true
  missing_components="ca-certificates、curl"
  [ "$need_docker" = false ] || missing_components="${missing_components}、Docker"
  [ "$need_compose" = false ] || missing_components="${missing_components}、Docker Compose"
  panel_top "系统依赖安装 / System Dependencies"
  panel_line "检测到系统" "$os_name"
  panel_line "软件包管理器" "$package_manager"
  panel_line "将安装/确认" "$missing_components"
  show_dependency_install_plan "$package_manager"
  panel_line "安装来源" "当前 Linux 发行版的软件仓库"
  print_rule

  if ! is_truthy "$auto_install_deps"; then
    prompt_confirm "允许安装以上系统软件？" || die "已取消系统依赖安装"
  fi

  case "$package_manager" in
    apt-get) install_apt_dependencies ;;
    apk) install_single_command_dependencies apk docker docker-cli-compose ;;
    dnf|yum) install_rpm_dependencies "$package_manager" ;;
    pacman) install_single_command_dependencies pacman docker docker-compose ;;
    zypper) install_single_command_dependencies zypper docker docker-compose ;;
    *) die "不支持的软件包管理器: $package_manager" ;;
  esac

  if start_docker_daemon; then
    success_msg "Docker 服务已启动"
  else
    warn_msg "软件已安装，但无法自动启动 Docker；请按当前系统方式启动 docker 服务"
  fi
  runtime_dependencies_present || die "安装完成后仍未检测到 Docker Compose；请检查软件仓库和 PATH"
}

ensure_runtime_dependencies() {
  runtime_dependencies_present && return 0
  warn_msg "缺少 Docker 或 Docker Compose"
  if is_truthy "$auto_install_deps"; then
    install_system_dependencies
  elif is_interactive; then
    install_system_dependencies
  else
    die "请先安装 Docker/Compose，或使用 ./deploy.sh --install-deps 允许脚本安装系统依赖"
  fi
}

require_docker() {
  ensure_runtime_dependencies
  if ! docker info >/dev/null 2>&1; then
    try_start=false
    if is_truthy "$auto_install_deps"; then
      try_start=true
    elif is_interactive && prompt_confirm "Docker 当前不可用，是否尝试启动服务？"; then
      try_start=true
    fi
    if [ "$try_start" = true ]; then
      start_docker_daemon || warn_msg "无法自动启动 Docker 服务"
    fi
  fi
  if ! docker info >/dev/null 2>&1; then
    if [ "$(id -u)" -ne 0 ]; then
      current_user=${USER:-$(id -un 2>/dev/null || printf '%s' '<user>')}
      die "无法连接 Docker。请启动 Docker；若是权限问题，执行 sudo usermod -aG docker \"$current_user\" 后重新登录，或使用 root 运行"
    fi
    die "无法连接 Docker；请确认 Docker 服务已经启动"
  fi
  if docker compose version >/dev/null 2>&1; then
    compose() { docker compose "$@"; }
  elif command -v docker-compose >/dev/null 2>&1; then
    warn_msg "正在使用旧版 docker-compose，建议安装 Compose plugin"
    compose() { docker-compose "$@"; }
  else
    die "Docker Compose 未安装"
  fi
}

check_system_dependencies() {
  ensure_runtime_dependencies
  require_docker
  success_msg "Docker 与 Docker Compose 均可用"
}

detect_scrcpygate_instance() {
  EXISTING_SCRCPYGATE_STATE=""
  EXISTING_SCRCPYGATE_PROJECT=""
  EXISTING_SCRCPYGATE_SERVICE=""
  EXISTING_SCRCPYGATE_WORKING_DIR=""
  EXISTING_SCRCPYGATE_CONFIG_FILES=""
  EXISTING_SCRCPYGATE_IMAGE=""
  EXISTING_SCRCPYGATE_DATA_TYPE=""
  EXISTING_SCRCPYGATE_DATA_SOURCE=""
  command -v docker >/dev/null 2>&1 || return 0
  docker info >/dev/null 2>&1 || return 0
  EXISTING_SCRCPYGATE_STATE=$(docker inspect --format '{{.State.Status}}' scrcpygate 2>/dev/null | tr -d '\r' || true)
  [ -n "$EXISTING_SCRCPYGATE_STATE" ] || return 0
  EXISTING_SCRCPYGATE_PROJECT=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' scrcpygate 2>/dev/null | tr -d '\r' || true)
  EXISTING_SCRCPYGATE_SERVICE=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' scrcpygate 2>/dev/null | tr -d '\r' || true)
  EXISTING_SCRCPYGATE_WORKING_DIR=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' scrcpygate 2>/dev/null | tr -d '\r' || true)
  EXISTING_SCRCPYGATE_CONFIG_FILES=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' scrcpygate 2>/dev/null | tr -d '\r' || true)
  EXISTING_SCRCPYGATE_IMAGE=$(docker inspect --format '{{.Config.Image}}' scrcpygate 2>/dev/null | tr -d '\r' || true)
  EXISTING_SCRCPYGATE_DATA_TYPE=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Type}}{{end}}{{end}}' scrcpygate 2>/dev/null | tr -d '\r' || true)
  EXISTING_SCRCPYGATE_DATA_SOURCE=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' scrcpygate 2>/dev/null | tr -d '\r' || true)
}

show_existing_scrcpygate() {
  panel_top "检测到现有 ScrcpyGate 实例"
  panel_line "容器" "scrcpygate"
  panel_line "状态" "$EXISTING_SCRCPYGATE_STATE"
  panel_line "Compose 项目" "${EXISTING_SCRCPYGATE_PROJECT:-未知/手工创建}"
  panel_line "Compose 服务" "${EXISTING_SCRCPYGATE_SERVICE:-未知}"
  panel_line "工作目录" "${EXISTING_SCRCPYGATE_WORKING_DIR:-未知}"
  panel_line "数据挂载" "${EXISTING_SCRCPYGATE_DATA_SOURCE:-未知}"
  print_rule
}

existing_instance_can_be_managed() {
  [ -n "$EXISTING_SCRCPYGATE_STATE" ] || return 1
  expected_config="$SCRIPT_DIR/compose.yaml"
  ownership_error=""
  case "$EXISTING_SCRCPYGATE_PROJECT" in scrcpygate) ;; *) ownership_error="Compose 项目不匹配" ;;
  esac
  if [ -z "${ownership_error:-}" ] && [ "$EXISTING_SCRCPYGATE_SERVICE" != scrcpygate ]; then ownership_error="Compose 服务不匹配"; fi
  if [ -z "${ownership_error:-}" ] && [ "$EXISTING_SCRCPYGATE_WORKING_DIR" != "$SCRIPT_DIR" ]; then ownership_error="Compose 工作目录不匹配"; fi
  if [ -z "${ownership_error:-}" ] && [ "$EXISTING_SCRCPYGATE_CONFIG_FILES" != "$expected_config" ]; then ownership_error="Compose 配置文件不匹配"; fi
  managed_image=$(dotenv_value SCRCPYGATE_IMAGE)
  managed_image=${managed_image:-scrcpygate:local}
  if [ -z "${ownership_error:-}" ] && [ "$EXISTING_SCRCPYGATE_IMAGE" != "$managed_image" ]; then ownership_error="容器镜像不匹配"; fi
  if [ -z "${ownership_error:-}" ]; then
    if expected_data=$(configured_data_dir_from_disk); then
      if actual_data=$(existing_container_data_dir); then
        EXISTING_SCRCPYGATE_DATA_SOURCE=$actual_data
      else
        ownership_error="无法从磁盘配置解析数据目录"
      fi
    elif actual_data=$(existing_container_data_dir); then
      expected_data=$actual_data
      WEB_SCRCPY_DATA_HOST=$actual_data
      EXISTING_SCRCPYGATE_DATA_SOURCE=$actual_data
      success_msg "未找到可用的 .env 数据目录，已恢复现有容器挂载: $(safe_display "$actual_data")"
    elif expected_data=$(configured_data_path_from_disk) && actual_data=$(existing_container_data_path); then
      EXISTING_SCRCPYGATE_DATA_SOURCE=$actual_data
      if [ "$actual_data" = "$expected_data" ]; then
        warn_msg "数据目录不存在，将只移除服务容器并跳过数据清理: $(safe_display "$actual_data")"
      fi
    else
      ownership_error="无法从磁盘配置解析数据目录"
    fi
  fi
  if [ -z "${ownership_error:-}" ] && [ "$EXISTING_SCRCPYGATE_DATA_SOURCE" != "$expected_data" ]; then ownership_error="数据挂载目录不匹配"; fi
  if [ -n "${ownership_error:-}" ]; then
    show_existing_scrcpygate
    warn_msg "${ownership_error}，脚本不会自动接管；请人工确认该容器"
    ownership_error=""
    return 1
  fi
  ownership_error=""
  return 0
}

scrcpygate_container_identity_is_safe() {
  # The Compose project name is part of the ownership boundary.  A container
  # from another project must never be treated as an old ScrcpyGate instance
  # merely because its image or service happens to share a name.
  case "${EXISTING_SCRCPYGATE_PROJECT:-}" in
    scrcpygate) ;;
    *) return 1 ;;
  esac
  case "${EXISTING_SCRCPYGATE_IMAGE:-}" in
    scrcpygate:*) ;;
    *)
      identity_image=$(dotenv_value SCRCPYGATE_IMAGE)
      [ -n "$identity_image" ] && [ "$EXISTING_SCRCPYGATE_IMAGE" = "$identity_image" ] || return 1
      ;;
  esac
  case "${EXISTING_SCRCPYGATE_SERVICE:-}" in
    ""|scrcpygate) ;;
    *) return 1 ;;
  esac
  return 0
}

scrcpygate_container_matches_current_project() {
  [ -n "${EXISTING_SCRCPYGATE_STATE:-}" ] || return 1
  [ "${EXISTING_SCRCPYGATE_PROJECT:-}" = scrcpygate ] || return 1
  [ "${EXISTING_SCRCPYGATE_SERVICE:-}" = scrcpygate ] || return 1
  [ "${EXISTING_SCRCPYGATE_WORKING_DIR:-}" = "$SCRIPT_DIR" ] || return 1
  current_project_image=$(dotenv_value SCRCPYGATE_IMAGE)
  [ "${EXISTING_SCRCPYGATE_IMAGE:-}" = "${current_project_image:-scrcpygate:local}" ] || return 1
  return 0
}

prepare_existing_container_for_install() {
  [ -n "${EXISTING_SCRCPYGATE_STATE:-}" ] || return 0
  if existing_instance_can_be_managed; then
    return 0
  fi
  # An explicitly confirmed, recognizable old ScrcpyGate container may be
  # removed so an install from this directory can continue.  Unrecognized
  # containers are never taken over automatically.
  scrcpygate_container_identity_is_safe || return 1
  if ! is_interactive; then
    warn_msg "检测到未归属当前项目的旧 scrcpygate 容器；非交互安装不会停止或删除它"
    return 1
  fi
  if ! prompt_confirm_no "是否停止并移除该旧 scrcpygate 容器后继续安装？数据目录和镜像会保留"; then
    warn_msg "已保留旧 scrcpygate 容器，安装已取消"
    return 1
  fi
  remove_scrcpygate_container_only || return 1
  return 0
}

detect_port_occupancy() {
  PORT_OCCUPANCY_STATUS=unavailable
  PORT_OCCUPANCY_TOOL=""
  PORT_OCCUPANCY_DETAILS=""
  PORT_OCCUPANCY_PIDS=""
  PORT_OCCUPANCY_CONTAINERS=""
  port_suffix=":${WEB_SCRCPY_PORT}"

  if command -v ss >/dev/null 2>&1; then
    ss_output=""
    ss_status=0
    ss_output=$(ss -H -ltnp 2>/dev/null) || ss_status=$?
    if [ "$ss_status" -eq 0 ]; then
      PORT_OCCUPANCY_STATUS=available
      PORT_OCCUPANCY_TOOL=ss
      PORT_OCCUPANCY_DETAILS=$(printf '%s\n' "$ss_output" \
        | awk -v suffix="$port_suffix" '$4 ~ suffix "$" {print}' || true)
      PORT_OCCUPANCY_PIDS=$(printf '%s\n' "$PORT_OCCUPANCY_DETAILS" \
        | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u || true)
      return 0
    fi
  fi

  if command -v lsof >/dev/null 2>&1; then
    lsof_output=""
    lsof_status=0
    lsof_output=$(lsof -nP -iTCP:"$WEB_SCRCPY_PORT" -sTCP:LISTEN 2>/dev/null) || lsof_status=$?
    if [ "$lsof_status" -eq 0 ]; then
      # BusyBox lsof accepts only a subset of the upstream flags and may return
      # every open descriptor.  Filter again by TCP, the exact port and LISTEN
      # so those descriptors cannot be mistaken for a port occupant.
      PORT_OCCUPANCY_DETAILS=$(printf '%s\n' "$lsof_output" \
        | awk -v port="$WEB_SCRCPY_PORT" 'NR > 1 && /TCP/ && /LISTEN/ && $0 ~ (":" port "([^0-9]|$)") {print}' || true)
      lsof_has_rows=$(printf '%s\n' "$lsof_output" | awk 'NR > 1 {print "yes"; exit}')
      if [ -z "$lsof_has_rows" ] || [ -n "$PORT_OCCUPANCY_DETAILS" ]; then
        PORT_OCCUPANCY_STATUS=available
        PORT_OCCUPANCY_TOOL=lsof
        PORT_OCCUPANCY_PIDS=$(printf '%s\n' "$PORT_OCCUPANCY_DETAILS" \
          | awk 'NF {print $2}' | sort -u || true)
        return 0
      fi
    fi
    # BusyBox lsof may ignore the filtering flags and return every open file.
    # Continue to a netstat fallback instead of treating that output as
    # authoritative or declaring the port occupied.
    PORT_OCCUPANCY_TOOL="lsof (无法解析监听行)"
    PORT_OCCUPANCY_DETAILS=""
  fi

  if command -v netstat >/dev/null 2>&1; then
    netstat_output=""
    netstat_status=0
    netstat_output=$(netstat -ltnp 2>/dev/null) || netstat_status=$?
    if [ "$netstat_status" -ne 0 ]; then
      netstat_status=0
      netstat_output=$(netstat -ltn 2>/dev/null) || netstat_status=$?
    fi
    if [ "$netstat_status" -eq 0 ]; then
      # Require the POSIX/Linux netstat table shape.  This avoids treating a
      # host-only command such as Windows netstat.exe as an authoritative
      # empty result when a later BusyBox applet can provide the real answer.
      netstat_header=$(printf '%s\n' "$netstat_output" \
        | awk '/Local Address/ {print "yes"; exit}')
      if [ -n "$netstat_header" ]; then
        PORT_OCCUPANCY_STATUS=available
        PORT_OCCUPANCY_TOOL=netstat
        PORT_OCCUPANCY_DETAILS=$(printf '%s\n' "$netstat_output" \
          | awk -v suffix="$port_suffix" 'NR > 1 && $6 == "LISTEN" && $4 ~ (suffix "$") {print}' || true)
        PORT_OCCUPANCY_PIDS=$(printf '%s\n' "$PORT_OCCUPANCY_DETAILS" \
          | sed -n 's/.*[[:space:]]\([0-9][0-9]*\)\/[^[:space:]]*[[:space:]]*$/\1/p' | sort -u || true)
        return 0
      fi
    fi
  fi

  # Minimal Alpine hosts often expose BusyBox only as a single binary, without
  # a `netstat` symlink.  Its netstat applet is sufficient to identify a
  # listener even when the BusyBox lsof applet cannot filter descriptors.
  if command -v busybox >/dev/null 2>&1; then
    busybox_netstat_output=""
    busybox_netstat_status=0
    busybox_netstat_output=$(busybox netstat -ltnp 2>/dev/null) || busybox_netstat_status=$?
    if [ "$busybox_netstat_status" -ne 0 ]; then
      busybox_netstat_status=0
      busybox_netstat_output=$(busybox netstat -ltn 2>/dev/null) || busybox_netstat_status=$?
    fi
    if [ "$busybox_netstat_status" -eq 0 ]; then
      busybox_netstat_header=$(printf '%s\n' "$busybox_netstat_output" \
        | awk '/Local Address/ {print "yes"; exit}')
      if [ -n "$busybox_netstat_header" ]; then
        PORT_OCCUPANCY_STATUS=available
        PORT_OCCUPANCY_TOOL="busybox netstat"
        PORT_OCCUPANCY_DETAILS=$(printf '%s\n' "$busybox_netstat_output" \
          | awk -v suffix="$port_suffix" 'NR > 1 && $6 == "LISTEN" && $4 ~ (suffix "$") {print}' || true)
        PORT_OCCUPANCY_PIDS=$(printf '%s\n' "$PORT_OCCUPANCY_DETAILS" \
          | sed -n 's/.*[[:space:]]\([0-9][0-9]*\)\/[^[:space:]]*[[:space:]]*$/\1/p' | sort -u || true)
        return 0
      fi
    fi
  fi
  return 0
}

detect_port_occupancy_containers() {
  PORT_OCCUPANCY_CONTAINERS=""
  command -v docker >/dev/null 2>&1 || return 0
  PORT_OCCUPANCY_CONTAINERS=$(docker ps --format '{{.ID}}\t{{.Names}}\t{{.Ports}}' \
    --filter "publish=${WEB_SCRCPY_PORT}" 2>/dev/null || true)
}

wait_for_port_release() {
  for port_wait in 1 2 3 4 5; do
    detect_port_occupancy
    case "$PORT_OCCUPANCY_STATUS" in
      unavailable) return 0 ;;
      available)
        [ -z "$PORT_OCCUPANCY_DETAILS" ] && return 0
        ;;
    esac
    sleep 1
  done
  detect_port_occupancy
  [ "$PORT_OCCUPANCY_STATUS" != available ] || [ -z "$PORT_OCCUPANCY_DETAILS" ]
}

show_port_occupancy() {
  panel_top "监听端口检查"
  panel_line "目标" "${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT}"
  case "$PORT_OCCUPANCY_STATUS" in
    unavailable)
      warn_msg "系统未提供 ss、lsof 或 netstat，无法可靠读取端口占用；Compose 启动失败时将立即返回"
      ;;
    available)
      panel_line "探测工具" "$PORT_OCCUPANCY_TOOL"
      if [ -n "$PORT_OCCUPANCY_DETAILS" ]; then
        printf '%s\n' "$PORT_OCCUPANCY_DETAILS" | while IFS= read -r port_line; do
          [ -n "$port_line" ] || continue
          panel_line "监听" "$port_line"
        done
        if [ -n "$PORT_OCCUPANCY_CONTAINERS" ]; then
          printf '%s\n' "$PORT_OCCUPANCY_CONTAINERS" | while IFS= read -r container_line; do
            [ -n "$container_line" ] || continue
            panel_line "容器" "$container_line"
          done
        fi
      else
        success_msg "目标端口当前未发现监听者"
      fi
      ;;
  esac
  print_rule
}

remove_scrcpygate_container_only() {
  [ -n "${EXISTING_SCRCPYGATE_STATE:-}" ] || return 0
  scrcpygate_container_identity_is_safe || {
    warn_msg "容器镜像或 Compose 服务标识不匹配，脚本不会自动删除 scrcpygate；请人工确认"
    return 1
  }
  if [ "$EXISTING_SCRCPYGATE_STATE" = running ] \
    || [ "$EXISTING_SCRCPYGATE_STATE" = restarting ] \
    || [ "$EXISTING_SCRCPYGATE_STATE" = paused ]; then
    if ! docker stop --time 15 scrcpygate; then
      warn_msg "无法正常停止旧 scrcpygate 容器"
      return 1
    fi
  fi
  if docker inspect scrcpygate >/dev/null 2>&1; then
    docker rm scrcpygate || {
      warn_msg "无法移除旧 scrcpygate 容器；未删除数据目录或镜像"
      return 1
    }
  fi
  EXISTING_SCRCPYGATE_STATE=""
  success_msg "旧 scrcpygate 容器已停止并移除（数据目录和镜像均保留）"
  return 0
}

stop_port_occupants() {
  detect_port_occupancy_containers
  container_count=$(printf '%s\n' "$PORT_OCCUPANCY_CONTAINERS" | awk 'NF {count++} END {print count + 0}')
  if [ "$container_count" -gt 1 ]; then
    warn_msg "端口由多个 Docker 容器发布，脚本不会批量停止；请先确认具体容器"
    return 1
  fi
  if [ "$container_count" -eq 1 ]; then
    container_name=$(printf '%s\n' "$PORT_OCCUPANCY_CONTAINERS" | awk 'NF {print $2; exit}')
    case "$container_name" in
      ""|*[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-]*)
        warn_msg "无法安全解析端口占用容器名称；请手工停止占用者"
        return 1
        ;;
    esac
    if [ "$container_name" = scrcpygate ]; then
      detect_scrcpygate_instance
      if ! scrcpygate_container_identity_is_safe; then
        warn_msg "scrcpygate 容器标识不匹配，脚本不会自动停止或删除；请先人工确认"
        return 1
      fi
      if ! prompt_confirm_no "端口由旧 scrcpygate 容器占用，是否停止并移除该容器？数据目录和镜像会保留"; then
        return 1
      fi
      remove_scrcpygate_container_only
    else
      if ! prompt_confirm_no "端口由 Docker 容器 ${container_name} 占用，是否停止该容器（不会删除）？"; then
        return 1
      fi
      docker stop --time 15 "$container_name" || {
        warn_msg "无法停止 Docker 容器 ${container_name}"
        return 1
      }
      success_msg "已停止端口占用容器 ${container_name}"
    fi
    if wait_for_port_release; then
      success_msg "端口占用已释放"
      return 0
    fi
    warn_msg "停止容器后端口仍被占用；未自动强制终止其他进程"
    return 1
  fi

  pid_count=$(printf '%s\n' "$PORT_OCCUPANCY_PIDS" | awk 'NF {count++} END {print count + 0}')
  if [ "$pid_count" -ne 1 ]; then
    warn_msg "无法安全解析唯一监听进程 PID；请手工停止端口占用者"
    return 1
  fi
  port_pid=$(printf '%s\n' "$PORT_OCCUPANCY_PIDS" | awk 'NF {print; exit}')
  case "$port_pid" in
    ''|*[!0-9]*|0|1|$$)
      warn_msg "监听进程 PID 不允许由部署脚本终止：$port_pid"
      return 1
      ;;
  esac
  port_process=$(ps -p "$port_pid" -o comm= 2>/dev/null | tr -d '\r' | awk 'NF {print $1; exit}' || true)
  [ -n "$port_process" ] || {
    warn_msg "无法读取端口监听进程名称；请手工停止 PID $port_pid"
    return 1
  }
  case "$port_process" in
    docker-proxy|dockerd|containerd|systemd|init)
      warn_msg "监听者是系统/Docker 进程 ${port_process}（PID ${port_pid}），脚本不会直接终止；请停止对应容器或服务"
      return 1
      ;;
  esac
  panel_line "监听进程" "${port_process} (PID ${port_pid})"
  if ! prompt_confirm_no "是否向该监听进程发送 TERM 停止信号？"; then
    return 1
  fi
  kill -TERM "$port_pid" || {
    warn_msg "无法停止监听进程 PID $port_pid"
    return 1
  }
  for port_wait in 1 2 3 4 5; do
    sleep 1
    detect_port_occupancy
    if [ -z "$PORT_OCCUPANCY_DETAILS" ]; then
      success_msg "端口占用进程已停止"
      return 0
    fi
  done
  warn_msg "发送 TERM 后端口仍被占用；未自动发送 KILL，请手工确认"
  return 1
}

ensure_startup_conflicts() {
  detect_scrcpygate_instance
  if [ -n "${EXISTING_SCRCPYGATE_STATE:-}" ] && ! scrcpygate_container_matches_current_project; then
    show_existing_scrcpygate
    if ! scrcpygate_container_identity_is_safe; then
      die "检测到未确认归属的旧 scrcpygate 容器；请使用菜单中的冲突检查人工确认"
    fi
    if ! is_interactive || ! prompt_confirm_no "检测到旧 scrcpygate 容器，是否停止并移除后继续启动？数据目录和镜像会保留"; then
      die "已保留旧 scrcpygate 容器，启动已取消"
    fi
    remove_scrcpygate_container_only || die "旧 scrcpygate 容器未能移除，启动已取消"
  fi

  detect_port_occupancy
  case "$PORT_OCCUPANCY_STATUS" in
    unavailable)
      warn_msg "无法使用系统工具检测 ${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT}；将交由 Compose 检查，失败时不会继续等待健康检查"
      return 0
      ;;
    available)
      [ -n "$PORT_OCCUPANCY_DETAILS" ] || return 0
      detect_port_occupancy_containers
      case "${EXISTING_SCRCPYGATE_STATE:-}" in
        running|restarting)
          if scrcpygate_container_matches_current_project; then
            return 0
          fi
          ;;
      esac
      show_port_occupancy
      if ! is_interactive || ! stop_port_occupants; then
        die "端口 ${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT} 仍被占用；请停止占用者或修改 WEB_SCRCPY_PORT"
      fi
      ;;
  esac
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

prepare_readonly_deployment() {
  [ -f .env ] || die "只读运维命令需要现有 .env；不会自动创建或修改配置"
  load_settings
  reject_persisted_secrets
  DEPLOY_SETTINGS_READ_ONLY=true
  validate_settings
  DEPLOY_SETTINGS_READ_ONLY=false
  require_docker
  compose config >/dev/null || die "compose.yaml 或 .env 配置无效"
}

prepare_data_directory() {
  if [ ! -d "$WEB_SCRCPY_DATA_HOST" ]; then
    mkdir -p "$WEB_SCRCPY_DATA_HOST" || die "无法创建数据目录: $WEB_SCRCPY_DATA_HOST"
  fi
  DATA_DIR=$(CDPATH= cd -- "$WEB_SCRCPY_DATA_HOST" && pwd -P)
  case "$DATA_DIR" in '/'|"$SCRIPT_DIR"|"${HOME:-}") die "拒绝使用不安全的数据目录: $DATA_DIR" ;; esac
  case "$SCRIPT_DIR/" in "$DATA_DIR/"*) die "拒绝使用项目父目录作为数据目录" ;; esac
  # 幂等收紧：数据目录含数据库（会话/令牌/审计），无论是否新建都不应组/其他可读。
  chmod 700 "$DATA_DIR" 2>/dev/null || warn_msg "无法限制数据目录权限"
}

generate_persisted_alas_token_key() {
  generated_key=""
  if command -v openssl >/dev/null 2>&1; then
    generated_key=$(openssl rand -hex 32 2>/dev/null || true)
  fi
  if [ -z "$generated_key" ]; then
    key_generator=$(python_command 2>/dev/null || true)
    if [ -n "$key_generator" ]; then
      generated_key=$(
        "$key_generator" -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null || true
      )
    fi
  fi
  if [ -z "$generated_key" ] && command -v od >/dev/null 2>&1; then
    generated_key=$(od -An -N32 -tx1 /dev/urandom 2>/dev/null | tr -d '[:space:]' || true)
  fi
  valid_token_encryption_key_value "$generated_key" || return 1
  printf '%s\n' "$generated_key"
}

existing_database_needs_alas_key() {
  # Inspect only the stored format, without bootstrapping or exposing a token.
  # SQLite mode=ro keeps the database read-only, but WAL readers may need to
  # create sidecars. Run as the app user on the writable data mount, not root.
  # Docker failures must never be confused with an empty credential.
  key_probe_result=$(docker run --rm --network none --read-only \
    -v "$DATA_DIR:/app/data" --entrypoint python scrcpygate:local -c '
import sqlite3, sys
try:
    conn = sqlite3.connect("file:/app/data/webscrcpy.db?mode=ro", uri=True)
    row = conn.execute("SELECT value FROM settings WHERE key=?", ("alas_token",)).fetchone()
    needs_key = bool(row and str(row[0] or "").startswith("v1:"))
    conn.close()
except Exception:
    sys.exit(2)
print("encrypted" if needs_key else "clear")
' 2>/dev/null) || return 2
  case "$key_probe_result" in
    encrypted) return 0 ;;
    clear) return 1 ;;
    *) return 2 ;;
  esac
}

confirm_reset_alas_token_without_key() {
  if ! is_interactive; then
    die "旧数据库含加密 ALAS Token，但密钥文件缺失。请恢复配套密钥或注入原密钥；确认无法找回时，可执行 --clear-alas-token 后重装并重新填写 Token。非交互安装不会自动重置"
  fi
  warn_msg "旧数据库含加密 ALAS Token，但密钥文件缺失；优先恢复原密钥可以保留现有 Token"
  warn_msg "强行重置只清空保存的 ALAS Token（含旧配置中的 Token），并生成新密钥；账号、密码、设备和其他配置保留。安装后需重新填写 ALAS Token"
  if ! prompt_confirm_no "是否强行重置 ALAS Token 并继续安装？"; then
    die "已取消重置，原 Token 保留；请恢复配套密钥后重新安装"
  fi
  if [ -e "$DATA_DIR/.alas-token-encryption-key" ] || [ -L "$DATA_DIR/.alas-token-encryption-key" ]; then
    die "密钥文件状态已变化，未清空 Token；请重新安装以检查当前密钥"
  fi
  # Target the data directory just inspected, not a possibly stale running
  # container. Do not initialize the database or replay startup side effects.
  if ! docker run --rm --network none --read-only \
    -v "$DATA_DIR:/app/data" --entrypoint python scrcpygate:local -c '
from app import storage, storage_core
with storage_core.StartupLock():
    storage.clear_alas_token()
    if storage.get_setting("alas_token") or storage._legacy_env_has_alas_token():
        raise SystemExit(1)
' >/dev/null 2>&1; then
    die "ALAS Token 重置失败，未生成新密钥；请检查数据目录后重试"
  fi
  success_msg "已重置 ALAS Token，正在生成新密钥；安装后请在 ALAS 设置中重新填写 Token"
}

ensure_alas_token_key() {
  # Explicit Secret Manager injection remains the strongest source and is
  # never copied to disk by this script.
  if [ -n "${ALAS_TOKEN_ENCRYPTION_KEY:-}" ]; then
    valid_token_encryption_key || die "ALAS_TOKEN_ENCRYPTION_KEY 必须解码为 32 字节"
    return 0
  fi

  key_file="$DATA_DIR/.alas-token-encryption-key"
  if [ -L "$key_file" ]; then
    die "ALAS Token 密钥文件不能是符号链接；请恢复与数据库配套的普通密钥文件"
  fi
  if [ -e "$key_file" ]; then
    [ -f "$key_file" ] || die "ALAS Token 密钥路径不是普通文件"
    key_file_value=$(cat "$key_file" 2>/dev/null) || die "无法读取 ALAS Token 密钥文件"
    valid_token_encryption_key_value "$key_file_value" \
      || die "ALAS Token 密钥文件无效；请恢复匹配的密钥备份，不要删除后重新生成"
    chmod 600 "$key_file" 2>/dev/null \
      || die "无法限制 ALAS Token 密钥文件权限（需要仅文件所有者可读）"
    success_msg "已复用服务器侧 ALAS Token 加密密钥"
    return 0
  fi

  if [ -e "$DATA_DIR/webscrcpy.db" ]; then
    ensure_data_permissions
    key_probe_status=0
    existing_database_needs_alas_key || key_probe_status=$?
    case "$key_probe_status" in
      0) confirm_reset_alas_token_without_key ;;
      1) ;;
      *) die "无法只读检查旧数据库，未生成新密钥；请检查数据库和本地镜像后重试" ;;
    esac
  fi

  TOKEN_KEY_TMP_FILE=$(mktemp "$DATA_DIR/.alas-token-encryption-key.XXXXXX") \
    || { TOKEN_KEY_TMP_FILE=""; die "无法创建 ALAS Token 密钥临时文件"; }
  chmod 600 "$TOKEN_KEY_TMP_FILE" 2>/dev/null \
    || { rm -f "$TOKEN_KEY_TMP_FILE"; TOKEN_KEY_TMP_FILE=""; die "无法限制 ALAS Token 密钥临时文件权限"; }
  generated_key=$(generate_persisted_alas_token_key) \
    || { rm -f "$TOKEN_KEY_TMP_FILE"; TOKEN_KEY_TMP_FILE=""; die "无法生成 ALAS Token 加密密钥"; }
  if ! printf '%s\n' "$generated_key" > "$TOKEN_KEY_TMP_FILE"; then
    rm -f "$TOKEN_KEY_TMP_FILE"
    TOKEN_KEY_TMP_FILE=""
    die "无法写入 ALAS Token 密钥文件"
  fi
  if ! mv "$TOKEN_KEY_TMP_FILE" "$key_file"; then
    rm -f "$TOKEN_KEY_TMP_FILE"
    TOKEN_KEY_TMP_FILE=""
    die "无法保存 ALAS Token 密钥文件"
  fi
  TOKEN_KEY_TMP_FILE=""
  generated_key=""
  success_msg "已生成服务器侧 ALAS Token 加密密钥（仅保存于受保护数据目录）"
}

has_global_ipv6() {
  if command -v ip >/dev/null 2>&1; then
    if ip -6 addr show scope global up 2>/dev/null | awk '/inet6[[:space:]]/ { found = 1 } END { exit(found ? 0 : 1) }'; then
      return 0
    fi
    if ip -6 route show default 2>/dev/null | awk '/^default[[:space:]]/ { found = 1 } END { exit(found ? 0 : 1) }'; then
      return 0
    fi
  fi
  # Minimal hosts (including BusyBox images) may not ship iproute2.  The
  # kernel interface still exposes IPv6 addresses through this proc file;
  # the Registry probe below remains the final connectivity check.
  if [ -r /proc/net/if_inet6 ] && awk '
    # Ignore the loopback address and link-local-only entries.  A successful
    # curl -6 probe is required before the result is used for image pulls.
    $1 !~ /^0{31}1$/ && $4 == "00" { found = 1 }
    END { exit(found ? 0 : 1) }
  ' /proc/net/if_inet6 2>/dev/null; then
    return 0
  fi
  return 1
}

registry_probe_url() {
  REGISTRY_PROBE_IMAGE=$1
  REGISTRY_PROBE_IMAGE=${REGISTRY_PROBE_IMAGE%%@*}
  REGISTRY_PROBE_HOST=${REGISTRY_PROBE_IMAGE%%/*}
  case "$REGISTRY_PROBE_IMAGE" in
    */*)
      case "$REGISTRY_PROBE_HOST" in
        *.*|*:*|localhost) printf 'https://%s/v2/\n' "$REGISTRY_PROBE_HOST" ;;
        *) printf '%s\n' 'https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull' ;;
      esac
      ;;
    *) printf '%s\n' 'https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull' ;;
  esac
}

probe_registry_family() {
  PROBE_IMAGE=$1
  PROBE_FAMILY=$2
  case "$PROBE_FAMILY" in
    4|6) ;;
    *) return 1 ;;
  esac
  command -v curl >/dev/null 2>&1 || return 1
  PROBE_URL=$(registry_probe_url "$PROBE_IMAGE")
  PROBE_CODE=$(curl "-$PROBE_FAMILY" -sS -o /dev/null -w '%{http_code}' \
    --connect-timeout "$SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS" \
    --max-time "$SCRCPYGATE_REGISTRY_CONNECT_TIMEOUT_SECONDS" \
    "$PROBE_URL" 2>/dev/null || printf '%s' 000)
  case "$PROBE_CODE" in
    2??|3??|401|403) return 0 ;;
    *) return 1 ;;
  esac
}

select_registry_ip_family() {
  REGISTRY_IP_FAMILY_SELECTED=4
  case "$SCRCPYGATE_REGISTRY_IP_FAMILY" in
    ipv4)
      log "基础镜像网络策略: 使用 IPv4"
      ;;
    ipv6)
      if has_global_ipv6 && probe_registry_family "$PYTHON_IMAGE" 6; then
        REGISTRY_IP_FAMILY_SELECTED=6
        log "基础镜像网络策略: IPv6 可用，优先使用 IPv6"
      else
        warn_msg "IPv6 地址或 Docker Registry 连接不可用/超时，切换 IPv4"
        if probe_registry_family "$PYTHON_IMAGE" 4; then
          log "基础镜像网络策略: 已切换 IPv4"
        else
          warn_msg "IPv4 探测也未通过，将继续尝试配置的镜像候选"
        fi
      fi
      ;;
    auto)
      if has_global_ipv6 && probe_registry_family "$PYTHON_IMAGE" 6; then
        REGISTRY_IP_FAMILY_SELECTED=6
        log "基础镜像网络策略: 检测到可用 IPv6，优先使用 IPv6"
      else
        if has_global_ipv6; then
          warn_msg "检测到 IPv6，但 Docker Registry IPv6 连接超时，切换 IPv4"
        else
          log "未检测到可用全局 IPv6，使用 IPv4 探测"
        fi
        if probe_registry_family "$PYTHON_IMAGE" 4; then
          log "基础镜像网络策略: 使用 IPv4"
        else
          warn_msg "Docker Hub IPv4 探测未通过，将按顺序尝试国内镜像候选"
        fi
      fi
      ;;
  esac
}

probe_candidate_registry() {
  CANDIDATE_IMAGE=$1
  command -v curl >/dev/null 2>&1 || return 0
  if [ "$REGISTRY_IP_FAMILY_SELECTED" = 6 ] && probe_registry_family "$CANDIDATE_IMAGE" 6; then
    return 0
  fi
  if probe_registry_family "$CANDIDATE_IMAGE" 4; then
    if [ "$REGISTRY_IP_FAMILY_SELECTED" = 6 ]; then
      warn_msg "镜像源 IPv6 连接超时，改用 IPv4 探测: $(safe_display "$CANDIDATE_IMAGE")"
      REGISTRY_IP_FAMILY_SELECTED=4
    fi
    return 0
  fi
  warn_msg "镜像源连通性探测未通过，仍尝试 Docker 拉取: $(safe_display "$CANDIDATE_IMAGE")"
  return 0
}

run_docker_pull() {
  PULL_IMAGE=$1
  if command -v timeout >/dev/null 2>&1; then
    # GNU/coreutils timeout already forwards termination to the foreground
    # Docker client, so retaining this synchronous path avoids an extra shell
    # wait on hosts such as Git Bash while still bounding the pull.
    timeout "$SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS" docker pull --quiet "$PULL_IMAGE" >/dev/null 2>&1
    return $?
  fi

  # POSIX fallback for hosts without coreutils `timeout` (for example a
  # minimal BusyBox installation).  Killing the Docker CLI cancels its
  # Engine request; the daemon will not be used by the next candidate.
  docker pull --quiet "$PULL_IMAGE" >/dev/null 2>&1 &
  PULL_PID=$!
  PULL_ELAPSED=0
  while kill -0 "$PULL_PID" 2>/dev/null; do
    if [ "$PULL_ELAPSED" -ge "$SCRCPYGATE_IMAGE_PULL_TIMEOUT_SECONDS" ]; then
      kill "$PULL_PID" 2>/dev/null || true
      wait "$PULL_PID" 2>/dev/null || true
      PULL_PID=""
      return 124
    fi
    sleep 1
    PULL_ELAPSED=$((PULL_ELAPSED + 1))
  done
  if wait "$PULL_PID"; then
    pull_status=0
  else
    pull_status=$?
  fi
  PULL_PID=""
  return "$pull_status"
}

pull_python_image() {
  PULL_IMAGE=$1
  if [ "$REGISTRY_IP_FAMILY_SELECTED" = 6 ]; then
    if run_docker_pull "$PULL_IMAGE"; then
      return 0
    fi
    # Docker Engine does not expose a per-command address-family switch.  A
    # failed IPv6 attempt is therefore followed by an explicit IPv4 probe and
    # a fresh pull request before moving on to a mirror candidate.
    warn_msg "基础镜像 IPv6 拉取超时/失败，改用 IPv4 重试: $(safe_display "$PULL_IMAGE")"
    REGISTRY_IP_FAMILY_SELECTED=4
    probe_registry_family "$PULL_IMAGE" 4 || true
    run_docker_pull "$PULL_IMAGE"
    return $?
  fi
  run_docker_pull "$PULL_IMAGE"
}

select_python_image() {
  [ "$skip_build" = true ] && return 0

  # A cached base image is already the selected source.  Avoid a network
  # probe unless the operator explicitly requested --pull; missing images and
  # explicit pulls still use the IPv6/IPv4 and mirror fallback below.
  if [ "$pull_images" != true ] && docker image inspect "$PYTHON_IMAGE" >/dev/null 2>&1; then
    REGISTRY_IP_FAMILY_SELECTED=4
    export PYTHON_IMAGE
    log "复用本地基础镜像: $(safe_display "$PYTHON_IMAGE")"
    return 0
  fi

  select_registry_ip_family
  candidate_list=$PYTHON_IMAGE
  case "$PYTHON_IMAGE" in
    "$DEFAULT_PYTHON_IMAGE"|docker.io/library/python:3.12-alpine)
      [ -n "$SCRCPYGATE_PYTHON_IMAGE_MIRRORS" ] && candidate_list="$candidate_list,$SCRCPYGATE_PYTHON_IMAGE_MIRRORS"
      ;;
    *)
      # A custom image must not silently fall back to a different base image.
      # Set an explicit mirror list when that behavior is intentional.
      if [ -n "$SCRCPYGATE_PYTHON_IMAGE_MIRRORS" ] && [ "$SCRCPYGATE_PYTHON_IMAGE_MIRRORS" != "$DEFAULT_PYTHON_IMAGE_MIRRORS" ]; then
        candidate_list="$candidate_list,$SCRCPYGATE_PYTHON_IMAGE_MIRRORS"
      fi
      ;;
  esac
  saved_ifs=$IFS
  IFS=,
  for candidate in $candidate_list; do
    candidate=$(printf '%s' "$candidate" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -n "$candidate" ] || continue
    if [ "$pull_images" != true ] && docker image inspect "$candidate" >/dev/null 2>&1; then
      PYTHON_IMAGE=$candidate
      export PYTHON_IMAGE
      IFS=$saved_ifs
      log "复用本地基础镜像: $(safe_display "$candidate")"
      return 0
    fi
    probe_candidate_registry "$candidate"
    log "正在获取基础镜像: $(safe_display "$candidate")"
    if pull_python_image "$candidate"; then
      if [ "$candidate" != "$PYTHON_IMAGE" ]; then
        warn_msg "Docker Hub 不可用，已切换基础镜像源: $(safe_display "$candidate")"
      fi
      PYTHON_IMAGE=$candidate
      export PYTHON_IMAGE
      IFS=$saved_ifs
      return 0
    fi
    warn_msg "基础镜像拉取失败: $(safe_display "$candidate")"
  done
  IFS=$saved_ifs
  die "无法获取 Python 基础镜像；请检查网络、IPv4/IPv6 路由或设置 SCRCPYGATE_PYTHON_IMAGE_MIRRORS"
}

build_image() {
  if [ "$skip_build" = true ]; then
    docker image inspect scrcpygate:local >/dev/null 2>&1 || die "scrcpygate:local 不存在，请不要使用 --skip-build"
    success_msg "复用镜像 scrcpygate:local"
  elif [ "$pull_images" = true ]; then
    select_python_image
    log "正在更新基础镜像并构建 ScrcpyGate..."
    if ! compose build --pull; then
      error_msg "镜像构建失败；已收集诊断信息"
      show_diagnostics
      die "镜像构建失败；请检查上方构建日志"
    fi
  else
    select_python_image
    log "正在构建 ScrcpyGate..."
    if ! compose build; then
      error_msg "镜像构建失败；已收集诊断信息"
      show_diagnostics
      die "镜像构建失败；请检查上方构建日志"
    fi
  fi
}

ensure_data_permissions() {
  app_uid=""
  app_gid=""
  id_output=$(docker run --rm --entrypoint id scrcpygate:local 2>/dev/null) || true
  if [ -n "$id_output" ]; then
    app_uid=$(printf '%s\n' "$id_output" | sed -n 's/.*uid=\([0-9]*\).*/\1/p' | head -1)
    app_gid=$(printf '%s\n' "$id_output" | sed -n 's/.*gid=\([0-9]*\).*/\1/p' | head -1)
  fi
  if [ -z "$app_uid" ] || [ -z "$app_gid" ]; then
    # 兼容无法解析 id 输出的环境, 回退到分别查询。
    app_uid=$(docker run --rm --entrypoint id scrcpygate:local -u 2>/dev/null) || die "无法读取容器 UID"
    app_gid=$(docker run --rm --entrypoint id scrcpygate:local -g 2>/dev/null) || die "无法读取容器 GID"
  fi

  # A pre-existing data directory can be writable by the container user while
  # a newly provisioned key file is still root-owned (0600).  Check the key
  # separately so a directory-level success cannot leave the runtime unable to
  # decrypt or update the web-managed Runtime Token.
  data_permissions_need_fix=false
  if ! docker run --rm -v "$DATA_DIR:/app/data" --entrypoint sh scrcpygate:local -c 'test -w /app/data' >/dev/null 2>&1; then
    data_permissions_need_fix=true
  fi
  key_file="$DATA_DIR/.alas-token-encryption-key"
  key_permissions_need_fix=false
  if [ -f "$key_file" ] && ! docker run --rm -v "$DATA_DIR:/app/data" --entrypoint sh scrcpygate:local -c 'test -r /app/data/.alas-token-encryption-key' >/dev/null 2>&1; then
    key_permissions_need_fix=true
  fi

  if [ "$data_permissions_need_fix" = true ]; then
    log "正在修复数据目录权限（容器 UID ${app_uid}:${app_gid}）..."
    docker run --rm --user 0 -v "$DATA_DIR:/app/data" --entrypoint chown scrcpygate:local -R "${app_uid}:${app_gid}" /app/data \
      || die "无法让容器写入数据目录"
  elif [ "$key_permissions_need_fix" = true ]; then
    log "正在修复 ALAS Token 密钥文件权限（容器 UID ${app_uid}:${app_gid}）..."
    docker run --rm --user 0 -v "$DATA_DIR:/app/data" --entrypoint chown scrcpygate:local "${app_uid}:${app_gid}" /app/data/.alas-token-encryption-key \
      || die "无法让容器读取 ALAS Token 密钥文件"
  fi
}

initialize_admin() {
  log "正在初始化管理员账号..."
  if [ -n "$INITIAL_ADMIN_PASSWORD" ]; then
    export INITIAL_ADMIN_PASSWORD
    bootstrap_output=$(compose run --rm --no-deps -e INITIAL_ADMIN_PASSWORD -e SCRCPYGATE_SHOW_GENERATED_PASSWORD=true scrcpygate python -m app.cli bootstrap-admin) \
      || die "管理员初始化失败"
  else
    bootstrap_output=$(compose run --rm --no-deps -e SCRCPYGATE_SHOW_GENERATED_PASSWORD=true scrcpygate python -m app.cli bootstrap-admin) \
      || die "管理员初始化失败"
  fi
  unset INITIAL_ADMIN_PASSWORD
  if ! password=$(parse_single_output_line "$bootstrap_output"); then
    die "管理员初始化返回了无法识别的多行输出"
  fi
  bootstrap_output=""
  if [ -n "$password" ]; then
    panel_top "初始管理员账号（请立即保存）"
    panel_line "用户名" "admin"
    panel_line "密码" "$password"
    print_rule
    password=""
  else
    panel_top "检测到现有管理员账号"
    panel_line "用户名" "admin"
    panel_line "密码" "保持原密码（安全原因不会重复显示）"
    panel_line "后续重置" "管理菜单 10，或 ./deploy.sh --reset-admin"
    print_rule
    if is_interactive && prompt_confirm_no "是否立即生成并显示新的 admin 密码？"; then
      reset_output=$(compose run --rm --no-deps -e SCRCPYGATE_SHOW_GENERATED_PASSWORD=true scrcpygate python -m app.cli reset-admin) \
        || die "管理员密码重置失败"
      if ! password=$(parse_single_output_line "$reset_output"); then
        die "管理员密码重置返回了无法识别的多行输出"
      fi
      reset_output=""
      [ -n "$password" ] || die "管理员密码已重置，但未能读取新密码"
      panel_top "管理员密码已重置（请立即保存）"
      panel_line "用户名" "admin"
      panel_line "新密码" "$password"
      print_rule
      password=""
    else
      success_msg "已保留现有管理员密码"
    fi
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

compose_up_checked() {
  # Keep the standard `compose up -d` startup contract visible to operators and
  # repository checks while routing failures through one diagnostic path.
  if compose up "$@"; then
    return 0
  fi
  error_msg "Docker Compose 启动失败"
  detect_port_occupancy
  detect_port_occupancy_containers
  if [ "$PORT_OCCUPANCY_STATUS" = available ] && [ -n "$PORT_OCCUPANCY_DETAILS" ]; then
    show_port_occupancy
  fi
  show_diagnostics
  die "容器未启动；请检查端口占用、Docker 日志和配置后重试"
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
  panel_line "访问地址" "$(display_url "$PUBLIC_BASE_URL")"
  panel_line "服务监听" "${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT}"
  panel_line "数据目录" "$DATA_DIR"
  print_rule
}

apply_configuration_to_running_instance() {
  prepare_deployment
  prepare_data_directory
  ensure_alas_token_key
  ensure_data_permissions
  log "正在应用新配置并重启 ScrcpyGate..."
  ensure_startup_conflicts
  compose_up_checked -d --force-recreate scrcpygate
  wait_for_health
  success_msg "ScrcpyGate 已重建并应用新配置"
}

configure_only_flow() {
  detect_scrcpygate_instance
  if [ -n "$EXISTING_SCRCPYGATE_STATE" ]; then
    existing_instance_can_be_managed || return 0
  fi
  configure_wizard || return 1
  case "$EXISTING_SCRCPYGATE_STATE" in running|restarting|paused) ;; *) return 0 ;; esac
  show_existing_scrcpygate
  if ! prompt_confirm_no "是否立即重建并重启以应用新配置？"; then
    warn_msg "配置已保存，当前运行实例仍使用旧配置；可稍后选择“重启服务”应用"
    return 0
  fi
  apply_configuration_to_running_instance
}

configure_and_install_flow() {
  # 先确认 Docker/Compose 可用(必要时交互式安装), 避免答完全部配置问题后才失败。
  ensure_runtime_dependencies
  detect_scrcpygate_instance
  if [ -n "$EXISTING_SCRCPYGATE_STATE" ]; then
    prepare_existing_container_for_install || return 0
  fi
  configure_wizard || return 1
  if [ -n "$EXISTING_SCRCPYGATE_STATE" ]; then
    show_existing_scrcpygate
    case "$EXISTING_SCRCPYGATE_STATE" in
      running|restarting|paused) deploy_question="是否重新构建并部署，重启现有 ScrcpyGate？" ;;
      *) deploy_question="是否重新构建并部署现有 ScrcpyGate 容器？" ;;
    esac
    if ! prompt_confirm_no "$deploy_question"; then
      warn_msg "配置已保存，已取消重新部署；现有实例尚未应用新配置"
      return 0
    fi
    INSTALL_INSTANCE_CHECKED=true
  elif is_interactive; then
    panel_top "开始安装"
    panel_line_raw "访问地址" "$PUBLIC_BASE_URL"
    panel_line_raw "监听" "${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT}"
    panel_line_raw "数据目录" "$WEB_SCRCPY_DATA_HOST"
    panel_line "将执行" "构建 scrcpygate:local 镜像 → 初始化 admin → 启动容器并等待健康检查"
    print_rule
    if ! prompt_confirm_no "开始安装？"; then
      warn_msg "配置已保存，已取消安装；稍后可用菜单 2 或 ./deploy.sh --install 继续"
      return 0
    fi
    INSTALL_INSTANCE_CHECKED=true
  fi
  install_service
}

install_service() {
  if [ "${INSTALL_INSTANCE_CHECKED:-false}" != true ]; then
    ensure_env_file
    load_settings
    validate_settings
    detect_scrcpygate_instance
    if [ -n "$EXISTING_SCRCPYGATE_STATE" ]; then
      prepare_existing_container_for_install || return 1
    fi
  fi
  prepare_deployment
  ensure_startup_conflicts
  prepare_data_directory
  show_install_summary
  build_image
  ensure_alas_token_key
  ensure_data_permissions
  detect_scrcpygate_instance
  case "${EXISTING_SCRCPYGATE_STATE:-}" in
    running|restarting|paused) ;;
    *) initialize_admin ;;
  esac
  log "正在启动 ScrcpyGate..."
  compose_up_checked -d
  wait_for_health
  success_msg "安装/更新完成：$(display_url "$PUBLIC_BASE_URL")"
  log "  状态: docker compose ps"
  log "  日志: docker logs --tail=120 scrcpygate"
}

install_current_service() {
  skip_build=false
  pull_images=false
  install_service
}

install_with_pull_service() {
  skip_build=false
  pull_images=true
  install_service
}

# 更新到已发布的 GHCR 镜像：只换镜像，不动源码、不动数据。
# 失败时保留 .env 与数据，自动回滚到更新前的镜像；任何路径都不会退回源码构建。
update_service() {
  prepare_readonly_deployment
  docker compose version >/dev/null 2>&1 || die "镜像更新需要 Docker Compose plugin"
  detect_scrcpygate_instance
  [ "${EXISTING_SCRCPYGATE_STATE:-}" = running ] || die "更新需要已运行的服务；首次部署请使用安装流程"
  existing_instance_can_be_managed || die "当前容器不属于此 bridge 部署；请按对应部署文档更新"
  prepare_data_directory
  up_current_ref=$EXISTING_SCRCPYGATE_IMAGE
  up_current_id=$(docker inspect --format '{{.Image}}' scrcpygate) || die "无法读取当前镜像 ID"
  [ -n "$up_current_id" ] || die "当前镜像 ID 为空"
  up_target_ref=${UPDATE_IMAGE:-}
  [ -n "$up_target_ref" ] || up_target_ref=$(dotenv_value SCRCPYGATE_UPDATE_IMAGE)
  [ -n "$up_target_ref" ] || up_target_ref="ghcr.io/ange-katrina/scrcpygate:latest"
  case "$up_target_ref" in
    ''|*[!a-zA-Z0-9._/@:-]*) die "无效的镜像引用 / invalid image reference" ;;
  esac
  # Pin the actual container image before pulling a mutable tag.
  up_stamp="$(date -u +%Y%m%d-%H%M%S)-$$"
  up_rollback_ref="scrcpygate:rollback-$up_stamp"
  docker image tag "$up_current_id" "$up_rollback_ref" || die "无法保留回滚镜像"
  panel_top "更新到已发布镜像"
  panel_line "当前镜像" "$(safe_display "$up_current_ref")"
  panel_line "目标镜像" "$(safe_display "$up_target_ref")"
  log "正在拉取目标镜像；不会退回源码构建"
  run_docker_pull "$up_target_ref" || die "拉取失败；当前服务与配置保持原状"
  up_target_id=$(docker image inspect --format '{{.Id}}' "$up_target_ref") || die "无法读取目标镜像 ID"
  if [ "$up_target_id" = "$up_current_id" ]; then
    success_msg "当前已运行相同镜像，无需重建 / already running this image"
    return 0
  fi
  # Persist a registry digest where available; otherwise retain the local ID.
  up_pinned_ref=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$up_target_id" | head -n 1)
  [ -n "$up_pinned_ref" ] || up_pinned_ref=$up_target_id
  up_version_label=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$up_target_id" |
    sed -n 's/^SCRCPYGATE_VERSION=//p' | head -n 1)
  case "$up_version_label" in *[!a-zA-Z0-9._/-]*) up_version_label="" ;; esac
  if [ "$UPDATE_SKIP_BACKUP" = true ]; then
    warn_msg "已跳过数据备份；仍保留部署配置与回滚镜像"
    up_env_backup=".env.update-$up_stamp.bak"
  else
    up_archive=$(backup_archive_path "")
    [ ! -e "$up_archive" ] || die "备份文件已存在，未覆盖"
    create_backup_archive "$DATA_DIR" "$up_archive" pre-update running
    prune_backups "$(dirname -- "$up_archive")" "$up_archive"
    up_env_backup="$up_archive.env"
    success_msg "更新前数据备份: $(safe_display "$up_archive")"
  fi
  [ ! -e "$up_env_backup" ] && [ ! -L "$up_env_backup" ] || die "配置备份路径已存在"
  ( umask 077; set -C; cat .env > "$up_env_backup" ) || die "无法备份 .env"
  chmod 600 "$up_env_backup" || die "无法保护 .env 备份"
  # Subshell confines fatal configuration-write errors to the update attempt.
  if (
    set_env_value SCRCPYGATE_IMAGE "$up_pinned_ref"
    set_env_value SCRCPYGATE_VERSION "$up_version_label"
    unset SCRCPYGATE_IMAGE SCRCPYGATE_VERSION
    compose up -d --no-build --pull never scrcpygate || exit 1
    wait_for_health
    [ "$(docker inspect --format '{{.Image}}' scrcpygate)" = "$up_target_id" ]
  ); then
    success_msg "更新完成：$(display_url "$PUBLIC_BASE_URL")"
    panel_line "运行镜像" "$(safe_display "$up_pinned_ref")"
    panel_line "配置备份" "$(safe_display "$up_env_backup")"
    panel_line "回滚镜像" "$up_rollback_ref"
    log "镜像回滚不恢复数据库；不兼容迁移需要人工恢复配套数据备份"
    return 0
  fi
  warn_msg "更新失败，正在恢复原配置与固定回滚镜像"
  if (
    cp -p "$up_env_backup" .env || exit 1
    chmod 600 .env || exit 1
    set_env_value SCRCPYGATE_IMAGE "$up_rollback_ref"
    unset SCRCPYGATE_IMAGE SCRCPYGATE_VERSION
    compose up -d --no-build --pull never scrcpygate || exit 1
    wait_for_health
    [ "$(docker inspect --format '{{.Image}}' scrcpygate)" = "$up_current_id" ]
  ); then
    error_msg "更新失败，原镜像已恢复健康；数据未自动回退"
    log "配置备份: $(safe_display "$up_env_backup")"
    return 2
  fi
  error_msg "更新失败且无法确认回滚健康；请用配置和数据备份人工恢复"
  log "配置备份: $(safe_display "$up_env_backup")"
  show_diagnostics
  return 3
}

start_service() {
  prepare_deployment
  ensure_startup_conflicts
  prepare_data_directory
  ensure_alas_token_key
  ensure_data_permissions
  detect_scrcpygate_instance
  case "${EXISTING_SCRCPYGATE_STATE:-}" in
    running|restarting|paused) ;;
    *) initialize_admin ;;
  esac
  compose_up_checked -d
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
  ensure_startup_conflicts
  prepare_data_directory
  ensure_alas_token_key
  ensure_data_permissions
  compose_up_checked -d --force-recreate scrcpygate
  wait_for_health
  success_msg "ScrcpyGate 已重建并应用当前配置"
}

show_status() {
  prepare_deployment
  compose ps
}

show_logs() {
  prepare_deployment
  lines=${LOG_LINES:-200}
  case "$lines" in ''|*[!0-9]*) lines=200 ;; esac
  if [ "$lines" -lt 1 ] || [ "$lines" -gt 10000 ]; then lines=200; fi
  docker logs --tail="$lines" scrcpygate 2>/dev/null || warn_msg "暂无容器日志"
}

reset_admin() {
  prepare_deployment
  prepare_data_directory
  ensure_alas_token_key
  ensure_data_permissions
  container_state=$(docker inspect --format '{{.State.Status}}' scrcpygate 2>/dev/null || true)
  if [ "$container_state" = running ]; then
    reset_output=$(compose exec -T -e SCRCPYGATE_SHOW_GENERATED_PASSWORD=true scrcpygate python -m app.cli reset-admin) || die "管理员密码重置失败"
  else
    warn_msg "容器未运行，将使用一次性容器执行重置"
    reset_output=$(compose run --rm --no-deps -e SCRCPYGATE_SHOW_GENERATED_PASSWORD=true scrcpygate python -m app.cli reset-admin) || die "管理员密码重置失败"
  fi
  if ! password=$(parse_single_output_line "$reset_output"); then
    die "管理员密码重置返回了无法识别的多行输出"
  fi
  reset_output=""
  [ -n "$password" ] || die "管理员密码已重置，但未能读取新密码"
  panel_top "管理员密码已重置"
  panel_line "用户名" "admin"
  panel_line "新密码" "$password"
  print_rule
  password=""
}

# --- 备份与恢复 -------------------------------------------------------------

backup_dir_default() {
  if [ -n "${SCRCPYGATE_BACKUP_DIR:-}" ]; then
    printf '%s\n' "$SCRCPYGATE_BACKUP_DIR"
    return 0
  fi
  if [ -f .env ]; then
    bk_dir_from_env=$(dotenv_value SCRCPYGATE_BACKUP_DIR)
    if [ -n "$bk_dir_from_env" ]; then
      printf '%s\n' "$bk_dir_from_env"
      return 0
    fi
  fi
  printf '%s\n' "$SCRIPT_DIR/backups"
}

backup_keep_default() {
  if [ -n "${BACKUP_KEEP:-}" ]; then
    printf '%s\n' "$BACKUP_KEEP"
    return 0
  fi
  if [ -n "${SCRCPYGATE_BACKUP_KEEP:-}" ]; then
    printf '%s\n' "$SCRCPYGATE_BACKUP_KEEP"
    return 0
  fi
  if [ -f .env ]; then
    dotenv_value SCRCPYGATE_BACKUP_KEEP
  fi
}

format_bytes() {
  awk -v bytes="${1:-0}" 'BEGIN {
    if (bytes >= 1073741824) { printf "%.2f GiB", bytes / 1073741824 }
    else if (bytes >= 1048576) { printf "%.1f MiB", bytes / 1048576 }
    else if (bytes >= 1024) { printf "%.1f KiB", bytes / 1024 }
    else { printf "%d B", bytes }
  }'
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" 2>/dev/null | awk '{print $1}'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'
    return 0
  fi
  return 1
}

backup_archive_path() {
  stamp=$(date -u +%Y%m%d-%H%M%S)
  target=${1:-}
  [ -n "$target" ] || target=$(backup_dir_default)
  case "$target" in
    *.tar.gz|*.tgz)
      parent=$(dirname -- "$target")
      mkdir -p "$parent" || die "无法创建备份目录: $parent"
      printf '%s\n' "$target"
      ;;
    *)
      target=${target%/}
      mkdir -p "$target" || die "无法创建备份目录: $target"
      printf '%s\n' "$target/scrcpygate-backup-$stamp.tar.gz"
      ;;
  esac
}

export_running_sqlite() {
  # 通过 SQLite 在线备份 API 从运行中的容器导出一致性快照；失败返回非 0。
  # 注意：本脚本不使用 local，函数内变量一律加前缀，避免覆盖调用者的同名全局变量
  # （此前 `destination` 就踩过：调用方刚算好的归档路径被这里冲掉，tar 于是写错了目标）。
  sqlite_export_target=$1
  docker exec scrcpygate python -c "import sqlite3; source = sqlite3.connect('/app/data/webscrcpy.db'); target = sqlite3.connect('/tmp/.scrcpygate-backup.db'); source.backup(target); target.close(); source.close()" >/dev/null 2>&1 || return 1
  if ! docker cp "scrcpygate:/tmp/.scrcpygate-backup.db" "$sqlite_export_target" >/dev/null 2>&1; then
    docker exec scrcpygate rm -f /tmp/.scrcpygate-backup.db >/dev/null 2>&1 || true
    return 1
  fi
  docker exec scrcpygate rm -f /tmp/.scrcpygate-backup.db >/dev/null 2>&1 || true
  return 0
}

read_backup_manifest_field() {
  printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -1
}

create_backup_archive() {
  # $1 源数据目录  $2 目标归档  $3 类别（backup|pre-restore）  $4 容器状态标签
  # 变量一律加 bk_ 前缀：本脚本没有 local，函数间共享全局命名空间。
  bk_source_dir=$1
  bk_destination=$2
  bk_kind=$3
  bk_state=$4

  BACKUP_STAGING_DIR=$(mktemp -d) || die "无法创建备份临时目录"
  bk_staging="$BACKUP_STAGING_DIR"
  mkdir -p "$bk_staging/data" || die "无法创建备份临时目录"
  chmod 700 "$bk_staging" 2>/dev/null || true

  bk_sqlite_mode="file-copy"
  if [ "$bk_state" = running ]; then
    if export_running_sqlite "$bk_staging/data/webscrcpy.db"; then
      bk_sqlite_mode="online-backup"
    else
      [ "$bk_kind" != pre-update ] || die "更新前无法取得一致的数据库快照；已停止更新"
      warn_msg "在线备份接口不可用，回退为直接复制数据库文件"
    fi
  fi
  if [ ! -f "$bk_staging/data/webscrcpy.db" ]; then
    [ -f "$bk_source_dir/webscrcpy.db" ] || die "未找到数据库文件: $bk_source_dir/webscrcpy.db"
    cp -p "$bk_source_dir/webscrcpy.db" "$bk_staging/data/webscrcpy.db" || die "无法复制数据库文件"
    for bk_sidecar in webscrcpy.db-wal webscrcpy.db-shm; do
      if [ -f "$bk_source_dir/$bk_sidecar" ]; then
        cp -p "$bk_source_dir/$bk_sidecar" "$bk_staging/data/$bk_sidecar" || die "无法复制 $bk_sidecar"
      fi
    done
  fi

  bk_key_included=false
  if [ -f "$bk_source_dir/.alas-token-encryption-key" ]; then
    cp -p "$bk_source_dir/.alas-token-encryption-key" "$bk_staging/data/.alas-token-encryption-key" || die "无法复制 ALAS 令牌密钥"
    chmod 600 "$bk_staging/data/.alas-token-encryption-key" 2>/dev/null || true
    bk_key_included=true
  fi

  {
    printf 'format=scrcpygate-backup\n'
    printf 'format_version=1\n'
    printf 'kind=%s\n' "$bk_kind"
    printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'container_state=%s\n' "$bk_state"
    printf 'image=%s\n' "${EXISTING_SCRCPYGATE_IMAGE:-unknown}"
    printf 'sqlite_mode=%s\n' "$bk_sqlite_mode"
    printf 'data_dir_name=%s\n' "$(basename -- "$bk_source_dir")"
    printf 'alas_key_included=%s\n' "$bk_key_included"
    for bk_name in webscrcpy.db webscrcpy.db-wal webscrcpy.db-shm .alas-token-encryption-key; do
      if [ -f "$bk_staging/data/$bk_name" ]; then
        bk_file_bytes=$(wc -c < "$bk_staging/data/$bk_name" | tr -d ' ')
        bk_file_sha=$(sha256_of "$bk_staging/data/$bk_name" || printf 'unavailable')
        printf 'file=%s bytes=%s sha256=%s\n' "$bk_name" "$bk_file_bytes" "$bk_file_sha"
      fi
    done
  } > "$bk_staging/MANIFEST" || die "无法写入备份清单"

  log "正在打包备份归档..."
  tar -czf "$bk_destination" -C "$bk_staging" . || die "无法创建备份归档: $bk_destination"
  chmod 600 "$bk_destination" 2>/dev/null || true
  ARCHIVE_BYTES=$(wc -c < "$bk_destination" | tr -d ' ')
  ARCHIVE_SHA=$(sha256_of "$bk_destination" || printf '')
  if [ -n "$ARCHIVE_SHA" ]; then
    printf '%s  %s\n' "$ARCHIVE_SHA" "$(basename -- "$bk_destination")" > "$bk_destination.sha256" || die "无法写入校验文件"
    chmod 600 "$bk_destination.sha256" 2>/dev/null || true
  fi
  ARCHIVE_SQLITE_MODE=$bk_sqlite_mode
  ARCHIVE_KEY_INCLUDED=$bk_key_included
  rm -rf "$bk_staging" 2>/dev/null || true
  BACKUP_STAGING_DIR=""
}

prune_backups() {
  # $1 备份目录  $2 本次刚生成的归档（永不删除）
  bk_prune_dir=$1
  bk_prune_current=$2
  bk_prune_keep=$(backup_keep_default)
  if [ -z "$bk_prune_keep" ]; then
    return 0
  fi
  case "$bk_prune_keep" in
    *[!0-9]*)
      warn_msg "保留份数必须是正整数（当前: $bk_prune_keep），本次不清理旧备份"
      return 0
      ;;
  esac
  if [ "$bk_prune_keep" -lt 1 ]; then
    warn_msg "保留份数必须 ≥ 1（当前: $bk_prune_keep），本次不清理旧备份"
    return 0
  fi
  [ -d "$bk_prune_dir" ] || return 0
  bk_prune_victims=$(ls -1 "$bk_prune_dir"/scrcpygate-backup-*.tar.gz 2>/dev/null | sort -r | tail -n +$((bk_prune_keep + 1)))
  [ -n "$bk_prune_victims" ] || return 0
  bk_prune_removed=0
  for bk_prune_victim in $bk_prune_victims; do
    [ -n "$bk_prune_victim" ] || continue
    if [ "$bk_prune_victim" = "$bk_prune_current" ]; then
      continue
    fi
    if rm -f "$bk_prune_victim" "$bk_prune_victim.sha256" "$bk_prune_victim.env"; then
      bk_prune_removed=$((bk_prune_removed + 1))
      log "  已清理旧备份: $(basename -- "$bk_prune_victim")"
    fi
  done
  if [ "$bk_prune_removed" -gt 0 ]; then
    success_msg "按保留 ${bk_prune_keep} 份清理了 ${bk_prune_removed} 个旧备份"
  fi
}

backup_service() {
  require_docker
  prepare_deployment
  prepare_data_directory
  detect_scrcpygate_instance
  container_state=${EXISTING_SCRCPYGATE_STATE:-absent}

  archive=$(backup_archive_path "${BACKUP_OUTPUT:-}")
  if [ -e "$archive" ]; then
    die "备份文件已存在，未覆盖: $archive"
  fi

  create_backup_archive "$DATA_DIR" "$archive" backup "$container_state"
  prune_backups "$(dirname -- "$archive")" "$archive"

  panel_top "备份完成"
  panel_line "归档" "$archive"
  panel_line "大小" "$(format_bytes "${ARCHIVE_BYTES:-0}")"
  panel_line "数据库方式" "${ARCHIVE_SQLITE_MODE:-unknown}"
  panel_line "ALAS 密钥" "${ARCHIVE_KEY_INCLUDED:-false}"
  if [ -n "${ARCHIVE_SHA:-}" ]; then
    panel_line "SHA-256" "$ARCHIVE_SHA"
    panel_line "校验文件" "$archive.sha256"
  else
    warn_msg "系统缺少 sha256sum/shasum，未生成校验文件"
  fi
  print_rule
  log "  恢复命令: ./deploy.sh --restore $archive"
}

list_backups_service() {
  bk_list_dir=$(backup_dir_default)
  panel_top "已有备份"
  panel_line "备份目录" "$bk_list_dir"
  print_rule
  if [ ! -d "$bk_list_dir" ]; then
    printf '  （目录不存在）\n'
    print_rule
    return 0
  fi
  bk_list_found=false
  for bk_list_file in "$bk_list_dir"/scrcpygate-backup-*.tar.gz "$bk_list_dir"/scrcpygate-pre-restore-*.tar.gz; do
    [ -f "$bk_list_file" ] || continue
    bk_list_found=true
    bk_list_bytes=$(wc -c < "$bk_list_file" | tr -d ' ')
    bk_list_manifest=$(tar -xzOf "$bk_list_file" ./MANIFEST 2>/dev/null || true)
    bk_list_created=$(read_backup_manifest_field "$bk_list_manifest" created_at)
    bk_list_kind=$(read_backup_manifest_field "$bk_list_manifest" kind)
    printf '  %-10s %-11s %s  %s\n' "$(format_bytes "$bk_list_bytes")" "${bk_list_kind:-backup}" "${bk_list_created:-unknown}" "$(basename -- "$bk_list_file")"
  done
  if [ "$bk_list_found" != true ]; then
    printf '  （还没有备份）\n'
  fi
  print_rule
}

latest_backup_summary() {
  bk_latest_dir=$(backup_dir_default)
  if [ ! -d "$bk_latest_dir" ]; then
    printf '%s' "（暂无）"
    return 0
  fi
  bk_latest_file=""
  for bk_latest_candidate in "$bk_latest_dir"/scrcpygate-backup-*.tar.gz "$bk_latest_dir"/scrcpygate-pre-restore-*.tar.gz; do
    [ -f "$bk_latest_candidate" ] || continue
    if [ -z "$bk_latest_file" ] || [ "$bk_latest_candidate" -nt "$bk_latest_file" ]; then
      bk_latest_file=$bk_latest_candidate
    fi
  done
  if [ -z "$bk_latest_file" ]; then
    printf '%s' "（暂无）"
    return 0
  fi
  bk_latest_bytes=$(wc -c < "$bk_latest_file" | tr -d ' ')
  bk_latest_time=$(date -u -r "$bk_latest_file" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || printf 'unknown')
  printf '%s · %s · %s' "$bk_latest_time" "$(format_bytes "$bk_latest_bytes")" "$(basename -- "$bk_latest_file")"
}

# wait_for_health() 失败即 die；恢复流程需要在失败时回滚，所以这里返回状态码。
wait_for_health_status() {
  health_url_for_settings
  started_at=$(date +%s)
  while :; do
    if http_health_ok; then
      return 0
    fi
    state=$(docker inspect --format '{{.State.Status}}' scrcpygate 2>/dev/null || true)
    case "$state" in
      exited|dead) return 1 ;;
    esac
    now=$(date +%s)
    [ $((now - started_at)) -lt "$SCRCPYGATE_HEALTH_TIMEOUT" ] || return 1
    sleep 1
  done
}

restore_rollback() {
  warn_msg "正在回滚到恢复前的数据目录..."
  compose stop scrcpygate >/dev/null 2>&1 || true
  rm -rf "$DATA_DIR"
  if [ -n "${RESTORE_SAFETY_DIR:-}" ] && [ -d "$RESTORE_SAFETY_DIR" ]; then
    if mv "$RESTORE_SAFETY_DIR" "$DATA_DIR"; then
      RESTORE_SAFETY_DIR=""
    else
      warn_msg "无法移回恢复前的数据目录，请手工恢复: $RESTORE_SAFETY_DIR"
    fi
  fi
  ensure_data_permissions >/dev/null 2>&1 || true
  compose up -d >/dev/null 2>&1 || true
  if wait_for_health_status; then
    success_msg "已回滚到恢复前的数据，服务健康"
  else
    warn_msg "回滚后健康检查仍未通过，请查看日志: docker logs --tail=120 scrcpygate"
  fi
}

restore_service() {
  require_docker
  [ -n "${RESTORE_ARCHIVE:-}" ] || die "--restore 需要一个备份归档路径"
  [ -f "$RESTORE_ARCHIVE" ] || die "备份归档不存在: $RESTORE_ARCHIVE"

  prepare_deployment
  prepare_data_directory

  tar -tzf "$RESTORE_ARCHIVE" >/dev/null 2>&1 || die "备份归档无法读取（不是有效的 tar.gz）: $RESTORE_ARCHIVE"
  archive_manifest=$(tar -xzOf "$RESTORE_ARCHIVE" ./MANIFEST 2>/dev/null || true)
  [ -n "$archive_manifest" ] || die "备份归档缺少 MANIFEST，拒绝恢复"
  archive_format=$(read_backup_manifest_field "$archive_manifest" format)
  [ "$archive_format" = scrcpygate-backup ] || die "不是 ScrcpyGate 备份归档（format=${archive_format:-未知}）"

  sidecar="$RESTORE_ARCHIVE.sha256"
  if [ -f "$sidecar" ]; then
    expected_sha=$(awk '{print $1}' "$sidecar" | head -1)
    actual_sha=$(sha256_of "$RESTORE_ARCHIVE" || printf '')
    if [ -n "$expected_sha" ] && [ -n "$actual_sha" ] && [ "$expected_sha" != "$actual_sha" ]; then
      die "备份校验和不匹配，拒绝恢复（归档可能已损坏）"
    fi
  else
    warn_msg "未找到 .sha256 校验文件，跳过完整性校验"
  fi

  detect_scrcpygate_instance
  panel_top "从备份恢复"
  panel_line "归档" "$RESTORE_ARCHIVE"
  panel_line "创建时间" "$(read_backup_manifest_field "$archive_manifest" created_at)"
  panel_line "数据库方式" "$(read_backup_manifest_field "$archive_manifest" sqlite_mode)"
  panel_line "ALAS 密钥" "$(read_backup_manifest_field "$archive_manifest" alas_key_included)"
  panel_line "当前数据目录" "$DATA_DIR"
  panel_line "服务容器" "${EXISTING_SCRCPYGATE_STATE:-不存在}"
  print_rule
  warn_msg "恢复会用归档内容替换当前数据目录（账号、设备、权限与全部应用配置）"
  if [ "$ASSUME_YES" != true ]; then
    require_interactive
    if ! prompt_confirm_no "确认执行恢复？"; then
      warn_msg "已取消恢复，未修改任何文件或容器"
      return 0
    fi
  fi

  BACKUP_STAGING_DIR=$(mktemp -d) || die "无法创建恢复临时目录"
  extract_dir="$BACKUP_STAGING_DIR/extract"
  mkdir -p "$extract_dir" || die "无法创建恢复临时目录"
  tar -xzf "$RESTORE_ARCHIVE" -C "$extract_dir" || die "解压备份归档失败"
  [ -d "$extract_dir/data" ] || die "备份归档缺少 data/ 目录"
  [ -f "$extract_dir/data/webscrcpy.db" ] || die "备份归档缺少数据库文件"

  # 运行中的容器持有数据库连接，换数据前必须先停下；--data-only 则完全不碰容器。
  if [ "$DATA_ONLY" != true ] && [ -n "${EXISTING_SCRCPYGATE_STATE:-}" ]; then
    log "正在停止服务容器..."
    compose stop scrcpygate >/dev/null 2>&1 || die "无法停止服务容器，未执行恢复"
  fi

  # 容器已停，此时的数据目录是静态的：先把它整体打成归档，作为可长期保存的恢复前快照。
  PRE_RESTORE_ARCHIVE=""
  if [ "$DATA_ONLY" != true ] && [ -f "$DATA_DIR/webscrcpy.db" ]; then
    PRE_RESTORE_ARCHIVE="$(backup_dir_default)/scrcpygate-pre-restore-$(date -u +%Y%m%d-%H%M%S).tar.gz"
    pre_restore_parent=$(dirname -- "$PRE_RESTORE_ARCHIVE")
    mkdir -p "$pre_restore_parent" || die "无法创建备份目录: $pre_restore_parent"
    log "正在为当前数据留存恢复前归档..."
    create_backup_archive "$DATA_DIR" "$PRE_RESTORE_ARCHIVE" pre-restore "${EXISTING_SCRCPYGATE_STATE:-absent}"
  fi

  RESTORE_SAFETY_DIR=""
  if [ -d "$DATA_DIR" ]; then
    RESTORE_SAFETY_DIR="${DATA_DIR%/}.pre-restore-$(date -u +%Y%m%d-%H%M%S)"
    mv "$DATA_DIR" "$RESTORE_SAFETY_DIR" || die "无法为当前数据目录创建安全副本"
  fi
  mkdir -p "$DATA_DIR" || die "无法创建数据目录"
  if ! cp -a "$extract_dir/data/." "$DATA_DIR/"; then
    restore_rollback
    die "复制备份内容失败，已回滚"
  fi
  chmod 700 "$DATA_DIR" 2>/dev/null || true

  if docker image inspect scrcpygate:local >/dev/null 2>&1; then
    ensure_data_permissions || die "恢复后无法修正数据目录权限（恢复前副本仍保留）"
  else
    warn_msg "本地镜像不存在，改用固定属主 100:101"
    chown -R 100:101 "$DATA_DIR" || warn_msg "无法修正数据目录属主，请手工执行 chown -R 100:101 $DATA_DIR"
  fi

  if [ "$DATA_ONLY" = true ]; then
    warn_msg "已按要求只恢复数据（--data-only），未触碰服务容器"
  elif [ "$NO_RESTART" = true ]; then
    warn_msg "已按要求跳过容器启动（--no-restart），服务当前处于停止状态"
  else
    log "正在启动 ScrcpyGate..."
    if ! compose up -d; then
      restore_rollback
      die "恢复后启动失败，已回滚"
    fi
    if ! wait_for_health_status; then
      restore_rollback
      die "恢复后健康检查未通过，已回滚"
    fi
  fi

  rm -rf "$extract_dir" 2>/dev/null || true
  rm -rf "$BACKUP_STAGING_DIR" 2>/dev/null || true
  BACKUP_STAGING_DIR=""

  panel_top "恢复完成"
  panel_line "归档" "$RESTORE_ARCHIVE"
  panel_line "数据目录" "$DATA_DIR"
  if [ -n "${PRE_RESTORE_ARCHIVE:-}" ]; then
    panel_line "恢复前归档" "$PRE_RESTORE_ARCHIVE"
  fi
  # 恢复成功后，目录形式的临时副本已无必要（归档副本仍然保留），自动清掉避免堆积。
  if [ -n "${RESTORE_SAFETY_DIR:-}" ]; then
    if rm -rf "$RESTORE_SAFETY_DIR"; then
      log "  已清理临时副本: $RESTORE_SAFETY_DIR"
    else
      warn_msg "临时副本未能删除，请手工处理: $RESTORE_SAFETY_DIR"
    fi
    RESTORE_SAFETY_DIR=""
  fi
  print_rule
}

check_production_boundary() {
  panel_top "ScrcpyGate 生产边界校验"
  validate_production_boundary
  panel_line "Gateway" "已关闭（ALAS_EMBED_ORIGIN 为空；不显示具体值）"
  panel_line "公开 Origin" "已配置 HTTPS（具体主机按默认策略隐藏）"
  panel_line "代理信任" "已启用并限制为配置的 IP/CIDR"
  panel_line "ALAS Runtime" "仅允许固定主机路由（不显示地址）"
  panel_line "密钥" "格式有效（未显示内容）"
  print_rule
}

check_service() {
  panel_top "ScrcpyGate 环境检查"
  if [ -f .env ]; then
    panel_line ".env" "存在"
  else
    panel_line ".env" "缺失（将使用默认配置）"
  fi
  load_uninstall_settings
  if data_dir=$(configured_data_dir_from_disk); then
    panel_line "数据目录" "$data_dir"
    if [ -w "$data_dir" ]; then
      panel_line "数据目录可写" "是"
    else
      warn_msg "数据目录不可写: $data_dir"
    fi
  else
    warn_msg "数据目录不存在: $(safe_display "$WEB_SCRCPY_DATA_HOST")"
  fi
  if command -v docker >/dev/null 2>&1; then
    panel_line "Docker" "$(docker --version 2>/dev/null | head -1)"
    if docker info >/dev/null 2>&1; then
      panel_line "Docker 守护进程" "可用"
    else
      warn_msg "Docker 守护进程不可用（权限不足或服务未启动）"
    fi
  else
    warn_msg "未检测到 Docker"
  fi
  if docker compose version >/dev/null 2>&1; then
    panel_line "Docker Compose" "$(docker compose version 2>/dev/null | head -1)"
  elif command -v docker-compose >/dev/null 2>&1; then
    warn_msg "正在使用旧版 docker-compose，建议升级为 Compose 插件"
  else
    warn_msg "未检测到 Docker Compose"
  fi
  state=$(docker inspect --format '{{.State.Status}}' scrcpygate 2>/dev/null || true)
  if [ -n "$state" ]; then
    panel_line "容器状态" "$(service_status_line)"
  else
    panel_line "容器" "未创建"
  fi
  if docker image inspect scrcpygate:local >/dev/null 2>&1; then
    panel_line "本地镜像" "scrcpygate:local 已构建"
  else
    panel_line "本地镜像" "尚未构建（安装时将自动构建）"
  fi
  if [ -f .env ]; then
    alas_origin=$(dotenv_value ALAS_EMBED_ORIGIN)
    [ -z "$alas_origin" ] || panel_line "ALAS 独立域名" "已设置（如需共享主域名请保持为空）"
  fi
  if command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then
    if [ -f .env ]; then
      load_settings
      health_url_for_settings
      if http_health_ok; then
        panel_line "服务健康检查" "通过: $health_url"
      else
        warn_msg "健康检查未通过: $health_url"
      fi
    fi
  fi
  print_rule
}

python_command() {
  if command -v python >/dev/null 2>&1; then
    printf '%s\n' python
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s\n' python3
  else
    error_msg "宿主缺少 Python 3，无法解析容器输出 / host Python 3 is required to parse container output"
    return 1
  fi
}

token_cli() {
  token_state=$(docker inspect --format '{{.State.Status}}' scrcpygate 2>/dev/null || true)
  case "$token_state" in
    running|restarting|paused)
      # Use the existing single instance when it is running; a second Compose
      # container could race on SQLite during a credential migration.
      compose exec -T scrcpygate python -m app.cli "$@"
      ;;
    *)
      compose run --rm --no-deps scrcpygate python -m app.cli "$@"
      ;;
  esac
}

redacted_token_status() {
  status_json=$1
  status_python=$(python_command) || return 1
  TOKEN_STATUS_JSON=$status_json "$status_python" - <<'PY'
import json
import os
import sys

try:
    status = json.loads(os.environ["TOKEN_STATUS_JSON"])
except (KeyError, TypeError, ValueError):
    raise SystemExit(1)
if not isinstance(status, dict):
    raise SystemExit(1)
allowed = (
    "ok",
    "state",
    "format",
    "decryptable",
    "key_source",
    "needs_rotation",
    "current_key_configured",
    "current_key_valid",
    "previous_key_configured",
    "previous_key_valid",
    "legacy_env_token_present",
    "legacy_env_migrated",
)
safe = {key: status.get(key) for key in allowed}
current_ready = bool(status.get("ok")) \
    and status.get("current_key_configured") is True \
    and status.get("current_key_valid") is True \
    and not bool(status.get("needs_rotation")) \
    and not bool(status.get("legacy_env_token_present")) \
    and status.get("key_source") in (None, "current")
safe["current_key_ready"] = current_ready
safe["previous_key_retired"] = not bool(status.get("previous_key_configured"))
safe["ready"] = current_ready and safe["previous_key_retired"]
print(json.dumps(safe, sort_keys=True, separators=(",", ":")))
PY
}

token_current_ready() {
  status_json=$1
  status_python=$(python_command) || return 1
  TOKEN_STATUS_JSON=$status_json "$status_python" - <<'PY'
import json
import os

try:
    value = json.loads(os.environ["TOKEN_STATUS_JSON"])
except (KeyError, TypeError, ValueError):
    raise SystemExit(1)
print("true" if isinstance(value, dict) and value.get("current_key_ready") is True else "false")
PY
}

token_status_ready() {
  status_json=$1
  status_python=$(python_command) || return 1
  TOKEN_STATUS_JSON=$status_json "$status_python" - <<'PY'
import json
import os

try:
    value = json.loads(os.environ["TOKEN_STATUS_JSON"])
except (KeyError, TypeError, ValueError):
    raise SystemExit(1)
print("true" if isinstance(value, dict) and value.get("ready") is True else "false")
PY
}

token_status_service() {
  prepare_readonly_deployment
  token_output=$(token_cli alas-token-status --check) || {
    token_output=""
    die "ALAS Token 状态检查失败；未显示凭据"
  }
  if ! token_line=$(parse_single_output_line "$token_output"); then
    token_output=""
    die "ALAS Token 状态返回了无法识别的多行输出"
  fi
  token_output=""
  if ! safe_status=$(redacted_token_status "$token_line"); then
    token_line=""
    die "ALAS Token 状态输出无效；未显示凭据"
  fi
  token_line=""
  panel_top "ALAS Token 迁移状态"
  panel_line "状态" "$safe_status"
  print_rule
  [ "$(token_status_ready "$safe_status")" = true ] \
    || die "ALAS Token 尚未完成 current key 迁移；未显示凭据"
}

migrate_alas_token_service() {
  prepare_readonly_deployment
  token_output=$(token_cli migrate-alas-token) || {
    token_output=""
    die "ALAS Token 迁移失败；服务应保持维护状态且未显示凭据"
  }
  if ! token_line=$(parse_single_output_line "$token_output"); then
    token_output=""
    die "ALAS Token 迁移返回了无法识别的多行输出"
  fi
  token_output=""
  if ! safe_status=$(redacted_token_status "$token_line"); then
    token_line=""
    die "ALAS Token 迁移状态输出无效；未显示凭据"
  fi
  token_line=""
  panel_top "ALAS Token 迁移完成"
  panel_line "状态" "$safe_status"
  panel_line "下一步" "确认新 Runtime Token 返回 200 后，再撤销旧 Token；应用不再注入 previous key"
  print_rule
  [ "$(token_current_ready "$safe_status")" = true ] \
    || die "ALAS Token 迁移尚未达到 current key 状态；未显示凭据"
}

clear_alas_token_service() {
  # 恢复出口：存不出来的密文（密钥换了/丢了）会一直让 ALAS 报错，清空后可在界面重填。
  # 只打印脱敏摘要（是否清掉、原值指纹），绝不出示凭据本身。
  prepare_readonly_deployment
  token_output=$(token_cli clear-alas-token) || {
    token_output=""
    die "清空 ALAS Token 失败；未显示凭据"
  }
  if ! token_line=$(parse_single_output_line "$token_output"); then
    token_output=""
    die "清空 ALAS Token 返回了无法识别的多行输出"
  fi
  token_output=""
  panel_top "ALAS Token 已清空"
  panel_line "结果" "$token_line"
  panel_line "下一步" "在「ALAS 设置」里重新填写 Runtime Token（会用当前密钥重新加密）"
  print_rule
}

candidate_manifest_service() {
  manifest_python=$(python_command) || die "生成候选清单需要 Python"
  # Tests and maintenance tools live in the repository, but are not release
  # inputs.  An explicit override remains available for controlled validation.
  manifest_tool=${SCRCPYGATE_MANIFEST_TOOL:-$SCRIPT_DIR/tools/release_manifest.py}
  [ -f "$manifest_tool" ] || die "缺少候选清单工具: $manifest_tool"
  if [ -n "$CANDIDATE_MANIFEST" ]; then
    candidate_output=$CANDIDATE_MANIFEST
  else
    manifest_run_id=$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || date +%Y%m%dT%H%M%S)
    candidate_output="$SCRIPT_DIR/../output/release-manifest/$manifest_run_id/scrcpygate-release-manifest.json"
  fi
  "$manifest_python" "$manifest_tool" \
    --root "$SCRIPT_DIR" \
    --output "$candidate_output" \
    --image scrcpygate:local \
    || die "候选清单生成失败；要求清洁 checkout、完整源码输入和可读取的镜像 digest"
  candidate_checksum=""
  if command -v sha256sum >/dev/null 2>&1; then
    candidate_checksum=$(sha256sum "$candidate_output" 2>/dev/null | awk '{print $1}')
  elif command -v openssl >/dev/null 2>&1; then
    candidate_checksum=$(openssl dgst -sha256 "$candidate_output" 2>/dev/null | awk '{print $NF}')
  fi
  panel_top "生产候选清单完成"
  panel_line "清单" "$candidate_output"
  panel_line "内容" "清洁 Git commit、release 输入文件 SHA-256 和 scrcpygate:local image digest"
  [ -n "$candidate_checksum" ] && panel_line "清单 SHA-256" "$candidate_checksum"
  print_rule
}

load_uninstall_settings() {
  if [ -z "${WEB_SCRCPY_DATA_HOST:-}" ] && [ -f .env ]; then
    WEB_SCRCPY_DATA_HOST=$(dotenv_value WEB_SCRCPY_DATA_HOST)
  fi
  WEB_SCRCPY_DATA_HOST=${WEB_SCRCPY_DATA_HOST:-./data}
}

configured_data_dir_from_disk() {
  load_uninstall_settings
  [ -d "$WEB_SCRCPY_DATA_HOST" ] || return 1
  (CDPATH= cd -- "$WEB_SCRCPY_DATA_HOST" && pwd -P)
}

canonical_path_allow_missing_leaf() {
  raw_path=${1:-}
  [ -n "$raw_path" ] || return 1
  case "$raw_path" in
    /*) candidate=$raw_path ;;
    *) candidate=$SCRIPT_DIR/$raw_path ;;
  esac
  while [ "$candidate" != / ] && [ "${candidate%/}" != "$candidate" ]; do
    candidate=${candidate%/}
  done
  leaf=${candidate##*/}
  parent=${candidate%/*}
  [ -n "$leaf" ] || return 1
  [ -n "$parent" ] || parent=/
  [ -d "$parent" ] || return 1
  parent=$(CDPATH= cd -- "$parent" && pwd -P) || return 1
  case "$parent" in
    /) printf '/%s\n' "$leaf" ;;
    *) printf '%s/%s\n' "$parent" "$leaf" ;;
  esac
}

configured_data_path_from_disk() {
  load_uninstall_settings
  canonical_path_allow_missing_leaf "$WEB_SCRCPY_DATA_HOST"
}

existing_container_data_path() {
  case "${EXISTING_SCRCPYGATE_DATA_TYPE:-}" in
    bind) ;;
    *) return 1 ;;
  esac
  source=${EXISTING_SCRCPYGATE_DATA_SOURCE:-}
  [ -n "$source" ] || return 1
  candidate=$(canonical_path_allow_missing_leaf "$source") || return 1
  case "$candidate" in
    '/'|"$SCRIPT_DIR"|"${HOME:-}") return 1 ;;
  esac
  case "$SCRIPT_DIR/" in
    "$candidate/"*) return 1 ;;
  esac
  printf '%s\n' "$candidate"
}

existing_container_data_dir() {
  candidate=$(existing_container_data_path) || return 1
  [ -d "$candidate" ] || return 1
  printf '%s\n' "$candidate"
}

resolve_uninstall_data_dir() {
  if ! UNINSTALL_DATA_DIR=$(configured_data_dir_from_disk); then
    UNINSTALL_DATA_DIR=$(configured_data_path_from_disk) \
      || UNINSTALL_DATA_DIR=$(existing_container_data_path) \
      || die "无法从磁盘配置解析数据目录: $WEB_SCRCPY_DATA_HOST"
    if [ -n "${EXISTING_SCRCPYGATE_STATE:-}" ]; then
      actual_data=$(existing_container_data_path) \
        || die "无法验证现有容器的数据挂载目录"
      [ "$actual_data" = "$UNINSTALL_DATA_DIR" ] \
        || die "数据挂载目录不匹配: $actual_data"
    fi
  fi
  case "$UNINSTALL_DATA_DIR" in
    '/'|"$SCRIPT_DIR"|"${HOME:-}") die "拒绝删除不安全的数据目录: $UNINSTALL_DATA_DIR" ;;
  esac
  case "$SCRIPT_DIR/" in
    "$UNINSTALL_DATA_DIR/"*) die "拒绝删除项目目录的父目录: $UNINSTALL_DATA_DIR" ;;
  esac
}

uninstall_data_can_be_removed() {
  [ -d "$UNINSTALL_DATA_DIR" ] || return 1
  case "$UNINSTALL_DATA_DIR" in "$SCRIPT_DIR"/*) return 0 ;; esac
  return 1
}

confirm_permanent_data_deletion() {
  printf '\n%s>%s 数据将永久删除；请输入完整路径 %s 继续: ' "$C_RED" "$C_RESET" "$UNINSTALL_DATA_DIR"
  IFS= read -r answer || return 1
  answer=$(printf '%s' "$answer" | tr -d '\r')
  [ "$answer" = "$UNINSTALL_DATA_DIR" ] || return 1
  current_data_dir=$(configured_data_dir_from_disk) \
    || current_data_dir=$(configured_data_path_from_disk) \
    || return 1
  [ "$current_data_dir" = "$UNINSTALL_DATA_DIR" ]
}

legacy_uninstall_data_is_recognized() {
  # Without container labels or .env, only recognize the default local SQLite
  # database. Never infer ownership of arbitrary directories or symlink targets.
  [ "$UNINSTALL_DATA_DIR" = "$SCRIPT_DIR/data" ] || return 1
  [ ! -L "$SCRIPT_DIR/data" ] || return 1
  [ -f "$UNINSTALL_DATA_DIR/webscrcpy.db" ] || return 1
  [ ! -L "$UNINSTALL_DATA_DIR/webscrcpy.db" ] || return 1
  [ "$(LC_ALL=C od -An -tx1 -N16 "$UNINSTALL_DATA_DIR/webscrcpy.db" 2>/dev/null | tr -d ' \n')" = '53514c69746520666f726d6174203300' ]
}

uninstall_menu_service() {
  # Keep the destructive choice local to this action, including cancellation.
  (
    require_interactive
    panel_top "选择卸载方式"
    panel_line "1（默认）" "保留数据卸载：保留账号、数据库、ALAS 密钥、.env 和镜像"
    panel_line "2" "彻底清理：删除本地数据、密钥、.env 和镜像，重装创建新账号"
    panel_line "0" "取消"
    printf '\n%s>%s 请选择 [1/2/0，默认 1]: ' "$C_YELLOW" "$C_RESET"
    IFS= read -r uninstall_choice || return 0
    uninstall_choice=$(printf '%s' "$uninstall_choice" | tr -d '\r')
    case "$uninstall_choice" in
      ''|1) PURGE=false ;;
      2) PURGE=true ;;
      0) return 0 ;;
      *) warn_msg "无效选项，已取消卸载"; return 0 ;;
    esac
    uninstall_service
  )
}

compose_down_owned_project() (
  unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES COMPOSE_ENV_FILES COMPOSE_PATH_SEPARATOR
  COMPOSE_REMOVE_ORPHANS=0
  export COMPOSE_REMOVE_ORPHANS
  compose --project-name scrcpygate --file "$SCRIPT_DIR/compose.yaml" down
)

uninstall_service() {
  # Menu actions share shell variables; never reuse an earlier resolved path.
  UNINSTALL_DATA_DIR=""
  legacy_cleanup=false
  load_uninstall_settings
  require_docker
  detect_scrcpygate_instance
  if [ -z "$EXISTING_SCRCPYGATE_STATE" ]; then
    if [ "$PURGE" != true ]; then
      success_msg "未检测到服务容器；已有数据、密钥、.env 和镜像均保留"
      warn_msg "保留数据重装可能沿用旧账号和 ALAS Token；全新安装请使用卸载菜单的彻底清理或 --uninstall --purge"
      return 0
    fi
    resolve_uninstall_data_dir
    if [ ! -f .env ] && { [ -e "$WEB_SCRCPY_DATA_HOST" ] || [ -L "$WEB_SCRCPY_DATA_HOST" ]; }; then
      legacy_uninstall_data_is_recognized \
        || die "无容器和 .env，无法确认残留数据归属；已保留全部文件和镜像。请恢复原 .env 并确认数据目录后重试"
      legacy_cleanup=true
    fi
    warn_msg "未检测到服务容器，将继续清理当前项目的残留安装状态"
  else
    existing_instance_can_be_managed || return 1
    resolve_uninstall_data_dir
  fi

  if [ "$PURGE" = true ] && is_interactive; then
    panel_top "彻底清理 ScrcpyGate（不可恢复）"
    panel_line "数据目录" "$UNINSTALL_DATA_DIR"
    panel_line "清理范围" "账号、密码、设备、权限、ALAS Token/密钥、ADB 授权、本地镜像和 .env"
    warn_msg "外置数据目录和项目备份不会自动删除；只有 ALAS 密钥丢失时，可取消并在安装时仅重置 ALAS Token"
    if ! confirm_permanent_data_deletion; then
      warn_msg "确认路径不匹配或已取消，未修改任何文件或容器"
      return 0
    fi
  fi

  if [ -n "$EXISTING_SCRCPYGATE_STATE" ]; then
    if is_interactive && [ "$PURGE" != true ]; then
      if [ -d "$UNINSTALL_DATA_DIR" ]; then
        uninstall_data_label="$UNINSTALL_DATA_DIR（默认保留）"
      else
        uninstall_data_label="$UNINSTALL_DATA_DIR（不存在）"
      fi
      panel_top "卸载 ScrcpyGate"
      panel_line "服务容器" "scrcpygate（将停止并移除）"
      panel_line "本地镜像" "scrcpygate:local（默认保留）"
      panel_line "数据目录" "$uninstall_data_label"
      panel_line "部署配置" ".env（默认保留）"
      print_rule
      warn_msg "删除数据目录会永久移除账号、设备、权限和全部应用配置，删除后不可恢复"
      if ! prompt_confirm_no "确认停止并移除当前目录所属的 ScrcpyGate 服务容器？"; then
        warn_msg "已取消卸载，未修改任何文件或容器"
        return 0
      fi
    fi

    compose_down_owned_project || die "无法移除 ScrcpyGate 服务容器；未继续清理镜像或文件"
    if docker inspect scrcpygate >/dev/null 2>&1; then
      die "Compose 执行完成后容器仍然存在；未继续清理镜像或文件"
    fi
    success_msg "ScrcpyGate 服务容器和 Compose 网络已移除"

    if [ "$PURGE" != true ]; then
      success_msg "已保留本地镜像、数据库、ALAS 密钥和 .env；运行 ./deploy.sh --install 可重装并继续使用原账号"
      return 0
    fi
  fi
  if [ -z "${UNINSTALL_DATA_DIR:-}" ]; then
    resolve_uninstall_data_dir
  fi

  if [ "$legacy_cleanup" = true ]; then
    # Retain retry evidence outside the directory being deleted: interrupted
    # cleanup may already have removed the database used for recognition.
    (umask 077; set -C; printf 'WEB_SCRCPY_DATA_HOST=./data\n' > "$SCRIPT_DIR/.env") \
      || die "无法保存清理重试配置；未继续删除镜像或数据"
  fi

  image_status="不存在"
  cleanup_failed=false
  if docker image inspect scrcpygate:local >/dev/null 2>&1; then
    if docker image rm scrcpygate:local; then
      image_status="已删除"
      success_msg "本地镜像已删除"
    else
      image_status="删除失败"
      cleanup_failed=true
      warn_msg "无法删除镜像 scrcpygate:local，可能仍被其他容器使用；未强制删除"
    fi
  fi

  if [ ! -d "$UNINSTALL_DATA_DIR" ]; then
    data_status="不存在"
    success_msg "数据目录不存在，无需清理"
  elif uninstall_data_can_be_removed; then
    rm -rf -- "$UNINSTALL_DATA_DIR" || die "无法删除数据目录: $UNINSTALL_DATA_DIR"
    data_status="已删除"
    success_msg "数据目录已删除"
  else
    data_status="已保留"
    cleanup_failed=true
    warn_msg "数据目录位于项目目录之外，脚本不会自动删除：$(safe_display "$UNINSTALL_DATA_DIR")"
    warn_msg "确认不再需要后请手工删除该目录"
  fi

  env_status="不存在"
  if [ -f .env ]; then
    if [ "$data_status" = "已保留" ] || [ "$cleanup_failed" = true ]; then
      env_status="已保留"
      warn_msg "清理尚未完成，已保留 .env 以记录原数据目录；处理后可重试 --uninstall --purge"
    else
      rm -f -- "$SCRIPT_DIR/.env" || die "无法删除 .env"
      env_status="已删除"
      success_msg ".env 部署配置已删除"
    fi
  fi

  if [ "$cleanup_failed" = true ]; then
    panel_top "服务已卸载，清理未完成"
  else
    panel_top "卸载完成"
  fi
  panel_line "服务容器" "已移除"
  panel_line "本地镜像" "$image_status"
  panel_line "数据目录" "$data_status"
  panel_line "部署配置" "$env_status"
  print_rule
  [ "$cleanup_failed" = false ]
}

load_menu_settings() {
  if [ -f .env ]; then
    WEB_SCRCPY_BIND=$(dotenv_value WEB_SCRCPY_BIND)
    WEB_SCRCPY_PORT=$(dotenv_value WEB_SCRCPY_PORT)
    WEB_SCRCPY_DATA_HOST=$(dotenv_value WEB_SCRCPY_DATA_HOST)
    PUBLIC_BASE_URL=$(dotenv_value PUBLIC_BASE_URL)
    SCRCPYGATE_SHOW_PUBLIC_HOST=${SCRCPYGATE_SHOW_PUBLIC_HOST:-$(dotenv_value SCRCPYGATE_SHOW_PUBLIC_HOST)}
    SCRCPYGATE_SHOW_PRIVATE_IPS=${SCRCPYGATE_SHOW_PRIVATE_IPS:-$(dotenv_value SCRCPYGATE_SHOW_PRIVATE_IPS)}
  else
    WEB_SCRCPY_BIND=127.0.0.1
    WEB_SCRCPY_PORT=5000
    WEB_SCRCPY_DATA_HOST=./data
    PUBLIC_BASE_URL=http://127.0.0.1:5000
    SCRCPYGATE_SHOW_PUBLIC_HOST=true
    SCRCPYGATE_SHOW_PRIVATE_IPS=true
  fi
}

check_conflicts_service() {
  require_interactive
  ensure_runtime_dependencies
  require_docker
  load_menu_settings
  case "${WEB_SCRCPY_PORT:-}" in
    ''|*[!0-9]*) WEB_SCRCPY_PORT=5000 ;;
  esac
  WEB_SCRCPY_BIND=${WEB_SCRCPY_BIND:-127.0.0.1}

  detect_scrcpygate_instance
  if [ -n "${EXISTING_SCRCPYGATE_STATE:-}" ]; then
    show_existing_scrcpygate
    if scrcpygate_container_identity_is_safe; then
      if prompt_confirm_no "是否停止并移除检测到的旧 scrcpygate 容器？数据目录和镜像会保留"; then
        remove_scrcpygate_container_only || warn_msg "旧容器未能移除"
      else
        warn_msg "已保留旧 scrcpygate 容器"
      fi
    else
      warn_msg "容器标识与 ScrcpyGate 不完全匹配，脚本不会自动删除；请人工确认"
    fi
  else
    success_msg "未检测到名为 scrcpygate 的旧容器"
  fi

  detect_port_occupancy
  detect_port_occupancy_containers
  show_port_occupancy
  if [ -n "$PORT_OCCUPANCY_DETAILS" ]; then
    stop_port_occupants || warn_msg "端口占用未改变；安装前仍需释放该端口"
  fi
}

show_menu() {
  require_interactive
  while :; do
    load_menu_settings

    panel_top "ScrcpyGate 安装与管理面板"
    panel_line "状态" "$(service_status_line)"
    panel_line "访问地址" "$(display_url "$PUBLIC_BASE_URL")"
    panel_line "监听" "${WEB_SCRCPY_BIND}:${WEB_SCRCPY_PORT}"
    panel_line "数据目录" "$WEB_SCRCPY_DATA_HOST"
    panel_line "最近备份" "$(latest_backup_summary)"
    print_rule

    menu_group "安装与更新"
    menu_item 1 "引导配置并安装" "$C_GREEN"
    menu_item 2 "使用当前配置安装/更新" "$C_GREEN"
    menu_item 3 "更新基础镜像并重新安装" "$C_YELLOW"
    menu_item 24 "更新到已发布镜像（GHCR，不重建源码）" "$C_YELLOW"

    menu_group "服务管理"
    menu_item 4 "启动服务" "$C_GREEN"
    menu_item 5 "停止服务" "$C_RED"
    menu_item 6 "重启服务" "$C_YELLOW"
    menu_item 7 "查看状态" "$C_CYAN"
    menu_item 8 "查看最近日志" "$C_CYAN"

    menu_group "配置与账号"
    menu_item 9 "修改部署配置" "$C_CYAN"
    menu_item 10 "重置管理员密码" "$C_RED"

    menu_group "备份与凭据"
    menu_item 16 "备份数据目录" "$C_GREEN"
    menu_item 17 "从备份恢复" "$C_YELLOW"
    menu_item 18 "查看已有备份" "$C_CYAN"
    menu_item 19 "查看 ALAS 令牌迁移状态" "$C_CYAN"
    menu_item 20 "导入/轮换 ALAS 令牌" "$C_YELLOW"
    menu_item 23 "清空 ALAS 令牌（密钥丢失时的恢复出口）" "$C_RED"
    menu_group "诊断与帮助"
    menu_item 11 "检查/安装 Docker 与 Compose" "$C_CYAN"
    menu_item 12 "查看命令帮助" "$C_GRAY"
    menu_item 14 "环境检查" "$C_CYAN"
    menu_item 15 "检测端口占用和旧容器" "$C_YELLOW"
    menu_item 21 "生产边界校验" "$C_CYAN"
    menu_item 22 "生成候选清单（文件哈希 + 镜像 digest）" "$C_GRAY"

    menu_group "卸载"
    menu_item 13 "卸载 ScrcpyGate（保留数据 / 彻底清理）" "$C_RED"
    menu_item 0 "退出" "$C_GRAY"

    printf '\n%s>%s 请选择 / Choose: ' "$C_YELLOW" "$C_RESET"
    IFS= read -r choice || exit 1
    choice=$(printf '%s' "$choice" | tr -d '\r')
    case "$choice" in
      1)
        run_menu_action configure_and_install_flow || true
        pause_menu
        ;;
      2) run_menu_action install_current_service || true; pause_menu ;;
      3) run_menu_action install_with_pull_service || true; pause_menu ;;
      24)
        printf '\n%s>%s 目标镜像引用（留空=按 .env/默认 latest，支持 tag 或 @sha256:digest）: ' "$C_YELLOW" "$C_RESET"
        IFS= read -r image_choice || exit 1
        image_choice=$(printf '%s' "$image_choice" | tr -d '\r')
        UPDATE_IMAGE=$image_choice
        run_menu_action update_service || true
        UPDATE_IMAGE=""
        pause_menu
        ;;
      4) run_menu_action start_service || true; pause_menu ;;
      5) run_menu_action stop_service || true; pause_menu ;;
      6) run_menu_action restart_service || true; pause_menu ;;
      7) (show_status) || true; pause_menu ;;
      8) (show_logs) || true; pause_menu ;;
      9) run_menu_action configure_only_flow || true; pause_menu ;;
      10)
        if prompt_confirm_no "确认重置 admin 密码？"; then run_menu_action reset_admin || true; fi
        pause_menu
        ;;
      11) run_menu_action check_system_dependencies || true; pause_menu ;;
      12) usage; pause_menu ;;
      13)
        run_menu_action uninstall_menu_service || true
        pause_menu
        ;;
      14) (check_service) || true; pause_menu ;;
      15) run_menu_action check_conflicts_service || true; pause_menu ;;
      16)
        printf '\n%s>%s 保留最近几份备份（留空=不清理旧备份）: ' "$C_YELLOW" "$C_RESET"
        IFS= read -r keep_choice || exit 1
        keep_choice=$(printf '%s' "$keep_choice" | tr -d '\r')
        case "$keep_choice" in
          '') BACKUP_KEEP="" ;;
          *[!0-9]*) warn_msg "保留份数必须是正整数，本次不清理旧备份"; BACKUP_KEEP="" ;;
          *) BACKUP_KEEP=$keep_choice ;;
        esac
        run_menu_action backup_service || true
        BACKUP_KEEP=""
        pause_menu
        ;;
      17)
        list_backups_service
        printf '\n%s>%s 备份文件名或完整路径（留空取消）: ' "$C_YELLOW" "$C_RESET"
        IFS= read -r restore_choice || exit 1
        restore_choice=$(printf '%s' "$restore_choice" | tr -d '\r')
        if [ -z "$restore_choice" ]; then
          warn_msg "已取消恢复"
        else
          case "$restore_choice" in
            /*|*/*) RESTORE_ARCHIVE=$restore_choice ;;
            *) RESTORE_ARCHIVE="$(backup_dir_default)/$restore_choice" ;;
          esac
          run_menu_action restore_service || true
        fi
        pause_menu
        ;;
      18) list_backups_service; pause_menu ;;
      19) run_menu_action token_status_service || true; pause_menu ;;
      20)
        if prompt_confirm_no "确认导入/轮换 ALAS 令牌？"; then run_menu_action migrate_alas_token_service || true; fi
        pause_menu
        ;;
      21) (check_production_boundary) || true; pause_menu ;;
      23)
        if prompt_confirm_no "确认清空 ALAS 令牌？（除非你有对应密钥备份，否则无法恢复）"; then
          run_menu_action clear_alas_token_service || true
        else
          warn_msg "已取消"
        fi
        pause_menu
        ;;
      22)
        printf '\n%s>%s 清单输出路径（留空=写入 output/release-manifest/…）: ' "$C_YELLOW" "$C_RESET"
        IFS= read -r manifest_choice || exit 1
        manifest_choice=$(printf '%s' "$manifest_choice" | tr -d '\r')
        CANDIDATE_MANIFEST=$manifest_choice
        run_menu_action candidate_manifest_service || true
        CANDIDATE_MANIFEST=""
        pause_menu
        ;;
      0|q|Q|quit|exit) exit 0 ;;
      *) error_msg "无效选项 / invalid choice: $choice"; pause_menu ;;
    esac
  done
}

if [ "$ACTION" = auto ]; then
  if is_interactive; then ACTION=menu; else ACTION=install; fi
fi

# Direct mutating actions hold the lock for their whole lifecycle.  The
# interactive menu acquires it per action so an idle menu does not block a
# second operator from running a read-only command.
case "$ACTION" in
  configure|install|update|start|stop|restart|reset_admin|uninstall|migrate_alas_token|clear_alas_token|candidate_manifest|check_conflicts|restore)
    acquire_deploy_lock
    ;;
esac

case "$ACTION" in
  menu) show_menu ;;
  configure) configure_only_flow ;;
  install) install_service ;;
  update) update_service ;;
  start) start_service ;;
  stop) stop_service ;;
  restart) restart_service ;;
  status) show_status ;;
  logs) show_logs ;;
  reset_admin) reset_admin ;;
  backup) backup_service ;;
  list_backups) list_backups_service ;;
  restore) restore_service ;;
  uninstall) uninstall_service ;;
  token_status) token_status_service ;;
  migrate_alas_token) migrate_alas_token_service ;;
  clear_alas_token) clear_alas_token_service ;;
  candidate_manifest) candidate_manifest_service ;;
  check) check_service ;;
  check_conflicts) check_conflicts_service ;;
  check_production) check_production_boundary ;;
  *) die "unsupported action: $ACTION" ;;
esac
