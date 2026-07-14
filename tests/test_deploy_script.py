import os
import re
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
      version)
        if [ "${FAKE_DOCKER_COMPOSE_MISSING:-0}" = 1 ] && [ ! -f "$PWD/compose-installed" ]; then
          exit 1
        fi
        printf '%s\n' 'Docker Compose version fake'
        ;;
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
        fake_id = fake_bin / "id"
        fake_id.write_text(
            "#!/bin/sh\n"
            "case \"${1:-}\" in\n"
            "  -u) printf '%s\\n' 0 ;;\n"
            "  -un) printf '%s\\n' tester ;;\n"
            "  *) printf '%s\\n' tester ;;\n"
            "esac\n",
            encoding="utf-8",
            newline="\n",
        )
        fake_id.chmod(0o755)
        fake_apt = fake_bin / "apt-get"
        fake_apt.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$PWD/package-manager.log\"\n"
            "case \"$*\" in *docker-compose-v2*) : > \"$PWD/compose-installed\" ;; esac\n",
            encoding="utf-8",
            newline="\n",
        )
        fake_apt.chmod(0o755)
        fake_service = fake_bin / "service"
        fake_service.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$PWD/service.log\"\n",
            encoding="utf-8",
            newline="\n",
        )
        fake_service.chmod(0o755)
        fake_ss = fake_bin / "ss"
        fake_ss.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' 'State Recv-Q Send-Q Local Address:Port Peer Address:Port'\n"
            "if [ -n \"${FAKE_SS_OCCUPIED_PORT:-}\" ]; then\n"
            "  printf '%s\\n' \"LISTEN 0 128 0.0.0.0:$FAKE_SS_OCCUPIED_PORT 0.0.0.0:*\"\n"
            "fi\n",
            encoding="utf-8",
            newline="\n",
        )
        fake_ss.chmod(0o755)
        fake_od = fake_bin / "od"
        fake_od.write_text(
            "#!/bin/sh\n"
            "counter_file=\"$PWD/random-counter\"\n"
            "counter=0\n"
            "[ ! -f \"$counter_file\" ] || counter=$(sed -n '1p' \"$counter_file\")\n"
            "printf '%s\\n' \"$counter\"\n"
            "next=$((counter + 1))\n"
            "printf '%s\\n' \"$next\" > \"$counter_file\"\n",
            encoding="utf-8",
            newline="\n",
        )
        fake_od.chmod(0o755)
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
            "SCRCPYGATE_AUTO_INSTALL_DEPS",
            "SCRCPYGATE_DETECTED_IP",
            "SCRCPYGATE_PACKAGE_MANAGER",
            "FAKE_DOCKER_COMPOSE_MISSING",
            "FAKE_SS_OCCUPIED_PORT",
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
        self.assertIn("--install-deps", result.stdout)

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
        self.assertIn("检查/安装 Docker 与 Compose", result.stdout)

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

    def test_configuration_wizard_uses_detected_ip_and_random_port(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--configure",
            input_text="2\n\n./wizard-data\n\n\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.77",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("检测到系统 IPv4", result.stdout)
        self.assertIn("已自动选择空闲端口", result.stdout)
        config = (target / ".env").read_text(encoding="utf-8")
        port_match = re.search(r"^WEB_SCRCPY_PORT=(\d+)$", config, re.MULTILINE)
        self.assertIsNotNone(port_match)
        port = int(port_match.group(1))
        self.assertGreaterEqual(port, 20000)
        self.assertLessEqual(port, 59999)
        self.assertIn(f"PUBLIC_BASE_URL=http://192.0.2.77:{port}", config)
        self.assertIn("ALLOWED_HOSTS=127.0.0.1,localhost,192.0.2.77", config)

    def test_random_port_skips_a_listening_candidate(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--configure",
            input_text="1\n\n\n\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.77",
            FAKE_SS_OCCUPIED_PORT="20000",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = (target / ".env").read_text(encoding="utf-8")
        self.assertIn("WEB_SCRCPY_PORT=20001", config)
        self.assertIn("PUBLIC_BASE_URL=http://127.0.0.1:20001", config)

    def test_configuration_wizard_builds_reverse_proxy_settings(self):
        target = self.prepare_installer()
        answers = (
            "3\n51234\n./proxy-data\n192.0.2.10\nexample.com\nhttps\n"
            "22263\n192.0.2.20\nhttp://192.0.2.10:51234\n\n"
        )
        result = self.run_installer(
            target,
            "--configure",
            input_text=answers,
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.10",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = (target / ".env").read_text(encoding="utf-8")
        expected = (
            "WEB_SCRCPY_BIND=192.0.2.10",
            "WEB_SCRCPY_PORT=51234",
            "PUBLIC_BASE_URL=https://example.com:22263",
            "ALLOWED_HOSTS=127.0.0.1,localhost,example.com,192.0.2.10",
            "ALLOWED_ORIGINS=https://example.com:22263,http://192.0.2.10:51234",
            "TRUST_PROXY=true",
            "TRUSTED_PROXY_IPS=192.0.2.20",
            "SESSION_COOKIE_SECURE=true",
        )
        for item in expected:
            self.assertIn(item, config)

    def test_reverse_proxy_default_https_port_is_not_rendered(self):
        target = self.prepare_installer()
        answers = "3\n51235\n\n\nexample.com\n\n\n192.0.2.20\n\n\n"
        result = self.run_installer(
            target,
            "--configure",
            input_text=answers,
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.10",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = (target / ".env").read_text(encoding="utf-8")
        self.assertIn("PUBLIC_BASE_URL=https://example.com\n", config)
        self.assertNotIn("example.com:443", config)
        self.assertIn("SESSION_COOKIE_SECURE=true", config)

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

    def test_install_deps_uses_distribution_package_manager_then_installs(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--install-deps",
            SCRCPYGATE_PACKAGE_MANAGER="apt-get",
            FAKE_DOCKER_COMPOSE_MISSING="1",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        package_calls = (target / "package-manager.log").read_text(encoding="utf-8")
        self.assertIn("update", package_calls)
        self.assertIn("install -y ca-certificates curl", package_calls)
        self.assertNotIn("docker.io", package_calls)
        self.assertIn("install -y docker-compose-v2", package_calls)
        self.assertTrue((target / "compose-installed").is_file())
        self.assertIn("系统依赖安装", result.stdout)

    def test_dependency_installer_has_supported_distribution_contract(self):
        source = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        for package_manager in ("apt-get", "apk", "dnf", "yum", "pacman", "zypper"):
            self.assertIn(package_manager, source)
        self.assertIn("SCRCPYGATE_AUTO_INSTALL_DEPS", source)
        self.assertIn("pacman -Syu --needed --noconfirm", source)
        self.assertNotIn("pacman -Sy --needed", source)
        self.assertNotIn("curl | sh", source)


if __name__ == "__main__":
    unittest.main()
