"""Read-only source checks and image metadata for GitHub Actions."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*))?")
LOCAL_DIRECTORIES = {
    ".agents", ".claude", ".codex", ".git", ".venv", "__pycache__",
    "backups", "browser-data", "data", "node_modules", "output",
    "playwright-report", "skill", "snapshots", "test-results", "test-tools", "tests",
}


def image_metadata(event: str, ref: str, sha: str, repository: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a full commit SHA")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository name")
    image = f"ghcr.io/{repository.split('/')[0].lower()}/scrcpygate"
    publish = event in {"push", "workflow_dispatch"}
    tags = [f"{image}:sha-{sha}"]
    if ref in {"refs/heads/main", "refs/heads/dev"}:
        tags.append(f"{image}:{'edge' if ref.endswith('/main') else 'dev'}")
    elif ref.startswith("refs/tags/"):
        version = ref.removeprefix("refs/tags/")
        match = VERSION.fullmatch(version)
        if not match or len(version) > 128:
            raise ValueError("Release tags must use vMAJOR.MINOR.PATCH[-prerelease]")
        prerelease = match.group(4)
        if prerelease and any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease.split(".")):
            raise ValueError("Numeric prerelease identifiers must not have leading zeroes")
        tags.append(f"{image}:{version}")
        if prerelease is None:
            tags.append(f"{image}:latest")
    else:
        publish = False
    return {"image": image, "publish": str(publish).lower(), "tags": tags if publish else []}


def check_files() -> None:
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    failures = []
    checked = 0
    for name in filter(None, names):
        path = Path(name)
        forbidden = (
            bool(set(path.parts) & LOCAL_DIRECTORIES)
            or name.startswith("docs/organized/")
            or path.name in {"AGENTS.md", "CLAUDE.md", "initial_admin_password.txt"}
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".log", ".har", ".pyc", ".pyo", ".pem", ".p12", ".pfx"}
        )
        if forbidden:
            failures.append(name)
            continue
        if path.suffix == ".py":
            ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=name)
        elif path.suffix == ".json":
            json.loads((ROOT / path).read_text(encoding="utf-8"))
        elif path.suffix == ".svg":
            ET.parse(ROOT / path)
        checked += 1
    if failures:
        raise ValueError("Workspace-only files are tracked: " + ", ".join(failures))
    print(f"Checked {checked} tracked files; Python/JSON/SVG syntax and repository boundaries passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check-files", "metadata"))
    args = parser.parse_args()
    if args.command == "check-files":
        check_files()
        return
    metadata = image_metadata(
        os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"],
        os.environ["GITHUB_SHA"], os.environ["GITHUB_REPOSITORY"],
    )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"image={metadata['image']}\npublish={metadata['publish']}\n")
            handle.write("tags<<SCRCPYGATE_IMAGE_TAGS\n")
            handle.write("\n".join(metadata["tags"]) + "\nSCRCPYGATE_IMAGE_TAGS\n")
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
