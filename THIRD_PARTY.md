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

## Android platform-tools

- Directory: `adb/linux/`
- Version: Android SDK Platform-Tools `36.0.0` (see `adb/linux/source.properties`).
- Purpose: Linux ADB binaries and support files used inside the Docker image.
- Notices: `adb/linux/NOTICE.txt`
- Bundled `adb` SHA-256: `372d800c04c3272729afade8a85d95a70fb1c7e74062d9ab17a92eb7b618096c`

The Docker image also installs Alpine's `android-tools`; the bundled files remain for compatibility with existing code paths.

## jMuxer

- File: `static/js/jmuxer.min.js`
- Version: `v2.0.7`
- Upstream: https://github.com/webstream-labs/jmuxer/tree/v2.0.7
- Purpose: H264 remuxing and MSE playback in the browser.
- License: MIT (`static/js/JMUXER_LICENSE`)
- SHA-256: `70381d825b1a2462885fb797bb952692e20993bc76d8f5cf5e13d7d4b0a2d6ae`

Update the version, hash, and bundled license when replacing this file.

## Lucide Icons

- Files: `static/icons/lucide.svg`, `static/icons/LUCIDE_LICENSE`
- Upstream: https://github.com/lucide-icons/lucide
- Purpose: Self-hosted interface icons used by the ScrcpyGate shell.
- License: ISC

Only the symbols used by the product shell are vendored. Keep the bundled ISC
license and this attribution when updating or redistributing the sprite.
