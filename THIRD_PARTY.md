# Third-Party Components

ScrcpyGate bundles a few third-party runtime assets so Docker deployments can run without downloading them at container startup.

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
