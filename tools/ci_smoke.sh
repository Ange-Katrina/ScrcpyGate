#!/bin/sh
# Exercise the built image without real devices, credentials, or server logs.
set -eu

image=${1:?Usage: ci_smoke.sh IMAGE}
name=scrcpygate-ci-smoke
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

check_running() {
    base_url=$1
    remaining=60
    while [ "$remaining" -gt 0 ]; do
        health=$(docker inspect --format '{{.State.Health.Status}}' "$name")
        if [ "$health" = healthy ] \
            && curl --fail --silent --max-time 5 "$base_url/healthz" >/dev/null \
            && curl --fail --silent --max-time 5 "$base_url/login" >/dev/null; then
            [ "$(docker exec "$name" id -u)" != 0 ]
            docker exec "$name" python -c '
import os, shutil, subprocess
from adb_manager import ADBManager
adb = ADBManager().adb_path
assert adb == shutil.which("adb") and os.access(adb, os.X_OK)
subprocess.run([adb, "version"], check=True, stdout=subprocess.DEVNULL)
assert not os.path.exists("/app/tools")
assert not os.path.exists("/app/tests")
'
            return 0
        fi
        running=$(docker inspect --format '{{.State.Running}}' "$name")
        [ "$running" = true ] || break
        remaining=$((remaining - 1))
        sleep 2
    done
    echo "Container health, login, or runtime checks failed; runtime logs are intentionally omitted." >&2
    return 1
}

docker run --detach --name "$name" \
    --cap-drop ALL --security-opt no-new-privileges \
    -e SESSION_COOKIE_SECURE=false \
    -e ENABLE_API_DOCS=false \
    -e ALLOWED_HOSTS=127.0.0.1,localhost \
    -e PUBLIC_BASE_URL=http://127.0.0.1:15077 \
    -p 127.0.0.1:15077:5000 "$image" >/dev/null
check_running http://127.0.0.1:15077
cleanup

docker run --detach --name "$name" --network host \
    --cap-drop ALL --security-opt no-new-privileges \
    -e SESSION_COOKIE_SECURE=false \
    -e ENABLE_API_DOCS=false \
    -e ALLOWED_HOSTS=127.0.0.1,localhost \
    -e WEB_SCRCPY_BIND=127.0.0.1 \
    -e WEB_SCRCPY_PORT=15078 \
    -e PUBLIC_BASE_URL=http://127.0.0.1:15078 "$image" >/dev/null
check_running http://127.0.0.1:15078
echo "Bridge and host-network smoke checks passed"
