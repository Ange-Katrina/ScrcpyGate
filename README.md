<div align="center">

<img src="docs/assets/logo.svg" width="88" height="88" alt="ScrcpyGate logo">

# ScrcpyGate

**Self-hosted Android screen mirroring and remote control in the browser.**

scrcpy server · native WebSocket · Raw v2 framing · FastAPI · no frontend build step

[![Builds](https://github.com/Ange-Katrina/ScrcpyGate/actions/workflows/Builds.yml/badge.svg?branch=main)](https://github.com/Ange-Katrina/ScrcpyGate/actions/workflows/Builds.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-3776AB.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.139-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/docker-amd64%20%7C%20arm64-2496ED.svg)](https://github.com/Ange-Katrina/ScrcpyGate/pkgs/container/scrcpygate)
[![Third party](https://img.shields.io/badge/third--party-THIRD__PARTY.md-lightgrey.svg)](THIRD_PARTY.md)

[English](README.md) · [简体中文](README.zh.md)

[Contributing](CONTRIBUTING.md) · [Report a vulnerability](SECURITY.md)

</div>

---

ScrcpyGate runs the official **scrcpy server** over ADB, forwards the raw H.264 stream to the
browser over a **native WebSocket**, and adds what a shared deployment actually needs: accounts
and roles, device authorization, explicit control leases, tamper-evident audit logging, quality
profiles, and an optional **ALAS** integration.

> It is not a thin page that exposes ADB to the network. It is meant to sit behind a reverse
> proxy / WAF, and it treats *who may watch, who may control, and what gets logged* as
> first-class features.

## Install

```sh
git clone https://github.com/Ange-Katrina/ScrcpyGate.git
cd ScrcpyGate
./deploy.sh --install
```

Then open `http://127.0.0.1:5000` and sign in as `admin`.

**Recommended path.** The installer is the supported way in — it configures, builds, starts and
health-checks the stack for you (details in [Quick start](#quick-start)). Everything below is for
when you want to see or drive the pieces yourself.

| Path | For | Start with |
| --- | --- | --- |
| **[deploy.sh](#recommended-deploysh)** | first installation, and hosts you want guided | `./deploy.sh --install` |
| [Docker](#docker-compose) | you manage the Compose stack yourself | `docker compose up -d --build` |
| [Host network / USB](#special-host-network-and-usb) | host adb server, USB devices, special networking | `docker compose -f compose.host.yaml up -d --build` |
| [Development](#development-uvicorn) | running from source | `uvicorn app.main:app` |

Whatever you choose runs the same image and the same `app.main:app`.

## Contents

- [Highlights](#highlights)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
  - [Recommended: deploy.sh](#recommended-deploysh)
  - [Docker: Compose](#docker-compose)
  - [Special: host network and USB](#special-host-network-and-usb)
  - [Development: uvicorn](#development-uvicorn)
- [Operations](#operations)
- [Advanced and recovery](#advanced-and-recovery)
- [Configuration](#configuration)
- [Security model](#security-model)

## Highlights

| | |
| --- | --- |
| **Mirror and control** | Network ADB devices, several viewers per device, one controller at a time — control is an explicit lease with expiry, and admin takeovers are audited. |
| **Raw v2 streaming** | scrcpy server → per-viewer bounded queue → WebSocket → browser, with keyframe-aware recovery, slow-viewer isolation, adaptive frame sizing and optional low-latency encoder hints. |
| **Rotation follow** | Device rotation keeps the capture transport and viewer connection open. The workbench and grid retain the last frame during decoder changes, with a brief rotation transition that respects reduced-motion preferences. Fullscreen can request OS orientation changes. |
| **Accounts and roles** | Per-user device grants, view/control separation, session and expiry handling, per-role workbench configuration. |
| **Shaped workbench** | Admins choose, per role, which dock buttons exist, which status-bar blocks show, the dock layout (level 1 / "More") and fullscreen behaviour switches. |
| **Audit and notifications** | Hash-chained audit events with alerts and retention controls, plus in-app notifications and viewer history. |
| **ALAS integration** | Optional embedded ALAS UI behind a policy boundary, an outbound gateway with host/CIDR allow-lists, and encrypted tokens whose key lives in the data directory — never in `.env`. |
| **Mobile-first UI** | Multi-page static frontend without a bundler: grid view, fullscreen mirror, edge handle, touch mapping, zh/en i18n. |
| **Docker first** | Multi-arch image (amd64 + arm64), non-root, healthcheck, and sample compose files with `cap_drop: ALL` and `no-new-privileges`. |
| **Release pipeline** | The image is smoke-run and scanned before it ships, published to GHCR with provenance/SBOM, and can be deployed by digest with automatic rollback. A weekly watcher also builds an untested **canary** image when upstream scrcpy releases a new server, so version bumps are validated before `latest` moves. |

## Grid status and device resources

The administrator grid lists all discoverable ALAS configurations, including configurations without device bindings, in a compact status rail. Mini pills show the configuration name and current task; green indicates running, neutral indicates idle or stopped, red indicates a failed check, and amber indicates pending or stale status. Full status labels are available on hover and to assistive technology; check times are not displayed. It queries the administrator overview every 30 seconds while the grid is visible, bypasses browser read caching, and pauses when the tab is hidden or the grid closes. Failed checks remain distinct from stopped configurations.

Use the activity button on a device card to expand CPU usage, memory and CPU temperature rows. Expanded cards refresh every 15 seconds through a shared serial sampling queue; click again to collapse. Sampling pauses in hidden tabs and stops when the grid closes, the device disappears, or access is lost. CPU is measured over a one-second interval across all cores; if Android denies `/proc/stat`, the sampler tries `dumpsys cpuinfo` and labels its latest system statistics separately. Memory is `MemTotal - MemAvailable`, rather than an individual app's usage. Results, including unavailable metrics, are cached for ten seconds, with at most two concurrent samples per application process and a four-second CPU/memory ADB timeout. The optional temperature query has its own two-second timeout; its failure preserves the CPU/memory result. Unsupported metrics remain unknown; ADB connection failures and timeouts are distinguished. CPU temperature uses current CPU-type sensors from Android `dumpsys thermalservice`, with readable, explicitly CPU-named `/sys/class/thermal/thermal_zone*/` nodes as a fallback. Multiple CPU sensors use the highest reading. Cached temperatures, thermal thresholds, battery, GPU and skin readings are not substituted. Missing or inaccessible CPU sensors show unavailable; no battery is required. Values use green/amber/red at CPU 60/85%, memory 70/90%, and CPU temperature 70/85°C; these are visual reference thresholds, not device-specific thermal limits. Unknown values remain neutral. Device sampling does not start mirroring or acquire control. No new deployment settings or dependencies are required.

The grid, user cards, and security forms share compact control geometry and touch-friendly targets. GeoIP download settings group the source and database choices beside connection settings on desktop, with bounded field widths and a single column on phones. Saved proxy addresses remain visible. The native database file picker aligns with the other upload controls.

## City lookup coverage

GeoLite2-City may return a country without a province or city. Missing fields remain unknown; an IP location is an estimate, not a device's GPS position. [ip2region](https://github.com/lionsoul2014/ip2region) is a possible supplementary offline source with separate IPv4 and IPv6 XDB databases and a Python client. Its bundled data is updated irregularly. It is not integrated in this release, and XDB files cannot be uploaded through the MMDB importer. Any future integration must identify the source, validate each database, and preserve the existing country access policy when sources disagree.

## How it works

```mermaid
flowchart LR
  B["Browser<br/>mirror · grid · admin"]
  A["FastAPI app<br/>app.main:app"]
  D["Android device<br/>scrcpy server v3.1"]
  B -- "HTTP" --> A
  A -- "WebSocket · Raw v2" --> B
  A -- "ADB" --> D
  D -- "H.264 · device meta · control" --> A
```

| Path | What lives there |
| --- | --- |
| `app/routers/` | HTTP and WebSocket API — 93 routes: auth, devices, mirror, ALAS, admin, audit |
| `app/mirror_manager.py`, `app/mirror_runtime.py`, `app/mirror_websocket.py` | session lifecycle, stream fan-out, per-viewer queues, control leases |
| `app/scrcpy_demuxer.py`, `app/h264.py` | Raw v2 framing, NAL / keyframe handling |
| `app/alas_*` | ALAS embed transport, policy, gateway, secrets, visibility |
| `app/security.py`, `app/login_guard.py`, `app/audit_*` | sessions, CSRF / origin rules, brute-force guard, audit chain |
| `static/` | framework-free frontend (pages + shared modules + CSS), no build step |
| `compose.yaml`, `compose.host.yaml` | the two supported deployment shapes |
| `docs/` | advanced deployment recipes (`docker run`) and the README logo |
| `tools/` | deployment helpers (excluded from the image) |

## Quick start

**Requirements** — a Linux host with Docker 24+ and Compose v2 (the installer can add them for
you with `--install-deps`), plus an Android device with USB debugging that is reachable over ADB.

### Recommended: deploy.sh

```sh
git clone https://github.com/Ange-Katrina/ScrcpyGate.git
cd ScrcpyGate
./deploy.sh --install
```

One command, and it does the whole first install in order:

1. creates `.env` from `.env.example` (or drives an interactive wizard with `--configure`),
2. validates the configuration, then checks for port and container conflicts,
3. creates the data directory,
4. builds the image, then reuses the ALAS token encryption key or provisions one for new data,
5. fixes the data-directory ownership for the container user (`uid 100` / `gid 101`),
6. creates the admin account and prints its password,
7. starts the stack and waits for the `/healthz` gate.

It ends by printing the URL to open and the admin password. Re-running the same command later is
how you update an existing installation; existing `.env` values, databases and passwords are never
overwritten.

Running `./deploy.sh` with no arguments opens an interactive menu with the same operations —
install/update, start/stop/restart, status, logs, configuration, admin reset, backups, ALAS token
status and migration, environment checks and uninstall.

For a reinstall that keeps accounts and settings, run `./deploy.sh --uninstall` and then
`./deploy.sh --install`. Ordinary uninstall always keeps `.env`, the local image, the database,
and its ALAS key together. Menu option 13 offers **Keep data** (the default) or **Full cleanup**.
Existing passwords are retained when keeping data;
use `./deploy.sh --reset-admin` if the original password was not saved.

Use `./deploy.sh --uninstall --purge` only to remove the local data and start fresh. If cleanup
fails, `.env` is retained so the same command can be retried. Data outside the project directory
is never deleted automatically; its configuration is retained and cleanup reports incomplete.
Backups are retained. Keep the database and its matching key together when moving a deployment.
Interactive full cleanup requires typing the complete data path before anything is removed;
an empty answer cancels, and `--yes` does not skip this confirmation. Noninteractive
`--uninstall --purge` is an explicit destructive command and runs without a prompt.
If an older uninstall already removed the container and `.env`, full cleanup can still remove
the default `./data` containing a `webscrcpy.db` with a SQLite header. Unrecognized directories
and symlinks are retained; restore the original `.env` and verify its data path before retrying.
Recognized legacy cleanup saves a minimal `.env` with the data path before deletion, so an
interrupted purge remains retryable even if the database was already removed.
Full cleanup removes accounts, ALAS credentials and persisted ADB authorization; reinstall
requires fresh setup. If only the ALAS key is missing, prefer the token-only reset below.
An old database with an encrypted ALAS token and a missing key blocks automatic key generation: restore the
matching key first. Interactive installation offers to clear only the ALAS token and continue with
a new key; the default answer is No. Accounts and device settings are retained. Noninteractive
installation never accepts this reset automatically, including with `--yes`.
If that key cannot be recovered, you can also explicitly run `./deploy.sh --clear-alas-token`,
then reinstall and enter a new ALAS token. This clears only the stored ALAS credential, not accounts.
An invalid existing key file must be repaired separately; reinstall never replaces it silently.
Do not use `--skip-build` when installing source fixes.

### Docker: Compose

If you would rather drive the stack yourself, the installer runs exactly this — the pieces are
just yours to manage:

```sh
git clone https://github.com/Ange-Katrina/ScrcpyGate.git
cd ScrcpyGate
cp .env.example .env

# Set the first-run admin password (>= MIN_PASSWORD_LENGTH, 12 characters) in .env:
#   INITIAL_ADMIN_PASSWORD=<strong-password-at-least-12-chars>

# The container runs as uid 100(app):gid 101(app); the host data directory must be writable
# by it, otherwise the app fails at startup with a PermissionError on /app/data/.init-db.lock.
mkdir -p ./data && sudo chown -R 100:101 ./data

docker compose up -d --build
```

Two things the installer did for you and Compose will not: the data-directory ownership above, and
the admin account. If you skip the password, the app generates one and writes it to
`./data/initial_admin_password.txt` (mode `0600`, removed again on the next start) instead of
leaving you locked out — see [Advanced and recovery](#advanced-and-recovery).

The container entrypoint provisions an ALAS key for new or unencrypted data. It preserves
existing keys and refuses to replace a missing key when the database contains encrypted tokens.
Restore the matching key in that case; the core application can start with an ALAS warning.
`SCRCPYGATE_ADMIN_PASSWORD_FILE=false` in `.env` disables the first-run password file.

Container-managed ADB now stores its identity in `data/.android`. Before replacing an older
container that stored it under `/tmp`, follow the [ADB migration steps](docs/deployment/docker-run.md#5-lifecycle-and-upgrades).

### Special: host network and USB

Use `compose.host.yaml` when the container must share the host's network stack — typically to
reuse the host's own adb server (`adb devices` on the host already sees your USB phones) or to
reach a service that only listens on the host's loopback:

```sh
docker compose -f compose.host.yaml up -d --build
```

It inherits the entire service definition from `compose.yaml`, so there is only one place to
maintain. There is no port mapping in this mode: `WEB_SCRCPY_BIND` / `WEB_SCRCPY_PORT` decide
where it binds on the host, so keep them consistent with `PUBLIC_BASE_URL`.

Set `ADB_SERVER_SOCKET=tcp:127.0.0.1:5037` in `.env` to let the container use the host's adb
server. Handing USB devices to the container directly instead needs
`--device /dev/bus/usb --privileged`.

### Development: uvicorn

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export WEB_SCRCPY_DATA_DIR=./data PUBLIC_BASE_URL=http://127.0.0.1:5000 \
       ALLOWED_HOSTS=127.0.0.1,localhost SESSION_COOKIE_SECURE=false
uvicorn app.main:app --host 127.0.0.1 --port 5000
```

> [!IMPORTANT]
> The application never reads `.env` — there is no dotenv loader. It reads the **process
> environment** only. `.env` exists for `docker compose` (`${VAR}` interpolation) and for
> `deploy.sh` (which looks values up by name). Running `uvicorn` directly means exporting the
> variables yourself.

## Workbench controls

Acquire control of the device before sending keys, text, or touch input. **More → Text input → Physical keyboard input mode** offers two modes:

- **Local input method (default):** select Chinese characters with your computer's input method. Chinese text and emoji use the device clipboard and paste, so the focused Android field must support paste. A single text submission supports up to 4096 UTF-8 bytes; oversized text is rejected rather than truncated.
- **Device input method:** send physical keyboard keys to Android, as in QtScrcpy's normal keyboard path. Use an English input layout on the computer and a compatible input method on the phone to select Chinese characters there. This mode requires a desktop physical keyboard.

Right-click the picture to go back or wake the device; middle-click to go home. **More** also contains volume, power-key, and explicit screen-off/screen-on commands. **Turn device screen off** requests mirroring with the physical display off; device support varies. A sent-command message confirms transmission, not the device's response.

The rotate button changes the local picture. **More → Restore automatic orientation** returns to automatic fitting without changing Android's rotation setting. Scroll and touch coordinates follow the displayed orientation; changing orientation or leaving the browser releases active gestures when the control channel is available.

The protocol follows the bundled scrcpy **3.1** server. See the upstream [keyboard guide](https://github.com/Genymobile/scrcpy/blob/v3.1/doc/keyboard.md), [shortcuts](https://github.com/Genymobile/scrcpy/blob/v3.1/doc/shortcuts.md), and [QtScrcpy FAQ](https://github.com/barry-ran/QtScrcpy/blob/dev/docs/FAQ.md). ScrcpyGate does not expose scrcpy's UHID/AOA keyboard modes.

The **Mirror management** page covers text input, automatic control on fullscreen, orientation reset, volume, power, and device screen on/off as well as the existing toolbar controls. Each role can place these actions in the toolbar, move them into **More**, or disable them. Existing layouts retain their ordering and disabled actions when upgraded. Disabling automatic control also stops its automatic acquisition behavior without changing the browser preference. These settings configure the workbench UI; device permissions and control ownership are still enforced separately.

### Updates and ALAS scheduling

The dashboard’s **System update** panel shows the running version, the selected release channel, the last check time, and a copyable command for bridge deployments managed by `deploy.sh`. Automatic channel selection follows `dev`/`edge` image tags or matching build versions; local builds default to stable. Floating tags require pulling and comparing images on the host. Checking never installs an update or grants the application Docker access. A failed check clears the old command, and a newer local version does not offer a downgrade.

ALAS updates separately. The [official configuration template](https://github.com/LmeSzinc/AzurLaneAutoScript/blob/master/deploy/template) supports these settings in **ALAS’s** `config/deploy.yaml` (merge into the existing sections):

```yaml
Deploy:
  Git:
    AutoUpdate: true
  Update:
    EnableReload: true
    CheckUpdateInterval: 5
    AutoRestartTime: '03:50'
```

`AutoUpdate` enables startup updates; `CheckUpdateInterval` only schedules checks (minutes; `0` disables them). `AutoRestartTime` schedules daily installation when an update exists (`null` disables it), using the ALAS environment’s time. `EnableReload` and a reload-capable launcher such as the official `gui.py` are required for scheduled restarts. ALAS stops and resumes running instances during updates; its updater may force-stop tasks after a ten-minute wait. Writable Git source files, reachable upstreams, and dependency installation permissions are required. A read-only or image-only ALAS deployment needs its own image update process. ScrcpyGate does not currently read or change these settings, so the dashboard does not claim they are enabled.

The ALAS web update button invokes the official updater through a PyWebIO callback. The current Alas-Gyre `/api/gyre` API does not expose ALAS application updates. Its separate `/runtime/update` service only updates Gyre overlay, launcher, and updater files. Use the ALAS management link to open its native page.

### Quality and activity records

Preset cards show familiar resolution tiers such as 720p and 1080p. These are size limits, not a guarantee of encoded dimensions; the device aspect ratio is preserved.

Quality presets apply immediately. Manual resolution, FPS and bit-rate edits are staged until **Apply quality** is clicked, so typing does not repeatedly restart the stream. Encoding changes may briefly restart the shared device stream; with other viewers connected, the server may save the preference and defer the restart.

Resolution is scrcpy's **long-edge limit**, preserving the device aspect ratio; reference width/height values are not a fixed output size. FPS is a maximum, and static content naturally produces fewer frames. Start with 30 fps for general use or try 60 fps for games; lower the resolution or target bit rate when bandwidth or decoding is constrained. Each viewer consumes upload bandwidth. Existing administrator presets are preserved. These semantics follow [scrcpy 3.1](https://github.com/Genymobile/scrcpy/blob/v3.1/doc/video.md); [QtScrcpy](https://github.com/barry-ran/QtScrcpy) also distinguishes frame limits and dropping expired frames for latency.

Administrator ALAS status distinguishes stopped (`idle`) from unchecked and reports the server's check time. Manual refresh bypasses the short status cache.

The **Users and permissions** page uses searchable, filterable user cards. Select a card for login IP, last login, total mirroring time, session count, latest start/end, active connections, and recent device sessions. Edit and permission controls remain on the card; password reset and deletion are in the details dialog.

User times use the browser's local time zone. Last login means successful authentication, not a page visit. Watch duration sums retained video connections, including simultaneous viewers separately; refreshes and reconnects create new sessions. New `viewer_watch_start` / `viewer_watch_end` audit events include the watch-session ID and matching timestamps. Historical events are not rewritten. Audit records can be filtered by actor and device before pagination, with the same filters applied to export. Runtime-log filtering covers a bounded recent tail; use audit records or the complete log export for historical investigations.

## Operations

CI/CD publishes verified amd64 and arm64 images to GHCR; servers are deployed
manually. See [CI/CD and manual deployment](docs/deployment/ci-cd.md) for
release tags, package permissions, image attestations, and deployment commands.

The product version is defined in [`VERSION`](VERSION). See the
[versioning guide](docs/contributing/versioning.md) for release numbering,
development builds, and version display.

Health endpoint `GET /healthz`; logs are JSON on stdout (`LOG_FORMAT=json`) with optional file
logging in the data directory.

When a mirror looks wrong ("black for a moment", "frozen", "it stopped by itself"), admins get a
browser-side event timeline in the workbench sidebar (**投屏记录 / Mirror record**): connection
state, keyframe waits, sequence gaps, decoder rebuilds, latency seeks, server resets, socket close
codes, bitrate and resolution changes, and control-lease changes, each with a timestamp. It lives
in browser memory and can be copied or exported as JSON. Choose **Local only** to keep the
timeline in that tab, or **Multi-viewer** to invite other clients watching the same device.
Each invited viewer can accept or decline. When the administrator stops and collects records,
accepted clients upload their timelines and the server relays them to the initiating browser
in memory. Exports keep each client's segment separate instead of merging different clocks.
Timeline payloads are not persisted by the relay; operation metadata is audited separately.
Server-side events stay in `/logs`.

**Export all retained logs** on the logs page downloads a ZIP containing all retained audit events (`audit.jsonl`), alerts including resolved alerts (`alerts.jsonl`), audit-chain state (`integrity.jsonl`), and `webscrcpy.log` plus numeric rotation files. It ignores page filters, pagination and the existing view export's 31-day/10,000-row limits. Every stored audit column is retained; `metadata_json` is the original stored JSON string. The manifest lists counts, sizes, SHA-256 hashes and snapshot boundaries. Audit and alerts share a database read snapshot; runtime files are captured afterward at their opening lengths. Retention-deleted records and console-only/Docker stdout logs cannot be recovered; installations without log files may export zero runtime files.

Full export requires administrator access and CSRF validation. It excludes databases, environment files and credential files. The default uncompressed limit is 512 MiB (`LOG_EXPORT_MAX_BYTES`), with one export per process and a 120-second generation deadline. Limit, read and rotation failures are explicit; no silently truncated archive is returned. Temporary archives are removed after download or disconnect. Archives retain existing audit identities and source IPs; review their contents before sharing.

The page receives the archive as base64 JSON and saves the ZIP locally in the browser, avoiding a separate attachment request by download managers. Base64 adds about one third to the compressed transfer size; the browser buffers the response and decoded archive, so large exports require sufficient browser memory.

Unknown event names remain stored and exported. The UI uses generic titles and a detail fallback when labels are missing; severity/outcome still control failure styling. Inspect raw `action`, `reason` and `metadata` fields for unmapped events. ALAS WebSocket failures now include exception type, connection phase and available handshake/close codes; normal browser departure and clean upstream closure are not upstream errors. Existing historical records are not rewritten.

Each browser keeps the latest **800 events**, dropping older entries when full. Accepted uploads
preserve the submitted fields, including unknown keys; this does not recover events already
evicted by the browser. Sessions default to 15 minutes with a 20-second upload window after
stopping, up to 12 invited participants, 20,000 entries and a 2 MiB timeline limit.
The global `API_REQUEST_BODY_MAX_BYTES` limit also applies to the entire JSON request and
defaults to 1 MiB. Increase it with enough room for the request envelope when accepting larger
timelines. Configure `MIRROR_RECORD_TTL_SECONDS`, `MIRROR_RECORD_UPLOAD_GRACE_SECONDS`,
`MIRROR_RECORD_UPLOAD_MAX_BYTES`, `MIRROR_RECORD_MAX_ENTRIES`, and `MIRROR_RECORD_MAX_PARTICIPANTS`
in the Compose `.env` (both network modes inherit them), or export them for a direct Python run.

With the installer (recommended — it wraps the same Compose project):

| Command | What it does |
| --- | --- |
| `./deploy.sh --install` | install or update, with the health gate |
| `./deploy.sh --configure` / `--menu` | interactive `.env` wizard / management menu |
| `./deploy.sh --install-deps` / `--pull` / `--skip-build` | install Docker + Compose, refresh base images, reuse the existing local image |
| `./deploy.sh --start` / `--stop` / `--restart` / `--status` | lifecycle and status |
| `./deploy.sh --logs [lines]` | log tail |
| `./deploy.sh --check` / `--check-conflicts` / `--check-production` | read-only self-check, port/container conflicts, production boundary validation |
| `./deploy.sh --backup [path] [--keep N]` / `--list-backups` | back up the data directory (online SQLite snapshot + ALAS key + manifest + checksum, optionally keeping only the newest `N` archives) / list archives |
| `./deploy.sh --restore <archive> [--yes] [--no-restart] [--data-only]` | restore a backup: archives the current data first, restarts behind the health gate, rolls back on failure |
| `./deploy.sh --candidate-manifest [file]` | file hashes and image digest of a clean checkout |
| `./deploy.sh --uninstall [--purge]` | remove the stack (and the data directory with `--purge`) |

With Compose, if you installed that way and prefer to stay there:

```sh
docker compose ps
docker compose logs -f
docker compose up -d                            # apply configuration changes
docker compose pull && docker compose up -d     # move to a newer image
docker compose down
```

Gated releases — immutable digest, health gate, automatic rollback:

```sh
sh tools/deploy_release.sh \
  --image ghcr.io/<owner>/scrcpygate@sha256:<digest> \
  --compose-dir /path/to/deployment \
  --health-url http://127.0.0.1:5000/healthz
# exit 0 deployed · 2 rolled back to the previous image · 3 rollback also unhealthy · 1 bad input
```

## Advanced and recovery

**Forgot the admin password.** If no admin password was set when the account was created, the
generated one was written to `data/initial_admin_password.txt` (mode `0600`) and deleted on the
next start. If it is gone too, reset the password:

```sh
docker exec -e SCRCPYGATE_SHOW_GENERATED_PASSWORD=true scrcpygate python -m app.cli reset-admin
# or, with the installer in play:
./deploy.sh --reset-admin
```

**In-container lifecycle commands** (`app/cli.py`), all reachable through `docker exec` /
`docker compose exec`:

| Command | What it does |
| --- | --- |
| `bootstrap-admin` | create the admin account; needs `SCRCPYGATE_SHOW_GENERATED_PASSWORD=true` to print a generated password |
| `reset-admin` | generate a new admin password (same display flag) |
| `initial-password` | print the bootstrap password, if this call created the account and the environment value still matches |
| `generate-alas-key` | provision the server-side ALAS token key (never printed) |
| `alas-token-status` / `migrate-alas-token` | legacy ALAS token status (read-only) / import and rotate |

**ALAS token migration** from the installer instead:

```sh
./deploy.sh --token-status
./deploy.sh --migrate-alas-token
```

**`docker run` without Compose** — bridge and host recipes, password handling, USB passthrough and
manual upgrades live in [docs/deployment/docker-run.md](docs/deployment/docker-run.md). They mirror
the two compose files; if you take that path, upgrades and rollbacks are yours to handle.

## Routine upgrades without source uploads

After the selected commit's GitHub Actions publication succeeds, run this in the
existing deployment directory to follow the `dev` test branch:

```sh
sudo sh ./deploy.sh --update --image ghcr.io/ange-katrina/scrcpygate:dev
```

Use `edge` for main or `latest` for stable tagged releases. This also switches a
running, script-managed bridge deployment from a local build to a published image.
The script pulls, backs up data and configuration, recreates the container, and
checks health with image rollback on failure. It remembers the successful target,
so subsequent upgrades only need `sudo sh ./deploy.sh --update`. Older scripts can
keep using the explicit `--image` command until `deploy.sh` is updated once.
Application image upgrades do not replace host deployment scripts or Compose files;
follow release notes when those files change. See the [deployment guide](docs/deployment/ci-cd.md#update-an-existing-bridge-deployment)
for host-network instructions and backup/rollback limits.

For GHCR access from China, enter **s → 2** in the image-update selector to use
the [Nanjing University mirror](https://doc.nju.edu.cn/books/e1654/page/ghcr).
The selector also supports the official registry and custom GHCR mirror hosts
with optional ports and path prefixes. Discovery and pulls use the selected
source; a successful update remembers the full reference without changing Docker
daemon settings. If discovery fails, common channels remain selectable and are
marked unverified. There is no automatic fallback to a different source.

```sh
sudo sh ./deploy.sh --update --image ghcr.nju.edu.cn/ange-katrina/scrcpygate:dev
```

Third-party caches can lag behind GHCR. Use fixed tags or digests when needed;
switching registries cannot provide a `latest` tag that has not been published.

### Disk maintenance

Source rebuilds retain Docker build cache and can leave dangling images. Prefer
published-image upgrades for routine updates. Menu 25 or `./deploy.sh --disk-usage`
reports usage; `--prune-images` removes only unused dangling images labeled
`io.scrcpygate.managed=true`, while `--prune-build-cache` prunes the default
builder's unused cache with a 1 GiB retention target. For small disks, use
`--prune-build-cache-all` (submenu 4) to set the retention target to zero.
The script prefers `--reserved-space`, falls back to the legacy `--keep-storage`
flag when supported, and stops if neither is available. Cleanup affects other
projects using that builder and may reclaim zero bytes when nothing qualifies.

`--prune-rollback-images` (submenu 5) previews removal of old installer-generated
rollback tags. It retains the newest unused image version by image ID, all
versions referenced by running or stopped containers, the configured image
reference, and unrecognized tags. Duplicate tags count as one version. Tag targets
and container references are checked again before deletion; removal never uses
force. Shared layers or remaining tags may prevent space from being reclaimed.

All cleanup requires confirmation unless `--yes` is supplied. These operations
never remove containers, volumes, application data, keys or backups. Image and
cache totals can share layers and must not be added together. Old source folders,
uploaded archives, system logs, unlabeled images and custom buildx caches need separate
review. See [disk maintenance](docs/deployment/ci-cd.md#disk-maintenance) for legacy
commands and backup retention.

## Configuration

`.env.example` documents every knob (`docker compose` and `deploy.sh` read it; the application
itself never does).

| Group | Variables |
| --- | --- |
| Networking | `WEB_SCRCPY_BIND`, `WEB_SCRCPY_PORT`, `WEB_SCRCPY_DATA_HOST`, `PUBLIC_BASE_URL`, `ALLOWED_HOSTS`, `ALLOWED_ORIGINS`, `TRUST_PROXY`, `TRUSTED_PROXY_IPS` |
| Sessions and login guard | `SESSION_COOKIE_SECURE`, `MIN_PASSWORD_LENGTH`, `LOGIN_RATE_LIMIT_*`, `LOGIN_CAPTCHA_*` |
| ADB and devices | `ADB_AUTOCONNECT`, `ADB_PATH`, `ADB_SERVER_SOCKET`, `ADB_HEARTBEAT_INTERVAL`, `ADB_ROTATION_RESTART_FALLBACK`, `ADB_ROTATION_POLL_INTERVAL`, `ADB_CONNECT_TIMEOUT` |
| Streaming | `SCRCPY_STREAM_MODE`, `SCRCPY_SERVER_LOG_LEVEL`, `SCRCPY_I_FRAME_INTERVAL`, `VIDEO_QUEUE_*`, `SCRCPY_RAW_*` |
| Logging and audit | `LOG_*`, `AUDIT_*`, `VIEWER_WATCH_RETENTION_DAYS` |
| ALAS (optional) | `ALAS_EMBED_ORIGIN`, `ALAS_ALLOWED_HOSTS`, `ALAS_ALLOWED_CIDRS`, `ALAS_POLICY_*`, `ALAS_TOKEN_*` |
| Image and build | `SCRCPYGATE_IMAGE`, `PYTHON_IMAGE`, `PIP_INDEX_URL` |

- `SCRCPYGATE_IMAGE` selects the image to run: empty means the locally built `scrcpygate:local`;
  for releases pin an immutable reference such as `ghcr.io/<owner>/scrcpygate@sha256:<digest>`.
- Behind a slow network, set `PIP_INDEX_URL` to a nearby PyPI mirror before building.
- Normal rotation uses scrcpy's native orientation handling. Only for a device whose
  encoder fails to follow rotation, set `ADB_ROTATION_RESTART_FALLBACK=true` and recreate
  the container; this compatibility mode briefly restarts capture. Existing
  `ADB_ROTATION_POLL_INTERVAL` settings alone do not enable restarts.
- ALAS token encryption keys are injected by your secret manager, or provisioned once into the
  data directory; never put key material in `.env`.

## Security model

- Host/Origin allow-lists, optional proxy trust with explicit CIDRs, CSRF tokens on mutations,
  `SameSite`/secure session cookies, and no API docs in production by default.
- Login guard: failure counting, lockout windows, and a click-to-verify, built-in proof-of-work check after
  repeated failures.
- Device access is granted per user; watching and controlling are separate permissions, and
  control is an explicit lease that can be released or taken over (with an audit trail).
- ALAS: outbound requests are constrained by host/CIDR allow-lists and bounded response sizes;
  the embedded UI enforces a visibility policy and audits denied actions.
- Audit events are hash-chained, and the audit log has retention/alert limits.
- Passwords are stored as PBKDF2-HMAC-SHA256 hashes (`pbkdf2_sha256$…`, 310k iterations by
  default, per-account random salt, constant-time comparison), never reversibly. Session tokens are
  stored as SHA-256 hashes, so a copy of the database cannot be replayed as a live session; the
  ALAS token is AES-GCM encrypted with a key that lives outside the database. The database file
  itself is not encrypted — that is what disk/volume encryption is for.
- Active login sessions are listed on the security page (account, client, source IP, last activity)
  and any of them can be ended from there — ending one also closes the WebSockets it was using.
  `MAX_SESSIONS_PER_USER` caps concurrent sessions per account by dropping the oldest at login.
- The container runs as a non-root user; the sample compose files drop all capabilities and
  enable `no-new-privileges`.

Login verification uses ScrcpyGate's own SHA-256 PoW by default: click the verification button, wait for the spinner, then sign in after the checkmark appears. Four small bounded puzzles reduce wait-time variance. Signed challenges expire and can be used only once for the bound account/source. PoW raises automation cost; it is not proof of human identity or a replacement for rate limiting, TLS, or a WAF.

HTTPS (or localhost for testing) is required for WebCrypto. The default build bundles no ALTCHA/Cap code and makes no CAPTCHA service requests. `LOGIN_POW_PROVIDER=builtin` selects the default. Other providers require a separately installed, trusted adapter package exporting the `scrcpygate.pow` entry point (API v1), a dedicated static directory with `client.js`, and `issue`/`verify` methods; simply installing an upstream library is insufficient. Docker users install the adapter in a derived image. Select its entry-point name and restart; missing/invalid adapters stop startup rather than bypassing verification. Provider changes require fresh challenges; deployments with multiple workers/replicas remain unsupported by the process-local login limits.

### Security controls in the admin console

- **Access records:** open request details in place, or select **Ban** to choose a duration and reason in a dialog. Rescheduling preserves the existing reason. Bans disconnect existing connections; banning your own source also removes your access. Recover on the server with `./deploy.sh --unban <ip>`.
- **Login protection:** customize initial PoW difficulty, escalation, ceiling, challenge lifetime, issuance interval, and failure thresholds. The light/balanced/stronger PoW presets change difficulty only. Each additional bit approximately doubles expected work. Benchmark locally and test on a phone before raising the ceiling; the built-in solver has a 30-second compute deadline. Save to apply to newly issued challenges.
- **Regional restrictions → Database and automatic updates:** updates default to [P3TERX/GeoLite.mmdb GitHub releases](https://github.com/P3TERX/GeoLite.mmdb/releases/latest), without an Account ID, License Key, or GitHub token. Select Country / City and check for updates. To use MaxMind directly, select the official source and enter an **Account ID and License Key** from the same account. Saving credentials does not enable enforcement.

Saved MaxMind credentials live in `data/.geo-credentials.json` (or the configured data directory), as a private configuration file, **not encrypted**. Linux permissions are `0600`; Windows operators must restrict the data directory ACL. The API never returns the Key, and credentials are excluded from Git, Docker build inputs, and SQLite/settings exports. `deploy.sh` includes the file in private data backups; protect those backups as secrets. Keeping data on reinstall preserves the configuration; purging data removes it. Removing saved credentials leaves the existing country database and region policy intact.

Credentials apply only to the optional official MaxMind source. `GEO_ACCOUNT_ID` or `GEO_LICENSE_KEY` in the environment takes precedence for the **entire pair**, so both must be configured; environment and saved values are never mixed. These credentials are never sent to GitHub. `GEO_UPDATE_ENABLED=false` disables online updates from all sources; use this with an externally managed, read-only database.

On upgrade, existing download settings without a source default to GitHub, preserving edition selections, proxy settings and saved MaxMind credentials. You can switch back to MaxMind in the admin panel. GitHub is a third-party mirror; applicable data licenses still apply. The updater queries the latest stable release, pins its asset URL and requires the asset size and SHA-256 digest. It verifies the downloaded bytes, MMDB type and build date before activation. Missing integrity metadata, expired or corrupt databases, and downgrades are rejected. Matching release metadata, local size and SHA-256, and a fresh valid database are all required to skip a download. Proxy settings apply to both metadata and asset requests. Allow outbound access to `api.github.com`, `github.com` and `release-assets.githubusercontent.com`. API rate limits and network errors preserve the current database and never silently switch sources.

The admin panel distinguishes configured, unverified, verified, rejected, and temporarily unverifiable credentials, with check and verification timestamps. MaxMind download authentication has no web login session and exposes no account/key expiry date. Rotating credentials requires verification again. See the [official update guide](https://dev.maxmind.com/geoip/updating-databases/) and [license key guide](https://support.maxmind.com/knowledge-base/articles/using-maxmind-license-keys).

Automatic updates download **GeoLite2 City** by default. Under **Download options**, select Country, City, or both; City already includes country information. Saving applies to the next check. Selecting neither pauses downloads without immediately deleting existing databases; normal database expiry and cleanup still apply. When both are selected, Country is checked first and City last, making City active after a successful check. If a later download fails, earlier successful files remain installed and the task reports failure. The existing Country database remains usable until City downloads, validates, and activates successfully. Attribution displays available country, region and city names, using English when Chinese names are missing and falling back to the country when details are absent. Country-based enforcement is unchanged; historical details and exports are not rewritten.

The **Download activity** window below the progress bar shows each database, stage, transferred bytes, elapsed time, and safe error messages. It keeps the latest 120 entries for the current task in memory. Use **Follow latest entries** to follow downloads or turn it off to read earlier entries. Starting another task, changing download settings, or restarting the service clears this history.

Choose **Use server proxy settings** (default), **Direct connection**, or **Custom proxy**. Custom URLs require an HTTP/HTTPS scheme and port, for example `http://proxy.example:7890` or `https://user:password@proxy.example:8443`; percent-encode special characters in credentials. This is a forward proxy, not a download mirror. HTTPS certificates are verified, and a failed custom proxy never falls back to a direct connection. A proxy on the Docker host must be reachable from the container; `127.0.0.1` inside a bridge-network container refers to that container.

Selections and the proxy URL are stored in `.geo-downloads.json` in the data volume (0600 on Unix; restrict the directory ACL on Windows). The proxy field stays visible, including after saving or reloading. Only authenticated administrators can read the saved URL through the no-store status endpoint; it is excluded from runtime logs, audits, and diagnostic exports. Leave the field blank to retain the saved proxy; explicitly check **Clear the saved proxy URL** to remove it and select a non-custom connection mode. Deployment backups include this private file: protect backups as credentials. Saving options does not verify connectivity; use **Check for updates** to test the download path.

The database panel lists size, build date, update time, source, active status, and backup size for Country and City. If the server cannot reach the download service, select an edition under **Upload a database**, choose an `.mmdb` (up to 256 MiB) or official `.tar.gz` (up to 128 MiB) obtained from MaxMind, and select **Validate and activate**. This admin-only, CSRF-protected, audited action needs no download credentials. Configure any reverse proxy to allow the corresponding request size and up to ten minutes for upload. Invalid, mismatched, expired (over 30 days), or older-than-installed databases are rejected. Activation or state-persistence failures restore the previous database. Each edition keeps at most one `.mmdb.bak`; identical content creates no replacement or extra backup.

Online updates compare Last-Modified, archive size, and a strong ETag or Content-MD5, then revalidate the local edition, freshness, size, and SHA-256 before skipping a download. An ETag is a version identifier, not an MD5 checksum. Downloads verify Content-MD5 when supplied, declared size, and the MMDB itself. Missing local receipts or incomplete remote evidence require a confirmation download; identical content is not installed again. Temporary upload and extraction files are cleaned up afterward.

Lookups use the local MMDB only; visitor IP addresses are not sent to a third party. Coordinates and street addresses are not displayed. City databases need more download time and memory than Country. VPNs, proxies and mobile networks may affect accuracy; results do not identify a person's actual location. See the [MaxMind City / Country database documentation](https://dev.maxmind.com/geoip/docs/databases/city-and-country/).

**Delete database** removes the selected managed Country or City file and its backup after confirmation, including backup-only remnants. Administrators and CSRF verification are required; deletion shares the update/upload lock. Enforce mode prevents deletion of the active database. Switch to Observe or Off and save first when removal is intended. Download selections are unchanged, so a selected edition can be downloaded again by a later update. Failed state persistence restores the files; a recovery or cleanup failure is reported and `.recovery` files are retained for manual recovery. Deletion does not remove credentials or change the access policy.

Country presets, editable ISO codes, and help text have separate labeled controls; changes take effect only after saving.

Set an automatic check interval of 1–168 hours (12 recommended) in the panel. The persisted setting overrides the `GEO_UPDATE_INTERVAL_HOURS` default and reschedules checks without a restart; the environment disable switch still applies. Country presets only fill the form and require Save geo settings to apply. Access summaries resolve attribution with the current local database; country filters, historical details and exports retain recorded values. Private addresses cannot be geolocated.

Account ID and License Key share one credential status: unconfigured, incomplete, configured, or unsaved changes. Both values must be present for the configured state. After changing Country/City selections or the proxy, **Save options and check for updates** saves the current choices before checking. Credential edits must be saved separately. Download permission status is separate from task progress; errors distinguish DNS, TLS, proxy, timeout, and file-operation failures.

HTTP failures show the status code, request method (HEAD/GET), and endpoint category (MaxMind or file storage), without response bodies, signed URLs, or credentials. Each failure produces one failure entry. A HEAD response of 405/501 falls back to GET within the same deadline and attempt budget; authentication, rate-limit, and other errors do not. Existing databases do not block downloads: after the new database passes validation, the current file is copied to `.mmdb.bak` and atomically replaced. Failed downloads retain the current database, and the run log shows backup steps. Do not delete the active database to troubleshoot HTTP errors.

The built-in updater checks every 12 hours by default, with jitter, using official HTTPS downloads and bounded redirects. Successful checks have a ten-minute cooldown; failures or actual changes to credentials or download settings shorten it to 30 seconds, measured from the previous attempt's start. A countdown automatically re-enables the retry button. The local limit remains 30 attempts per UTC day, including failures; saving unchanged settings resets neither the cooldown nor the quota. Larger City downloads have a ten-minute transfer deadline, with a 30-second connection/read timeout. Unchanged remote versions avoid a full download; invalid downloads keep the last usable database. A database older than 30 days is considered unavailable by this application's freshness policy. In enforce mode an unavailable database denies access, so configure and test updates before enabling enforcement.

Start with **Observe**, preview your source, and configure trusted proxy CIDRs correctly if using a WAF or CDN. Never trust arbitrary forwarded headers. IP geolocation can be inaccurate for VPNs, proxies, and mobile networks; it supplements authentication and IP bans. Recovery: `./deploy.sh --geo-off`, or set `GEO_ENFORCE_DISABLED=true` and restart.

MaxMind documentation: [generate a license key](https://support.maxmind.com/hc/en-us/articles/4407111582235-Generate-a-License-Key) · [database downloads and update schedule](https://support.maxmind.com/hc/en-us/articles/4408216129947-Download-and-Update-Databases). The publication schedule is maintained by MaxMind; checking more often does not imply a new database each time.

---

<sub>Apache-2.0 — see [LICENSE](LICENSE) · bundled third-party components are listed in [THIRD_PARTY.md](THIRD_PARTY.md)</sub>
