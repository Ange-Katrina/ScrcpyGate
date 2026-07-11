# Third-Party Components

ScrcpyGate bundles a few third-party runtime assets so Docker deployments can run without downloading them at container startup.

## Original web-scrcpy Project

- Upstream: https://github.com/baixin1228/web-scrcpy
- Purpose: Original Web-Scrcpy project and implementation reference for browser-based scrcpy access.

ScrcpyGate is a FastAPI/WebSocket refactor and productized gateway built with its own authentication, authorization, video-profile, ALAS, and deployment layers. Keep upstream attribution visible when publishing derivative releases.

## scrcpy-server

- File: `scrcpy-server`
- Upstream: https://github.com/Genymobile/scrcpy
- Purpose: Android-side scrcpy server used for screen capture and control.

Verify the bundled version before publishing a release and keep its license obligations with the release artifacts.

## Android platform-tools

- Directory: `adb/linux/`
- Purpose: Linux ADB binaries and support files used inside the Docker image.
- Notices: `adb/linux/NOTICE.txt`

The Docker image also installs Alpine's `android-tools`; the bundled files remain for compatibility with existing code paths.

## jMuxer

- File: `static/js/jmuxer.min.js`
- Upstream: https://github.com/webstream-labs/jmuxer
- Purpose: H264 remuxing and MSE playback in the browser.

Keep upstream license notices when replacing this file.

## Lucide Icons

- Files: `static/icons/lucide.svg`, `static/icons/LUCIDE_LICENSE`
- Upstream: https://github.com/lucide-icons/lucide
- Purpose: Self-hosted interface icons used by the ScrcpyGate shell.
- License: ISC

Only the symbols used by the product shell are vendored. Keep the bundled ISC
license and this attribution when updating or redistributing the sprite.
