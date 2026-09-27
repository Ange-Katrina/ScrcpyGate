#!/bin/sh
# Deploy an already-published image through the target's existing deployment script.
set -eu

die() { printf '%s\n' "$1" >&2; exit 1; }
[ -n "${DEPLOY_IMAGE:-}" ] || die 'Missing DEPLOY_IMAGE.'
[ -n "${DEPLOY_HOST:-}" ] || die 'Missing SCRCPYGATE_DEPLOY_HOST.'
[ -n "${DEPLOY_USER:-}" ] || die 'Missing SCRCPYGATE_DEPLOY_USER.'
[ -n "${DEPLOY_DIR:-}" ] || die 'Missing SCRCPYGATE_DEPLOY_DIR.'
[ -n "${DEPLOY_SSH_KEY:-}" ] || die 'Missing SCRCPYGATE_DEPLOY_SSH_KEY.'
[ -n "${DEPLOY_KNOWN_HOSTS:-}" ] || die 'Missing SCRCPYGATE_DEPLOY_KNOWN_HOSTS.'

printf '%s\n' "$DEPLOY_IMAGE" | grep -Eq '^ghcr\.io/[a-z0-9_.-]+/scrcpygate@sha256:[0-9a-f]{64}$' \
  || die 'The published image reference is invalid.'
printf '%s\n' "$DEPLOY_HOST" | grep -Eq '^[a-zA-Z0-9][a-zA-Z0-9.-]*$' \
  || die 'The SSH host is invalid.'
printf '%s\n' "$DEPLOY_USER" | grep -Eq '^[a-zA-Z_][a-zA-Z0-9_-]*$' \
  || die 'The SSH user is invalid.'
printf '%s\n' "$DEPLOY_DIR" | grep -Eq '^/[a-zA-Z0-9_./-]+$' \
  || die 'The deployment directory must be an absolute path without spaces.'
case "$DEPLOY_DIR" in
  */../*|*/..) die 'The deployment directory cannot contain parent traversal.' ;;
esac
DEPLOY_PORT=${DEPLOY_PORT:-22}
case "$DEPLOY_PORT" in ''|*[!0-9]*) die 'The SSH port must be between 1 and 65535.' ;; esac
[ "$DEPLOY_PORT" -ge 1 ] && [ "$DEPLOY_PORT" -le 65535 ] \
  || die 'The SSH port must be between 1 and 65535.'

umask 077
temporary_dir=$(mktemp -d)
trap 'rm -rf -- "$temporary_dir"' EXIT
printf '%s\n' "$DEPLOY_SSH_KEY" > "$temporary_dir/key"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$temporary_dir/known_hosts"

ssh -T -i "$temporary_dir/key" -p "$DEPLOY_PORT" \
  -o BatchMode=yes -o ConnectTimeout=15 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$temporary_dir/known_hosts" \
  "$DEPLOY_USER@$DEPLOY_HOST" "sh -s -- '$DEPLOY_IMAGE' '$DEPLOY_DIR'" <<'REMOTE'
set -eu
image=$1
deployment_dir=$2
cd "$deployment_dir"
test -f deploy.sh && test -f compose.yaml && test -f .env || {
  printf 'The target is not an existing bridge deployment.\n' >&2
  exit 1
}
exec sh ./deploy.sh --update --image "$image"
REMOTE
