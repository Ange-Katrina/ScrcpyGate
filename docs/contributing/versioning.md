# Versioning

The root [`VERSION`](../../VERSION) file is the single source for the product
version. It contains `MAJOR.MINOR.PATCH`, optionally followed by a prerelease
such as `-rc.1`, without a leading `v`. The initial release version is `1.0.0`.

| Change | Example |
| --- | --- |
| Compatible bug or security fix | `1.0.0` → `1.0.1` |
| Compatible feature | `1.0.0` → `1.1.0` |
| Breaking API, configuration or deployment change | `1.0.0` → `2.0.0` |
| Release candidate | `1.1.0-rc.1` → `1.1.0` |

Increment `VERSION` for every delivered update, including updates delivered on
`dev` and source deployment bundles. Choose the increment from the table above.
One delivery may contain several commits; intermediate edits, CI retries, and
repackaging the same source do not require another increment. Never decrease
or reuse a version for a later delivery.

Before committing or pushing a delivery, update `VERSION`, include English
change notes and any upgrade instructions, and refresh `ScrcpyGate-deploy`
from the verified commit. A version in the source tree is not evidence that
its image or GitHub Release has been published.

## Runtime and builds

- Source runs and local Docker/Compose builds use `VERSION`.
- The API's OpenAPI version, administrator update panel, administrator sidebar
  and login footer share the same runtime version.
- `main`, `dev` and PR builds append a development identifier, for example
  `1.0.0-dev.main.g0123456789ab`. This distinguishes snapshots from a release.
- A tag build requires the exact tag `v` plus `VERSION`. Its image version is
  the source version; CI also records it in `org.opencontainers.image.version`.
- Branch image aliases remain `edge` / `dev`. Stable tags publish `latest`;
  prerelease tags never move `latest`.
- `SCRCPYGATE_VERSION` remains a compatibility fallback for legacy runtime
  layouts without a valid version file. An old `.env` value cannot override a
  current image's version or the version of a new Compose source build.
- Direct `docker build --build-arg SCRCPYGATE_VERSION=...` can set an explicit
  build version. The build validates it and writes it to the image's version
  file. Use the unprefixed form, such as `1.0.0-rc.1`; never use a branch name,
  `latest` or a digest. Build metadata containing `+` is intentionally excluded
  so versions can also be used in Docker tags.

Inspect a checkout with `python -m app.version`, or a running container with
`docker exec scrcpygate python -m app.version`. These commands do not contact
GitHub or modify the deployment.

## Publish

Follow [CI/CD](../deployment/ci-cd.md#publish-a-release) and the
[release writing guide](pull-requests-and-releases.md#release-notes). Push the
matching annotated tag only when ready to publish the verified source. Wait
for both architecture checks and GHCR publication, then create the GitHub
Release using its existing tag and verified image digest.

Adding or editing `VERSION` does not create a Git tag, GitHub Release or image
publication by itself.
