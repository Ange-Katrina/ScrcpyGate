# Third-Party Components

ScrcpyGate bundles a few third-party runtime assets so Docker deployments can run without downloading them at container startup.

## Original web-scrcpy Project

- Upstream: https://github.com/baixin1228/web-scrcpy
- Purpose: Original Web-Scrcpy project and implementation reference for browser-based scrcpy access.

ScrcpyGate is a FastAPI/WebSocket refactor and productized gateway built with its own authentication, authorization, video-profile, ALAS, and deployment layers. Keep upstream attribution visible when publishing derivative releases.

## scrcpy-server

- File: `scrcpy-server`
- Version: `v3.1`
- Upstream: https://github.com/Genymobile/scrcpy/releases/tag/v3.1
- Purpose: Android-side scrcpy server used for screen capture and control.
- License: Apache License 2.0; the upstream copyright notice is retained in `LICENSE`.
- SHA-256: `958f0944a62f23b1f33a16e9eb14844c1a04b882ca175a738c16d23cb22b86c0`

The protocol version in `scrcpy.py` must match this bundled server version.

Upstream releases are watched by `.github/workflows/Upstream-Canary.yml`: on a weekly
schedule it compares this documented version with upstream's latest release, and when
upstream is ahead it builds an untested canary image (`scrcpy-<version>-canary`, `canary`)
from the official `scrcpy-server-v<version>` asset, verifies that asset against upstream's
`SHA256SUMS.txt`, smoke-runs the image, and opens an issue asking for human verification.
Promotion is always manual: validate mirroring and control with the canary image, replace
the bundled `scrcpy-server`, update the Version and SHA-256 above, then tag `v*` so
`Builds.yml` publishes the official image. `latest` never moves from the canary workflow.

## Android platform-tools

- Directory: `adb/linux/`
- Version: Android SDK Platform-Tools `36.0.0` (see `adb/linux/source.properties`).
- Purpose: Linux ADB binaries and support files used inside the Docker image.
- Notices: `adb/linux/NOTICE.txt`
- Bundled `adb` SHA-256: `372d800c04c3272729afade8a85d95a70fb1c7e74062d9ab17a92eb7b618096c`

The Docker image also installs Alpine's `android-tools` and runs the `adb` found on
`PATH` (`/usr/bin/adb`). The bundled `adb` above is a glibc build, so it does not
execute inside that musl-based image; it stays for non-Alpine hosts that point
`ADB_PATH` at it.

## jMuxer

- File: `static/vendor/jmuxer.min.js`
- Version: `v2.0.7`
- Upstream: https://github.com/webstream-labs/jmuxer/tree/v2.0.7
- Purpose: H264 remuxing and MSE playback in the browser.
- License: MIT (`static/vendor/JMUXER_LICENSE`)
- SHA-256: `70381d825b1a2462885fb797bb952692e20993bc76d8f5cf5e13d7d4b0a2d6ae`

Update the version, hash, and bundled license when replacing this file.

## Lucide Icons

- Files: `static/vendor/lucide.min.js`, `static/icons/lucide.svg`, `static/icons/LUCIDE_LICENSE`
- Upstream: https://github.com/lucide-icons/lucide
- Purpose: Self-hosted interface icons used by the ScrcpyGate shell.
- License: ISC

Only the symbols used by the product shell are vendored. Keep the bundled ISC
license and this attribution when updating or redistributing the sprite.

## maxminddb

- Package: `maxminddb==3.2.0` (`requirements.txt`)
- Upstream: https://github.com/maxmind/MaxMind-DB-Reader-python
- Purpose: Read local MaxMind DB (MMDB) files for the optional region restriction
  feature. The reader never performs network lookups; it opens a local file.
- License: Apache License 2.0

## HTTPX

- Package: `httpx==0.28.1` (`requirements.txt`)
- Upstream: https://github.com/encode/httpx
- Purpose: GeoIP downloads through explicitly configured HTTP/HTTPS forward proxies,
  with TLS certificate verification and bounded official-host redirects.
- License: BSD 3-Clause; the installed distribution includes its license notice.

## GeoLite2 City and Country data (not bundled)

- Region restrictions and access attribution can download **GeoLite2-City**
  (default), **GeoLite2-Country**, or both from MaxMind at runtime, as selected
  in the admin console. Country codes determine access policy; available region and city
  names are used for display only. **No MMDB file is shipped with this
  repository or the container image**, and no license key is bundled: the key is
  supplied with `GEO_ACCOUNT_ID` through the `GEO_LICENSE_KEY` environment variable,
  or configured through the admin console in a private data-volume file. It is
  never written to SQLite, logs, responses, or image layers. See the README for
  file permissions and backup handling.
- Using GeoLite2 (including the download itself) requires accepting MaxMind's
  GeoLite2 End User License Agreement and having a MaxMind account with a
  license key: https://www.maxmind.com/en/geolite2/eula
- Attribution requirement: applications using GeoLite2 data must be accompanied
  by the notice *"This product includes GeoLite2 data created by MaxMind,
  available from https://www.maxmind.com"*. The admin console shows this
  attribution in the region-restriction card, and it must stay in place when the
  feature is used.
- Version retention: MaxMind's EULA requires ceasing use and destroying old versions
  within 30 days after an updated version is released. ScrcpyGate additionally applies
  a conservative 30-day build-age cutoff and cleans its managed active/backup files.
  Operators maintain externally managed files and archived backups; the application
  does not delete files from an external read-only directory.
- Operators who may not redistribute or host the data can mount an external
  read-only directory (`./geoip:/app/data/geoip:ro`) and set
  `GEO_UPDATE_ENABLED=false`; the in-container updater then stays off and only
  reads the file the operator provides.
