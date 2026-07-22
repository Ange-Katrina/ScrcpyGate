import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SUFFIX = "v" + str(2)
LEGACY_COMPOSE = f"docker-compose.{LEGACY_SUFFIX}.yml"
LEGACY_DEPLOY = f"deploy-{LEGACY_SUFFIX}.sh"
LEGACY_SERVICE = f"web-scrcpy-{LEGACY_SUFFIX}"
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}
TEXT_FILENAMES = {".dockerignore", ".gitignore", "Dockerfile", "LICENSE"}


class RepositoryLayoutTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_docker_entrypoints_use_standard_names(self):
        compose_path = ROOT / "compose.yaml"
        deploy_path = ROOT / "deploy.sh"
        self.assertTrue(compose_path.is_file())
        self.assertTrue(deploy_path.is_file())
        self.assertFalse((ROOT / LEGACY_COMPOSE).exists())
        self.assertFalse((ROOT / LEGACY_DEPLOY).exists())

        compose = self.read("compose.yaml")
        self.assertIn("name: scrcpygate", compose)
        self.assertIn("  scrcpygate:", compose)
        self.assertIn("image: scrcpygate:local", compose)
        self.assertIn("container_name: scrcpygate", compose)
        self.assertIn('driver: "${SCRCPYGATE_LOG_DRIVER:-local}"', compose)
        self.assertIn('max-size: "${SCRCPYGATE_LOG_MAX_SIZE:-20m}"', compose)
        self.assertIn('max-file: "${SCRCPYGATE_LOG_MAX_FILES:-5}"', compose)

        deploy = self.read("deploy.sh")
        self.assertNotIn("docker compose -f", deploy)
        self.assertNotIn("docker-compose -f", deploy)
        self.assertNotIn(LEGACY_SERVICE, deploy)
        self.assertIn("compose up -d", deploy)

    def test_documentation_uses_auto_discovered_compose(self):
        for readme in ("README.md", "README.en.md"):
            text = self.read(readme)
            self.assertNotIn(LEGACY_COMPOSE, text)
            self.assertNotIn(LEGACY_DEPLOY, text)
            self.assertNotIn(LEGACY_SERVICE, text)
            self.assertIn("compose.yaml", text)
            self.assertIn("docker compose up -d", text)

    def test_generated_paths_are_ignored_by_git_and_docker(self):
        def patterns(relative_path: str) -> set[str]:
            lines = (line.strip() for line in self.read(relative_path).splitlines())
            return {
                line.lstrip("/")
                for line in lines
                if line and not line.startswith(("#", "!"))
            }

        gitignore = patterns(".gitignore")
        dockerignore = patterns(".dockerignore")
        expected = (
            "data/",
            ".agents/",
            ".codex-mobile-data/",
            ".uv-cache/",
            ".uv-cache-local/",
            ".uv-python/",
            ".venv-codex-mobile/",
            ".playwright-cli/",
            ".ruff_cache/",
            "__pycache__/",
            "node_modules/",
            "test-results/",
            "playwright-report/",
            "blob-report/",
            "output/",
            "ALAS_EMBED_STATUS.md",
        )
        for pattern in expected:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, gitignore)
                self.assertIn(pattern, dockerignore)

    def test_tracked_text_has_no_bom_or_generated_paths(self):
        tracked = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, encoding="utf-8"
        ).splitlines()
        generated_prefixes = (
            "data/",
            ".agents/",
            ".codex-mobile-data/",
            ".playwright-cli/",
            ".ruff_cache/",
            ".uv-cache/",
            ".uv-cache-local/",
            ".uv-python/",
            ".venv-codex-mobile/",
            "__pycache__/",
            "node_modules/",
            "output/",
            "playwright-report/",
            "blob-report/",
            "test-results/",
        )
        for relative in tracked:
            normalized = relative.replace("\\", "/")
            self.assertFalse(
                normalized.startswith(generated_prefixes),
                f"generated path is tracked: {relative}",
            )
            path = ROOT / relative
            if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_FILENAMES:
                self.assertFalse(
                    path.read_bytes().startswith(b"\xef\xbb\xbf"),
                    f"UTF-8 BOM remains in {relative}",
                )

    def test_required_runtime_assets_remain(self):
        for relative in (
            "adb/linux/adb",
            "scrcpy-server",
            "static/js/jmuxer.min.js",
            "static/icons/lucide.svg",
            "static/icons/LUCIDE_LICENSE",
            "static/js/JMUXER_LICENSE",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file())


if __name__ == "__main__":
    unittest.main()
