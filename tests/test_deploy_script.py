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

    def run_installer(
        self,
        target: Path,
        arguments: str = "",
        input_text: str | None = None,
        **environment: str,
    ):
        command = (
            'export PATH="$PWD/fake-bin:$PATH"; '
            'export FAKE_DOCKER_LOG="$PWD/docker.log"; '
            f"sh ./deploy.sh {arguments}"
        )
        env = os.environ.copy()
        for key in (
            "WEB_SCRCPY_BIND",
            "WEB_SCRCPY_PORT",
            "WEB_SCRCPY_DATA_HOST",
            "PUBLIC_BASE_URL",
            "ALLOWED_HOSTS",
            "ALLOWED_ORIGINS",
            "TRUST_PROXY",
            "TRUSTED_PROXY_IPS",
            "SESSION_COOKIE_SECURE",
            "SCRCPYGATE_HEALTH_TIMEOUT",
            "INITIAL_ADMIN_PASSWORD",
        ):
            env.pop(key, None)
        env.update(environment)
        return subprocess.run(
            [self.shell(), "-c", command],
            cwd=target,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=input_text,
            timeout=30,
            check=False,
        )

    def test_help_does_not_require_docker(self):
        result = subprocess.run(
            [self.shell(), "-c", "sh ./deploy.sh --help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ScrcpyGate", result.stdout)
        self.assertIn("--menu", result.stdout)
        self.assertIn("--skip-build", result.stdout)
        self.assertIn("--pull", result.stdout)

    def test_menu_opens_in_forced_interactive_mode(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--menu",
            input_text="0\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ScrcpyGate 安装与管理面板", result.stdout)
        self.assertIn("引导配置并安装", result.stdout)
        self.assertIn("重置管理员密码", result.stdout)

    def test_configuration_wizard_writes_lan_settings(self):
        target = self.prepare_installer()
        answers = "2\n5053\n./wizard-data\n192.0.2.50\n\n\n"
        result = self.run_installer(
            target,
            "--configure",
            input_text=answers,
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("部署配置向导", result.stdout)
        self.assertIn("配置已保存", result.stdout)
        config = (target / ".env").read_text(encoding="utf-8")
        self.assertIn("WEB_SCRCPY_BIND=0.0.0.0", config)
        self.assertIn("WEB_SCRCPY_PORT=5053", config)
        self.assertIn("WEB_SCRCPY_DATA_HOST=./wizard-data", config)
        self.assertIn("PUBLIC_BASE_URL=http://192.0.2.50:5053", config)
        self.assertIn("ALLOWED_HOSTS=127.0.0.1,localhost,192.0.2.50", config)

    def test_fresh_install_runs_the_complete_compose_flow(self):
        target = self.prepare_installer()
        result = self.run_installer(target)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((target / ".env").is_file())
        self.assertIn(".env.example", result.stdout)
        self.assertIn("http://127.0.0.1:5000", result.stdout)
        self.assertIn("TestInitialPassword-123456", result.stdout)
        self.assertIn("docker compose ps", result.stdout)

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
        self.assertIn("http://127.0.0.1:5052", result.stdout)
        self.assertIn("scrcpygate:local", result.stdout)

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
