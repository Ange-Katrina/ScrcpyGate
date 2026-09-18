#!/bin/sh
# Exercise the built image without real devices, credentials, or server logs.
set -eu

image=${1:?Usage: ci_smoke.sh IMAGE}
name=scrcpygate-ci-smoke
volume="scrcpygate-ci-smoke-data-$$"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
cleanup_all() { cleanup; docker volume rm "$volume" >/dev/null 2>&1 || true; }
trap cleanup_all EXIT
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
            docker exec -e EXPECTED_VERSION="${EXPECTED_VERSION:-}" "$name" python -c '
import os, shutil, subprocess
from pathlib import Path
from adb_manager import ADBManager
from app.version import get_version, validate_version
version = validate_version(Path("/app/VERSION").read_text().strip())
assert get_version() == version
if os.environ.get("EXPECTED_VERSION"):
    assert version == os.environ["EXPECTED_VERSION"]
adb = ADBManager().adb_path
assert adb == shutil.which("adb") and os.access(adb, os.X_OK)
subprocess.run([adb, "version"], check=True, stdout=subprocess.DEVNULL)
assert not os.path.exists("/app/tools")
assert not os.path.exists("/app/tests")
assert "Apache License" in Path("/app/LICENSE").read_text()
for notice in ("THIRD_PARTY.md", "adb/linux/NOTICE.txt", "static/vendor/JMUXER_LICENSE", "static/icons/LUCIDE_LICENSE"):
    assert Path("/app", notice).stat().st_size > 0
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

docker volume create "$volume" >/dev/null
docker run --detach --name "$name" \
    --mount "type=volume,source=$volume,target=/app/data" \
    --cap-drop ALL --security-opt no-new-privileges \
    -e SESSION_COOKIE_SECURE=false \
    -e ENABLE_API_DOCS=false \
    -e ALLOWED_HOSTS=127.0.0.1,localhost \
    -e PUBLIC_BASE_URL=http://127.0.0.1:15077 \
    -p 127.0.0.1:15077:5000 "$image" >/dev/null
check_running http://127.0.0.1:15077
# Only synthetic CI identities are retained in this disposable volume.
docker exec "$name" python -c '
import hashlib, json, os, subprocess
from pathlib import Path
assert os.environ["HOME"] == "/app/data"
adb_dir = Path.home() / ".android"
adb_dir.mkdir(mode=0o700, exist_ok=True)
adb_key = adb_dir / "adbkey"
if not adb_key.exists():
    subprocess.run(["adb", "keygen", str(adb_key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
paths = [Path("/app/data/.alas-token-encryption-key"), adb_key]
snapshot = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
Path("/app/data/.ci-identities.json").write_text(json.dumps(snapshot))
'
cleanup

docker run --detach --name "$name" --network host \
    --mount "type=volume,source=$volume,target=/app/data" \
    --cap-drop ALL --security-opt no-new-privileges \
    -e SESSION_COOKIE_SECURE=false \
    -e ENABLE_API_DOCS=false \
    -e ALLOWED_HOSTS=127.0.0.1,localhost \
    -e WEB_SCRCPY_BIND=127.0.0.1 \
    -e WEB_SCRCPY_PORT=15078 \
    -e PUBLIC_BASE_URL=http://127.0.0.1:15078 "$image" >/dev/null
check_running http://127.0.0.1:15078
docker exec "$name" python -c '
import hashlib, json
from pathlib import Path
snapshot = json.loads(Path("/app/data/.ci-identities.json").read_text())
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p, digest in snapshot.items())
'
echo "Bridge, host-network and persisted identity smoke checks passed"
