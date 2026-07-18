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

safe_display() {
  value=${1:-}
  case "${SCRCPYGATE_SHOW_PRIVATE_IPS:-false}" in 1|true|TRUE|yes|YES|on|ON) printf '%s\n' "$value"; return 0 ;; esac
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

服务管理:
  ./deploy.sh --start | --stop | --restart
  ./deploy.sh --status | --logs | --reset-admin
  ./deploy.sh --uninstall    安全移除当前服务；交互模式可选择清理本地文件

其他:
  ./deploy.sh --help

交互模式会在安装系统软件前显示命令并请求确认。
非交互模式仅在使用 --install-deps 或设置
SCRCPYGATE_AUTO_INSTALL_DEPS=true 时安装系统软件。

Existing .env files, databases, users, and passwords are never overwritten.
EOF
}

ACTION=auto
skip_build=false
pull_images=false
auto_install_deps=${SCRCPYGATE_AUTO_INSTALL_DEPS:-false}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --menu) ACTION=menu ;;
    --configure) ACTION=configure ;;
    --install) ACTION=install ;;
    --install-deps) ACTION=install; auto_install_deps=true ;;
    --pull) ACTION=install; pull_images=true ;;
    --skip-build) ACTION=install; skip_build=true ;;
    --start) ACTION=start ;;
    --stop) ACTION=stop ;;
    --restart) ACTION=restart ;;
    --status) ACTION=status ;;
    --logs) ACTION=logs ;;
    --reset-admin) ACTION=reset_admin ;;
    --uninstall) ACTION=uninstall ;;
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
  ALLOW_NULL_ORIGIN=${ALLOW_NULL_ORIGIN:-$(dotenv_value ALLOW_NULL_ORIGIN)}
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
  ALLOW_NULL_ORIGIN=${ALLOW_NULL_ORIGIN:-false}
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
  export PUBLIC_BASE_URL ALLOWED_HOSTS ALLOWED_ORIGINS ALLOW_NULL_ORIGIN
  export TRUST_PROXY TRUSTED_PROXY_IPS SESSION_COOKIE_SECURE
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
    detected=$(ifconfig 2>/dev/null | awk '/inet / { if ($2 != "127.0.0.1") { print $2; exit } }' || true)
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
  case "$mode" in
    1)
      new_bind=127.0.0.1
      new_public="http://127.0.0.1:${new_port}"
      new_hosts="127.0.0.1,localhost"
      new_origins=$new_public
      new_allow_null=false
      new_trust=false
      new_secure=false
      ;;
    2)
      current_host=$(public_host_from_url "$PUBLIC_BASE_URL")
      case "$current_host" in ''|127.0.0.1|localhost) current_host=$system_ip ;; esac
      prompt_value "服务器局域网 IP 或域名（不含协议、路径和端口）" "$current_host" || return 1
      lan_host=$PROMPT_RESULT
      valid_host_input "$lan_host" || { error_msg "请输入纯 IP 或域名，不要包含协议、路径或端口"; return 1; }
      new_bind=0.0.0.0
      new_public=$(build_public_url http "$lan_host" "$new_port")
      new_hosts="127.0.0.1,localhost,${lan_host}"
      new_origins=$new_public
      new_allow_null=false
      new_trust=false
      new_secure=false
      ;;
    3)
      proxy_bind=$WEB_SCRCPY_BIND
      case "$proxy_bind" in 0.0.0.0|127.0.0.1|'') proxy_bind=$system_ip ;; esac
      prompt_value "反向代理连接的内部绑定 IP" "$proxy_bind" || return 1
      new_bind=$PROMPT_RESULT
      valid_ipv4 "$new_bind" || { error_msg "内部绑定地址必须是有效 IPv4"; return 1; }

      proxy_host=$(public_host_from_url "$PUBLIC_BASE_URL")
      case "$proxy_host" in ''|127.0.0.1|localhost|"$system_ip") proxy_host="" ;; esac
      if [ -n "$proxy_host" ]; then
        prompt_value "外部域名或 IP（示例：example.com；不要输入 http://、https://、路径或端口）" "$proxy_host" || return 1
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
      prompt_value "是否兼容 WAF 的 Origin: null（true/false，通常保持 false）" "$ALLOW_NULL_ORIGIN" || return 1
      new_allow_null=$PROMPT_RESULT
      case "$new_allow_null" in true|false) ;; *) error_msg "ALLOW_NULL_ORIGIN 只能是 true 或 false"; return 1 ;; esac
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
  panel_line "模式" "$mode"
  panel_line "监听" "${new_bind}:${new_port}"
  panel_line "访问地址" "$new_public"
  panel_line "数据目录" "$new_data"
  panel_line "信任代理" "$new_trust"
  panel_line "允许 Null Origin" "$new_allow_null"
  panel_line "安全 Cookie" "$new_secure"
  print_rule
  prompt_confirm "保存以上配置？" || { warn_msg "已取消"; return 1; }

  set_env_value WEB_SCRCPY_BIND "$new_bind"
  set_env_value WEB_SCRCPY_PORT "$new_port"
  set_env_value WEB_SCRCPY_DATA_HOST "$new_data"
  set_env_value PUBLIC_BASE_URL "$new_public"
  set_env_value ALLOWED_HOSTS "$new_hosts"
  set_env_value ALLOWED_ORIGINS "$new_origins"
  set_env_value ALLOW_NULL_ORIGIN "$new_allow_null"
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
  if [ -z "${ownership_error:-}" ] && [ "$EXISTING_SCRCPYGATE_IMAGE" != scrcpygate:local ]; then ownership_error="容器镜像不匹配"; fi
  if [ -z "${ownership_error:-}" ]; then
    if expected_data=$(configured_data_dir_from_disk); then
      if actual_data=$(existing_container_data_dir); then
        EXISTING_SCRCPYGATE_DATA_SOURCE=$actual_data
      else
        ownership_error="无法从磁盘配置解析数据目录"
      fi
    elif expected_data=$(existing_container_data_dir); then
      WEB_SCRCPY_DATA_HOST=$expected_data
      EXISTING_SCRCPYGATE_DATA_SOURCE=$expected_data
      success_msg "未找到可用的 .env 数据目录，已恢复现有容器挂载: $(safe_display "$expected_data")"
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
      reset_output=$(compose run --rm --no-deps scrcpygate python -m app.cli reset-admin) \
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

apply_configuration_to_running_instance() {
  prepare_deployment
  prepare_data_directory
  ensure_data_permissions
  log "正在应用新配置并重启 ScrcpyGate..."
  compose up -d --force-recreate scrcpygate
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
  detect_scrcpygate_instance
  if [ -n "$EXISTING_SCRCPYGATE_STATE" ]; then
    existing_instance_can_be_managed || return 0
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
  fi
  install_service
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
  success_msg "安装/更新完成：$(safe_display "$PUBLIC_BASE_URL")"
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
  compose up -d --force-recreate scrcpygate
  wait_for_health
  success_msg "ScrcpyGate 已重建并应用当前配置"
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
  reset_output=$(compose exec -T scrcpygate python -m app.cli reset-admin) || die "管理员密码重置失败"
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

load_uninstall_settings() {
  if [ -f .env ]; then
    WEB_SCRCPY_DATA_HOST=$(dotenv_value WEB_SCRCPY_DATA_HOST)
  else
    WEB_SCRCPY_DATA_HOST=./data
  fi
  WEB_SCRCPY_DATA_HOST=${WEB_SCRCPY_DATA_HOST:-./data}
}

configured_data_dir_from_disk() {
  load_uninstall_settings
  [ -d "$WEB_SCRCPY_DATA_HOST" ] || return 1
  (CDPATH= cd -- "$WEB_SCRCPY_DATA_HOST" && pwd -P)
}

existing_container_data_dir() {
  case "${EXISTING_SCRCPYGATE_DATA_TYPE:-}" in
    bind) ;;
    *) return 1 ;;
  esac
  source=${EXISTING_SCRCPYGATE_DATA_SOURCE:-}
  [ -n "$source" ] || return 1
  [ -d "$source" ] || return 1
  candidate=$(CDPATH= cd -- "$source" && pwd -P) || return 1
  case "$candidate" in
    '/'|"$SCRIPT_DIR"|"${HOME:-}") return 1 ;;
  esac
  case "$SCRIPT_DIR/" in
    "$candidate/"*) return 1 ;;
  esac
  printf '%s\n' "$candidate"
}

resolve_uninstall_data_dir() {
  UNINSTALL_DATA_DIR=$(configured_data_dir_from_disk) \
    || die "无法从磁盘配置解析数据目录: $WEB_SCRCPY_DATA_HOST"
  case "$UNINSTALL_DATA_DIR" in
    '/'|"$SCRIPT_DIR"|"${HOME:-}") die "拒绝删除不安全的数据目录: $UNINSTALL_DATA_DIR" ;;
  esac
  case "$SCRIPT_DIR/" in
    "$UNINSTALL_DATA_DIR/"*) die "拒绝删除项目目录的父目录: $UNINSTALL_DATA_DIR" ;;
  esac
}

uninstall_data_can_be_removed() {
  case "$UNINSTALL_DATA_DIR" in "$SCRIPT_DIR"/*) return 0 ;; esac
  return 1
}

confirm_permanent_data_deletion() {
  printf '\n%s>%s 数据将永久删除；请输入完整路径 %s 继续: ' "$C_RED" "$C_RESET" "$UNINSTALL_DATA_DIR"
  IFS= read -r answer || return 1
  answer=$(printf '%s' "$answer" | tr -d '\r')
  [ "$answer" = "$UNINSTALL_DATA_DIR" ] || return 1
  current_data_dir=$(configured_data_dir_from_disk) || return 1
  [ "$current_data_dir" = "$UNINSTALL_DATA_DIR" ]
}

compose_down_owned_project() (
  unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES COMPOSE_ENV_FILES COMPOSE_PATH_SEPARATOR
  COMPOSE_REMOVE_ORPHANS=0
  export COMPOSE_REMOVE_ORPHANS
  compose --project-name scrcpygate --file "$SCRIPT_DIR/compose.yaml" down
)

uninstall_service() {
  load_uninstall_settings
  require_docker
  detect_scrcpygate_instance
  if [ -z "$EXISTING_SCRCPYGATE_STATE" ]; then
    success_msg "未检测到当前目录所属的 ScrcpyGate 服务容器，无需卸载"
    return 0
  fi
  existing_instance_can_be_managed || return 1
  resolve_uninstall_data_dir

  if is_interactive; then
    panel_top "卸载 ScrcpyGate"
    panel_line "服务容器" "scrcpygate（将停止并移除）"
    panel_line "本地镜像" "scrcpygate:local（默认保留）"
    panel_line "数据目录" "$UNINSTALL_DATA_DIR（默认保留）"
    panel_line "部署配置" ".env（默认保留）"
    print_rule
    warn_msg "删除数据目录会永久移除账号、设备、权限和全部应用配置"
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

  if ! is_interactive; then
    success_msg "非交互卸载已保留本地镜像、数据目录和 .env"
    return 0
  fi

  image_status="不存在"
  cleanup_failed=false
  if docker image inspect scrcpygate:local >/dev/null 2>&1; then
    if prompt_confirm_no "是否删除本地镜像 scrcpygate:local？下次安装需要重新构建"; then
      if docker image rm scrcpygate:local; then
        image_status="已删除"
        success_msg "本地镜像已删除"
      else
        image_status="删除失败"
        cleanup_failed=true
        warn_msg "无法删除镜像 scrcpygate:local，可能仍被其他容器使用；未强制删除"
      fi
    else
      image_status="已保留"
      success_msg "已保留本地镜像 scrcpygate:local"
    fi
  fi

  data_status="已保留"
  if uninstall_data_can_be_removed; then
    if prompt_confirm_no "是否删除数据目录 ${UNINSTALL_DATA_DIR}？此操作不可恢复"; then
      if ! confirm_permanent_data_deletion; then
        warn_msg "确认路径不匹配或目录已变化，已保留数据目录"
      else
        rm -rf -- "$UNINSTALL_DATA_DIR" || die "无法删除数据目录: $UNINSTALL_DATA_DIR"
        data_status="已删除"
        success_msg "数据目录已删除"
      fi
    else
      success_msg "已保留数据目录"
    fi
  else
    warn_msg "数据目录位于项目目录之外，脚本不会自动删除：$(safe_display "$UNINSTALL_DATA_DIR")"
    warn_msg "确认不再需要后请手工备份并删除该目录"
  fi

  env_status="不存在"
  if [ -f .env ]; then
    if prompt_confirm_no "是否删除 .env 部署配置？"; then
      rm -f -- "$SCRIPT_DIR/.env" || die "无法删除 .env"
      env_status="已删除"
      success_msg ".env 部署配置已删除"
    else
      env_status="已保留"
      success_msg "已保留 .env 部署配置"
    fi
  fi

  panel_top "卸载完成"
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
  else
    WEB_SCRCPY_BIND=127.0.0.1
    WEB_SCRCPY_PORT=5000
    WEB_SCRCPY_DATA_HOST=./data
    PUBLIC_BASE_URL=http://127.0.0.1:5000
  fi
}

show_menu() {
  require_interactive
  while :; do
    load_menu_settings

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
    menu_item 11 "检查/安装 Docker 与 Compose" "$C_CYAN"
    menu_item 12 "查看命令帮助" "$C_GRAY"
    menu_item 13 "卸载 ScrcpyGate" "$C_RED"
    menu_item 0 "退出" "$C_GRAY"

    printf '\n%s>%s 请选择 / Choose: ' "$C_YELLOW" "$C_RESET"
    IFS= read -r choice || exit 1
    choice=$(printf '%s' "$choice" | tr -d '\r')
    case "$choice" in
      1)
        (skip_build=false; pull_images=false; configure_and_install_flow) || true
        pause_menu
        ;;
      2) (skip_build=false; pull_images=false; install_service) || true; pause_menu ;;
      3) (skip_build=false; pull_images=true; install_service) || true; pause_menu ;;
      4) (start_service) || true; pause_menu ;;
      5) (stop_service) || true; pause_menu ;;
      6) (restart_service) || true; pause_menu ;;
      7) (show_status) || true; pause_menu ;;
      8) (show_logs) || true; pause_menu ;;
      9) (configure_only_flow) || true; pause_menu ;;
      10)
        if prompt_confirm "确认重置 admin 密码？"; then (reset_admin) || true; fi
        pause_menu
        ;;
      11) (check_system_dependencies) || true; pause_menu ;;
      12) usage; pause_menu ;;
      13)
        (uninstall_service) || true
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

case "$ACTION" in
  menu) show_menu ;;
  configure) configure_only_flow ;;
  install) install_service ;;
  start) start_service ;;
  stop) stop_service ;;
  restart) restart_service ;;
  status) show_status ;;
  logs) show_logs ;;
  reset_admin) reset_admin ;;
  uninstall) uninstall_service ;;
  *) die "unsupported action: $ACTION" ;;
esac
