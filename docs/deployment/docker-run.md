# Advanced deployment: `docker run`

[English](docker-run.md) · [简体中文](docker-run.zh.md) · [Back to README](../../README.md)

The maintained deployment path is [Docker Compose](../../README.md#docker-compose). This page is
for hosts where Compose is unavailable or unwanted: it mirrors the two compose files with plain
`docker run` commands. Everything here runs the **same image** and the **same `app.main:app`** —
only networking and the lifecycle tooling differ.

> [!NOTE]
> If you take this path, upgrades, restarts at boot and rollbacks are yours to handle. Compose
> costs one small file and gives you `up -d`, `pull`, `logs -f` and `restart: unless-stopped`
> for free, so use it unless something prevents you.

## 1. Build the image

`docker run` does not build, so build once first — from a checkout of this repository:

```sh
git clone https://github.com/Ange-Katrina/ScrcpyGate.git
cd ScrcpyGate
docker build -t scrcpygate:local .                 # slow network: add --build-arg PIP_INDEX_URL=<mirror>
```

Alternatively pin a published image instead of building, if your host can pull it:

```sh
docker pull ghcr.io/<owner>/scrcpygate@sha256:<digest>
```

The GHCR package is **private** by default: sign in first with a personal access token that has
`read:packages` (`docker login ghcr.io`), or switch the package visibility to public in the
repository settings.

## 2. Prepare the data directory

The container runs as `uid 100(app):gid 101(app)`. The host directory you mount must be writable
by that user, otherwise the app exits at startup with a `PermissionError` on
`/app/data/.init-db.lock` and the container restarts in a loop:

```sh
mkdir -p ./data && sudo chown -R 100:101 ./data
```

## 3. Bridge networking with a published port

This is what [`compose.yaml`](../../compose.yaml) does. Read the first-run admin password without
echoing it — at least `MIN_PASSWORD_LENGTH` (12) characters — and pass the variable **by name**
so the password never appears on the command line or in your shell history:

```sh
read -rs INITIAL_ADMIN_PASSWORD && export INITIAL_ADMIN_PASSWORD

docker run -d --name scrcpygate --restart unless-stopped \
  -p 127.0.0.1:5000:5000 \
  -e INITIAL_ADMIN_PASSWORD \
  -e PUBLIC_BASE_URL=http://127.0.0.1:5000 \
  -e ALLOWED_HOSTS=127.0.0.1,localhost \
  -e SESSION_COOKIE_SECURE=false \
  -v "$PWD/data:/app/data" \
  scrcpygate:local
```

Then open `http://127.0.0.1:5000`. Change `-p` and `PUBLIC_BASE_URL` together before exposing the
service; keep the bind address on `127.0.0.1` until a reverse proxy / WAF terminates TLS in front
of it.

## 4. Host networking

This is what [`compose.host.yaml`](../../compose.host.yaml) does. There is no port mapping — the
container binds the host port itself through `WEB_SCRCPY_BIND` / `WEB_SCRCPY_PORT`, and
`127.0.0.1` inside the container **is the host**:

```sh
read -rs INITIAL_ADMIN_PASSWORD && export INITIAL_ADMIN_PASSWORD

docker run -d --name scrcpygate --restart unless-stopped --net=host \
  -e WEB_SCRCPY_BIND=0.0.0.0 -e WEB_SCRCPY_PORT=5000 \
  -e INITIAL_ADMIN_PASSWORD \
  -e PUBLIC_BASE_URL=http://127.0.0.1:5000 \
  -e ALLOWED_HOSTS=127.0.0.1,localhost \
  -e SESSION_COOKIE_SECURE=false \
  -e ADB_SERVER_SOCKET=tcp:127.0.0.1:5037 \
  -v "$PWD/data:/app/data" \
  scrcpygate:local
```

Keep `WEB_SCRCPY_BIND` / `WEB_SCRCPY_PORT` consistent with `PUBLIC_BASE_URL`, because the app
really listens there.

**Reusing the host's adb server.** With `--net=host`, `ADB_SERVER_SOCKET=tcp:127.0.0.1:5037`
points the container's adb client at the server you already run on the host — the one that sees
your USB devices in `adb devices`. Leave the variable unset and the container starts (and owns)
its own adb server, which is what you want for network ADB (`adb connect`) only.

**USB inside the container.** To hand USB devices to the container instead, drop
`ADB_SERVER_SOCKET` and add `--device /dev/bus/usb --privileged` (or equivalent udev/cgroup
rules). Reusing the host adb server is usually simpler and avoids the extra privileges.

## 5. Lifecycle and upgrades

```sh
docker logs -f scrcpygate            # follow logs (JSON on stdout)
docker restart scrcpygate            # restart
docker stop scrcpygate && docker rm scrcpygate   # remove the container (data stays in ./data)
```

To upgrade, replace the container with a new one built from a newer checkout or pulled by digest —
`docker run` has no `up -d --build` equivalent, so repeat the `docker run` command above after
removing the old container. Because the state lives in `./data`, this is safe as long as you keep
the same volume mount.

## 6. Administration without Compose

The in-container CLI is the same one the Compose instructions use; only the command prefix
differs:

```sh
docker exec scrcpygate python -m app.cli reset-admin
docker exec scrcpygate python -m app.cli generate-alas-key
docker exec scrcpygate python -m app.cli alas-token-status
```

Password output requires an explicit opt-in per invocation, for example:

```sh
docker exec -e SCRCPYGATE_SHOW_GENERATED_PASSWORD=true scrcpygate python -m app.cli reset-admin
```

Health endpoint: `GET /healthz`. If you omitted `INITIAL_ADMIN_PASSWORD` when the admin account
was first created, the generated password was written to `data/initial_admin_password.txt`
(mode `0600`) and deleted on the next start; if that file is gone too, the command above is the way
back in.

## Mapping back to Compose

| This page | Compose equivalent |
| --- | --- |
| `docker run -p …` (section 3) | `docker compose up -d` with `compose.yaml` |
| `docker run --net=host …` (section 4) | `docker compose -f compose.host.yaml up -d` |
| manual rebuild and re-run (section 5) | `docker compose up -d --build`, `docker compose pull` |
| `docker exec …` (section 6) | `docker compose exec scrcpygate …` |

Environment variables, volumes and the security posture (`cap_drop`, `no-new-privileges`,
logging limits) are all defined in the compose files — if you need more than the flags shown
above, read them and translate the corresponding entries.
