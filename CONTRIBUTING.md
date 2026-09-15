# Contributing to ScrcpyGate

For installation and a local development server, follow the
[README](README.md#development-uvicorn). For security reports, use
[SECURITY.md](SECURITY.md) instead of a public issue.

## Development environment

- Python 3.12 and the dependencies in `requirements-dev.txt`.
- Node.js 24 for JavaScript syntax checks; the shipped frontend needs no bundler.
- A POSIX shell for deployment scripts. On Windows, use Git Bash or WSL for
  the shell commands below.
- Docker Engine and Docker Compose for image and deployment validation.
  Host networking checks require a Linux Docker host. The host-network Compose
  variant requires Compose 2.24 or newer.

The application reads process environment variables, not `.env` automatically.
Keep development data in a local ignored directory and use synthetic accounts
and device examples when preparing a reproduction.

## Making a change

1. Create a focused branch from the current `main`, for example
   `git switch -c fix/describe-the-change`.
2. Keep changes limited to one problem and update affected documentation.
   Keep the English and Chinese README/deployment guides consistent.
3. Run the relevant checks below and explain the result in a pull request.
4. Use English Conventional Commits, such as `fix: handle disconnected devices`
   or `docs: clarify host-network deployment`. Explain the behavior and reason
   in the commit body when the title is insufficient.

Keep private configurations, runtime databases, device addresses, browser
profiles, internal audit material, and generated verification output out of
commits. Do not submit private test fixtures. The public repository keeps its
verification helpers in `tools/`; no internal `tests/` directory is published.
Use `.editorconfig` for new edits and avoid unrelated formatting changes.

## Checks before a pull request

Run from the repository root after installing `requirements-dev.txt`:

```sh
python tools/ci.py check-files
python -m ruff check --no-cache app adb_manager.py scrcpy.py tools
git diff --check

git ls-files -z 'static/*.js' 'static/**/*.js' |
  while IFS= read -r -d '' file; do node --check "$file" || exit; done
for file in deploy.sh docker-entrypoint.sh tools/*.sh; do
  sh -n "$file" || exit
done

docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example -f compose.host.yaml config --quiet
pip-audit -r requirements.txt --progress-spinner off
```

The JavaScript loop uses Bash. `check-files` inspects tracked files: stage new
source files explicitly before running it so they are included. For workflow
changes, also run `actionlint -shellcheck= -pyflakes=`. CI verifies scanner
downloads and scans reachable history with redacted Gitleaks output.

For changes that affect the image, on a Linux Docker host run:

```sh
docker build -t scrcpygate-ci:local .
sh tools/ci_smoke.sh scrcpygate-ci:local
```

The smoke helper uses the container name `scrcpygate-ci-smoke` and loopback
ports 15077/15078. Use an isolated development Docker host with those resources
available. It checks both network modes, health/login endpoints, non-root
execution, ADB, and bundled license notices.

CI repeats these checks on native amd64 and arm64 runners. It does not replace
functional regression checks for authentication, device permissions, mirroring,
or real ALAS services. Describe the focused synthetic or device validation you
performed and any gaps; do not attach sensitive logs as evidence.

## Review and publication

Changes to `main` go through pull requests. Required checks are
`Source and workflow checks`, `Verify image (amd64)`, and `Verify image (arm64)`.
Keep the branch current with `main` and resolve review conversations before
merging. A second maintainer's approval is not required for this solo-maintained
project. Direct pushes, force pushes, and branch deletion are blocked by the
main ruleset.

PRs do not publish images. After merge, `main` publishes `edge` and a full commit
SHA tag. Maintainers publish stable images by creating a `vMAJOR.MINOR.PATCH` tag;
only stable version tags update `latest`. Do not add a version tag merely to run
CI. See the [release guide](docs/deployment/ci-cd.md) for digest deployment,
prereleases, and publication recovery.
