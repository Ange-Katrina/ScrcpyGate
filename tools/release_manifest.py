"""Build a redacted, reproducible manifest for a ScrcpyGate release input."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT_FILES = (
    ".dockerignore",
    ".env.example",
    "Dockerfile",
    "LICENSE",
    "THIRD_PARTY.md",
    "adb_manager.py",
    "compose.host.yaml",
    "compose.yaml",
    "deploy.sh",
    "docker-entrypoint.sh",
    "requirements.txt",
    "scrcpy-server",
    "scrcpy.py",
)
ROOT_DIRECTORIES = ("app", "static", "adb/linux")
SKIPPED_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
FORBIDDEN_NAMES = {
    ".env",
    "backups",
    "browser-data",
    "data",
    "initial_admin_password.txt",
    "node_modules",
    "output",
    "test-results",
}


class ManifestError(RuntimeError):
    """Raised when a checkout cannot be used as a release candidate."""


def _run_git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ManifestError("git metadata could not be read") from exc
    return result.stdout.strip()


def _git_metadata(root: Path) -> dict[str, object]:
    top_level = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != root:
        raise ManifestError("manifest root must be the repository root")
    status = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ManifestError("candidate checkout must have a clean git worktree")
    branch = _run_git(root, "branch", "--show-current") or "detached"
    head = _run_git(root, "rev-parse", "HEAD")
    if len(head) != 40 or any(char not in "0123456789abcdefABCDEF" for char in head):
        raise ManifestError("candidate checkout does not have a valid commit")
    return {"branch": branch, "head": head, "worktree_clean": True}


def _relative_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix()
    parts = set(Path(relative).parts)
    if parts & FORBIDDEN_NAMES or relative.endswith((".db", ".db-shm", ".db-wal", ".log")):
        raise ManifestError(f"forbidden release input: {relative}")
    if any(part.startswith(".env") and part != ".env.example" for part in Path(relative).parts):
        raise ManifestError(f"forbidden release input: {relative}")
    return relative


def _release_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for relative in ROOT_FILES:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ManifestError(f"required release input is missing or not a regular file: {relative}")
        _relative_path(root, path)
        if relative == ".env.example":
            _validate_example(path)
        paths.append(path)

    for directory in ROOT_DIRECTORIES:
        base = root / directory
        if not base.is_dir() or base.is_symlink():
            raise ManifestError(f"required release directory is missing: {directory}")
        for path in sorted(base.rglob("*")):
            relative_parts = path.relative_to(root).parts
            if any(part in SKIPPED_NAMES for part in relative_parts):
                continue
            if path.is_symlink():
                raise ManifestError(f"release input cannot be a symbolic link: {path.relative_to(root)}")
            if path.is_file():
                _relative_path(root, path)
                paths.append(path)
    return sorted(set(paths), key=lambda path: path.relative_to(root).as_posix())


def _validate_example(path: Path) -> None:
    protected = {
        "ALAS_TOKEN_ENCRYPTION_KEY",
        "ALAS_TOKEN_ENCRYPTION_KEY_PREVIOUS",
        "ALAS_GYRE_TOKEN",
        "INITIAL_ADMIN_PASSWORD",
    }
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ManifestError(".env.example could not be read") from exc
    for line in lines:
        probe = line.strip()
        if not probe or probe.startswith("#") or "=" not in probe:
            continue
        name, value = probe.split("=", 1)
        if name.strip() in protected and value.strip().strip("\"'"):
            raise ManifestError(".env.example contains a populated protected value")


def _sha256(path: Path) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise ManifestError(f"release input changed while hashing: {path.name}")
    return digest.hexdigest(), size


def _image_metadata(image: str) -> dict[str, object]:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{json .}}"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ManifestError("docker image digest could not be read") from exc
    try:
        metadata = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise ManifestError("docker image inspect returned invalid metadata") from exc
    digest = str(metadata.get("Id") or "")
    if not digest.startswith("sha256:"):
        raise ManifestError("docker image has no sha256 image digest")
    repo_digests = [str(value) for value in metadata.get("RepoDigests") or [] if str(value)]
    return {
        "reference": image,
        "digest": digest,
        "repo_digests": repo_digests,
        "created": metadata.get("Created"),
        "architecture": metadata.get("Architecture"),
        "os": metadata.get("Os"),
    }


def build_manifest(root: Path, image: str) -> dict[str, object]:
    root = root.resolve()
    if not root.is_dir():
        raise ManifestError("manifest root is not a directory")
    git = _git_metadata(root)
    files = []
    for path in _release_files(root):
        digest, size = _sha256(path)
        files.append({"path": path.relative_to(root).as_posix(), "size": size, "sha256": digest})
    return {
        "schema_version": 1,
        "artifact": "scrcpygate-release-input",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git": git,
        "image": _image_metadata(image),
        "files": files,
        "excluded": [
            ".env and .env.*",
            "data/ and backups/",
            "logs, databases, runtime state and browser state",
            "repository tools/ (maintenance inputs), docs/, output/ and local tooling caches",
            "Git metadata and untracked files",
        ],
    }


def _write_json(output: Path, payload: dict[str, object]) -> None:
    output = output.expanduser().resolve()
    if output.exists() and output.is_symlink():
        raise ManifestError("manifest output cannot be a symbolic link")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    except (OSError, UnicodeError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ManifestError("manifest could not be written atomically") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="scrcpygate:local")
    args = parser.parse_args(argv)
    try:
        _write_json(args.output, build_manifest(args.root, args.image))
    except ManifestError as exc:
        print(f"release manifest: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
