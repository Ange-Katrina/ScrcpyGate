import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


FAKE_DOCKER = r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  info) exit 0 ;;
  compose)
    shift
    case "$1" in
      version) printf '%s\n' 'Docker Compose version fake' ;;
      config|build|up|ps) exit 0 ;;
      run) printf '%s\n' 'TestInitialPassword-123456' ;;
      *) exit 2 ;;
    esac
    ;;
  run)
    case "$*" in
      *"--entrypoint id"*"-u") printf '%s\n' '100' ;;
      *"--entrypoint id"*"-g") printf '%s\n' '101' ;;
      *"--entrypoint sh"*) exit 0 ;;
      *"--entrypoint chown"*) exit 0 ;;
      *) exit 2 ;;
    esac
    ;;
  image) exit 0 ;;
  inspect)
    case "$*" in
      *Health*) printf '%s\n' 'healthy' ;;
      *) printf '%s\n' 'running' ;;
    esac
    ;;
  logs) exit 0 ;;
  *) exit 2 ;;
esac
'''


class DeployScriptTests(unittest.TestCase):
    def shell(self) -> str:
        if os.name == "nt":
            for candidate in (
                Path(r"C:\Program Files\Git\bin\bash.exe"),
                Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
            ):
                if candidate.is_file():
                    return str(candidate)
        shell = shutil.which("sh")
        if shell:
            return shell
        self.skipTest("POSIX shell is not available")

    def prepare_installer(self) -> Path:
        target = Path(tempfile.mkdtemp(prefix="scrcpygate-deploy-test-"))
        self.addCleanup(shutil.rmtree, target, True)
        for name in ("deploy.sh", ".env.example", "compose.yaml"):
            shutil.copy2(ROOT / name, target / name)
        fake_bin = target / "fake-bin"
        fake_bin.mkdir()
        fake_docker = fake_bin / "docker"
        fake_docker.write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
        fake_docker.chmod(0o755)
        for name in ("curl", "wget"):
            command = fake_bin / name
            command.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
            command.chmod(0o755)
        return target

    def run_installer(self, target: Path, arguments: str = "", **environment: str):
        command = (
            'export PATH="$PWD/fake-bin:$PATH"; '
            'export FAKE_DOCKER_LOG="$PWD/docker.log"; '
            f"sh ./deploy.sh {arguments}"
        )
        env = os.environ.copy()
        env.update(environment)
        return subprocess.run(
            [self.shell(), "-c", command],
            cwd=target,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_help_does_not_require_docker(self):
        result = subprocess.run(
            [self.shell(), "-c", "sh ./deploy.sh --help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ScrcpyGate installer", result.stdout)
        self.assertIn("--skip-build", result.stdout)
        self.assertIn("--pull", result.stdout)

    def test_fresh_install_runs_the_complete_compose_flow(self):
        target = self.prepare_installer()
        result = self.run_installer(target)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((target / ".env").is_file())
        self.assertIn("Created .env from .env.example", result.stdout)
        self.assertIn("Public URL:     http://127.0.0.1:5000", result.stdout)
        self.assertIn("TestInitialPassword-123456", result.stdout)
        self.assertIn("ScrcpyGate installation completed", result.stdout)

        calls = (target / "docker.log").read_text(encoding="utf-8")
        for expected in (
            "info",
            "compose version",
            "compose config",
            "compose build",
            "compose run --rm --no-deps scrcpygate python -m app.cli bootstrap-admin",
            "compose up -d",
        ):
            self.assertIn(expected, calls)

    def test_existing_configuration_is_preserved_when_reusing_image(self):
        target = self.prepare_installer()
        config = "\n".join(
            (
                "WEB_SCRCPY_BIND=127.0.0.1",
                "WEB_SCRCPY_PORT=5052",
                "WEB_SCRCPY_DATA_HOST=./persistent-data",
                "PUBLIC_BASE_URL=http://127.0.0.1:5052",
                "SCRCPYGATE_HEALTH_TIMEOUT=10",
                "",
            )
        )
        (target / ".env").write_text(config, encoding="utf-8", newline="\n")

        result = self.run_installer(target, "--skip-build")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((target / ".env").read_text(encoding="utf-8"), config)
        self.assertIn("Public URL:     http://127.0.0.1:5052", result.stdout)
        self.assertIn("Reusing image scrcpygate:local", result.stdout)

        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertIn("image inspect scrcpygate:local", calls)
        self.assertNotIn("compose build", calls)

    def test_invalid_port_stops_before_calling_docker(self):
        target = self.prepare_installer()
        result = self.run_installer(target, WEB_SCRCPY_PORT="invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("WEB_SCRCPY_PORT must be an integer", result.stderr)
        self.assertFalse((target / "docker.log").exists())


if __name__ == "__main__":
    unittest.main()
