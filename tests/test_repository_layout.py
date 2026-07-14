import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryLayoutTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_docker_entrypoints_use_standard_names(self):
        compose_path = ROOT / "compose.yaml"
        deploy_path = ROOT / "deploy.sh"
        self.assertTrue(compose_path.is_file())
        self.assertTrue(deploy_path.is_file())
        self.assertFalse((ROOT / "docker-compose.v2.yml").exists())
        self.assertFalse((ROOT / "deploy-v2.sh").exists())

        compose = self.read("compose.yaml")
        self.assertIn("name: scrcpygate", compose)
        self.assertIn("  scrcpygate:", compose)
        self.assertIn("image: scrcpygate:local", compose)
        self.assertIn("container_name: scrcpygate", compose)

        deploy = self.read("deploy.sh")
        self.assertNotIn(" -f ", deploy)
        self.assertNotIn("web-scrcpy-v2", deploy)
        self.assertIn("$DC up -d", deploy)

    def test_documentation_uses_auto_discovered_compose(self):
        for readme in ("README.md", "README.en.md"):
            text = self.read(readme)
            self.assertNotIn("docker-compose.v2.yml", text)
            self.assertNotIn("deploy-v2.sh", text)
            self.assertNotIn("web-scrcpy-v2", text)
            self.assertIn("compose.yaml", text)
            self.assertIn("docker compose up -d", text)


if __name__ == "__main__":
    unittest.main()
