#!/bin/sh
# 按镜像 digest 部署 ScrcpyGate：拉取 → 切换 .env 里的镜像引用 → 重建 → 健康门 → 失败自动回滚。
#
# 为什么需要它：CI 构建出来的是 ghcr.io/<owner>/scrcpygate@sha256:<digest>，目标机上如果只
# `docker compose up -d`，用的是本地 build 出来的 tag —— 「CI 里验证过的产物」和「线上跑的
# 东西」就不是同一个字节。这个脚本让部署指向不可变的 digest，并且健康检查不过就自动退回上一版。
#
# 用法：
#   sh tools/deploy_release.sh --image <ref> [--compose-dir DIR] [--service NAME] [--project NAME]
#                             [--env-key KEY] [--health-url URL] [--timeout SECONDS] [--dry-run]
#
# 退出码：0 成功；2 健康检查失败但已回滚到上一版；3 回滚后仍不健康（或没有可回滚的版本）；
#        1 参数/环境错误（缺 docker/curl、compose.yaml 不支持 image 变量等）。
set -eu

IMAGE=""
COMPOSE_DIR="$(pwd)"
SERVICE="scrcpygate"
PROJECT="scrcpygate"
ENV_KEY="SCRCPYGATE_IMAGE"
HEALTH_URL="http://127.0.0.1:5000/healthz"
TIMEOUT=120
DRY_RUN=0
ENV_FILE=""

usage() {
  cat <<'USAGE'
用法: deploy_release.sh --image <镜像引用> [选项]
  --image REF         要部署的镜像（建议带 digest：ghcr.io/owner/name@sha256:...）
  --compose-dir DIR   部署目录（默认当前目录，需含 compose.yaml 与 .env）
  --service NAME      compose 服务名（默认 scrcpygate）
  --project NAME      compose 项目名（默认 scrcpygate）
  --env-key KEY       .env 里存镜像引用的键（默认 SCRCPYGATE_IMAGE）
  --health-url URL    健康检查地址（默认 http://127.0.0.1:5000/healthz）
  --timeout SECONDS   健康检查最长等待秒数（默认 120）
  --dry-run           只打印将要做什么，不动 .env、不调用 docker
USAGE
}

log() { printf '[deploy-release] %s\n' "$*"; }
die() { printf '[deploy-release] 错误：%s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --image) [ $# -ge 2 ] || die "--image 需要参数"; IMAGE=$2; shift 2 ;;
    --compose-dir) [ $# -ge 2 ] || die "--compose-dir 需要参数"; COMPOSE_DIR=$2; shift 2 ;;
    --service) [ $# -ge 2 ] || die "--service 需要参数"; SERVICE=$2; shift 2 ;;
    --project) [ $# -ge 2 ] || die "--project 需要参数"; PROJECT=$2; shift 2 ;;
    --env-key) [ $# -ge 2 ] || die "--env-key 需要参数"; ENV_KEY=$2; shift 2 ;;
    --health-url) [ $# -ge 2 ] || die "--health-url 需要参数"; HEALTH_URL=$2; shift 2 ;;
    --timeout) [ $# -ge 2 ] || die "--timeout 需要参数"; TIMEOUT=$2; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "未知参数：$1" ;;
  esac
done

[ -n "$IMAGE" ] || { usage >&2; die "缺少 --image"; }
[ -d "$COMPOSE_DIR" ] || die "找不到部署目录：$COMPOSE_DIR"
[ -f "$COMPOSE_DIR/compose.yaml" ] || die "部署目录里没有 compose.yaml：$COMPOSE_DIR"
command -v docker >/dev/null 2>&1 || die "找不到 docker"
command -v curl >/dev/null 2>&1 || die "找不到 curl"
case "$TIMEOUT" in ''|*[!0-9]*) die "--timeout 必须是整数秒" ;; esac

ENV_FILE="$COMPOSE_DIR/.env"

# compose.yaml 必须真的插值这个键，否则写 .env 只是自我安慰（线上仍跑旧的 build tag）。
grep -q "\${$ENV_KEY" "$COMPOSE_DIR/compose.yaml" \
  || die "compose.yaml 没有使用 \${$ENV_KEY:-...} 作为 image，无法按引用部署（先同步新版 compose.yaml）"

env_value() { # FILE KEY
  [ -f "$1" ] || return 0
  sed -n "s/^[[:space:]]*$2=//p" "$1" | tail -n 1
}

set_env_value() { # FILE KEY VALUE
  file=$1 key=$2 value=$3
  if [ -f "$file" ] && grep -q "^[[:space:]]*$key=" "$file"; then
    tmp="$file.tmp.$$"
    awk -v key="$key" -v value="$value" '
      BEGIN { done = 0 }
      $0 ~ "^[[:space:]]*" key "=" { print key "=" value; done = 1; next }
      { print }
      END { if (!done) print key "=" value }
    ' "$file" > "$tmp"
    mv "$tmp" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

previous_image() {
  running=$(docker inspect "$PROJECT" --format '{{.Config.Image}}' 2>/dev/null || true)
  if [ -n "$running" ]; then printf '%s\n' "$running"; return 0; fi
  env_value "$ENV_FILE" "$ENV_KEY"
}

health_ok() {
  deadline=$(( $(date +%s) + TIMEOUT ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || true)
    if [ "$code" = "200" ]; then return 0; fi
    sleep 2
  done
  return 1
}

apply_image() { # REF
  ref=$1
  set_env_value "$ENV_FILE" "$ENV_KEY" "$ref"
  if ! docker image inspect "$ref" >/dev/null 2>&1; then
    log "拉取 $ref"
    docker pull "$ref" >/dev/null
  else
    log "本地已有 $ref，跳过拉取"
  fi
  log "重建服务 $SERVICE（compose 项目 $PROJECT）"
  ( cd "$COMPOSE_DIR" && docker compose -p "$PROJECT" up -d --no-build "$SERVICE" )
}

PREVIOUS=$(previous_image || true)
log "当前版本：${PREVIOUS:-<未知>}"
log "目标版本：$IMAGE"

if [ "$DRY_RUN" = "1" ]; then
  log "dry-run：会在 $ENV_FILE 写入 $ENV_KEY=$IMAGE，拉取/重建 $SERVICE，然后对 $HEALTH_URL 做 ${TIMEOUT}s 健康检查（失败则回滚到 ${PREVIOUS:-<无>}）"
  exit 0
fi

if [ -f "$ENV_FILE" ]; then
  backup="$ENV_FILE.release-$(date +%Y%m%d-%H%M%S).bak"
  cp "$ENV_FILE" "$backup"
  log "已备份 .env → $backup"
fi

if ! apply_image "$IMAGE"; then
  log "应用 $IMAGE 失败"
else
  log "等待 $HEALTH_URL 返回 200（最多 ${TIMEOUT}s）"
  if health_ok; then
    running=$(docker inspect "$PROJECT" --format '{{.Image}}' 2>/dev/null || true)
    log "部署成功：$IMAGE（容器镜像 ${running:-?}）"
    exit 0
  fi
  log "健康检查未通过"
fi

if [ -z "$PREVIOUS" ] || [ "$PREVIOUS" = "$IMAGE" ]; then
  die "没有可回滚的上一版（之前记录的版本：${PREVIOUS:-<空>}），服务可能处于不可用状态"
fi

log "回滚到 $PREVIOUS"
apply_image "$PREVIOUS" || true
if health_ok; then
  log "已回滚到 $PREVIOUS 并恢复健康；本次发布失败"
  exit 2
fi

log "回滚后仍不健康，请人工介入"
exit 3
