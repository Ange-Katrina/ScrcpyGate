import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


FAKE_DOCKER = r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
emit_reset_password() {
  if [ "${FAKE_RESET_FAIL:-0}" = 1 ]; then
    return 3
  elif [ "${FAKE_RESET_EMPTY:-0}" = 1 ]; then
    return 0
  elif [ "${FAKE_RESET_MULTILINE:-0}" = 1 ]; then
    printf '%s\n' 'unexpected diagnostic' 'TestResetPassword-654321'
  else
    printf '%s\n' 'TestResetPassword-654321'
  fi
}
case "$1" in
  info) exit 0 ;;
  compose)
    shift
    while :; do
      case "${1:-}" in
        -p|--project-name|-f|--file|--env-file) shift 2 ;;
        --project-name=*|--file=*|--env-file=*) shift ;;
        *) break ;;
      esac
    done
    case "$1" in
      version)
        if [ "${FAKE_DOCKER_COMPOSE_MISSING:-0}" = 1 ] && [ ! -f "$PWD/compose-installed" ]; then
          exit 1
        fi
        printf '%s\n' 'Docker Compose version fake'
        ;;
      config|build|ps) exit 0 ;;
      up) : > "$PWD/container-running" ;;
      down)
        if [ "${FAKE_REQUIRE_CLEAN_COMPOSE_ENV:-0}" = 1 ]; then
          [ -z "${COMPOSE_FILE:-}${COMPOSE_PROJECT_NAME:-}${COMPOSE_PROFILES:-}${COMPOSE_ENV_FILES:-}${COMPOSE_PATH_SEPARATOR:-}" ] || exit 5
          [ "${COMPOSE_REMOVE_ORPHANS:-}" = 0 ] || exit 6
        fi
        [ "${FAKE_COMPOSE_DOWN_FAIL:-0}" != 1 ] || exit 4
        rm -f "$PWD/container-running"
        [ "${FAKE_CONTAINER_REMAINS_AFTER_DOWN:-0}" = 1 ] || : > "$PWD/container-removed"
        ;;
      run)
        case "$*" in
          *reset-admin*)
            emit_reset_password
            ;;
          *bootstrap-admin*)
            if [ "${FAKE_EXISTING_ADMIN:-0}" != 1 ]; then
              printf '%s\n' 'TestInitialPassword-123456'
            fi
            ;;
          *) exit 2 ;;
        esac
        ;;
      exec)
        case "$*" in
          *reset-admin*) emit_reset_password ;;
          *) exit 2 ;;
        esac
        ;;
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
  image)
    case "$2" in
      inspect) [ "${FAKE_IMAGE_EXISTS:-1}" = 1 ] ;;
      rm) [ "${FAKE_IMAGE_RM_FAIL:-0}" != 1 ] ;;
      *) exit 2 ;;
    esac
    ;;
  inspect)
    [ ! -f "$PWD/container-removed" ] || exit 1
    state=${FAKE_SCRCPYGATE_STATE:-}
    [ -n "$state" ] || { [ ! -f "$PWD/container-running" ] || state=running; }
    [ -n "$state" ] || exit 1
    case "$*" in
      *com.docker.compose.project.config_files*) printf '%s\n' "${FAKE_SCRCPYGATE_CONFIG_FILES:-$PWD/compose.yaml}" ;;
      *com.docker.compose.project.working_dir*) printf '%s\n' "${FAKE_SCRCPYGATE_WORKING_DIR:-$PWD}" ;;
      *com.docker.compose.project*) printf '%s\n' "${FAKE_SCRCPYGATE_PROJECT:-scrcpygate}" ;;
      *com.docker.compose.service*) printf '%s\n' "${FAKE_SCRCPYGATE_SERVICE:-scrcpygate}" ;;
      *'.Config.Image'*) printf '%s\n' "${FAKE_SCRCPYGATE_IMAGE:-scrcpygate:local}" ;;
      *'.Type'*) printf '%s\n' "${FAKE_SCRCPYGATE_DATA_TYPE:-bind}" ;;
      *'.Destination "/app/data"'*) printf '%s\n' "${FAKE_SCRCPYGATE_DATA_SOURCE:-$PWD/data}" ;;
      *Health*) printf '%s\n' 'healthy' ;;
      *) printf '%s\n' "$state" ;;
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
            "ALLOW_NULL_ORIGIN",
            "TRUST_PROXY",
            "TRUSTED_PROXY_IPS",
            "SESSION_COOKIE_SECURE",
            "SCRCPYGATE_HEALTH_TIMEOUT",
            "INITIAL_ADMIN_PASSWORD",
            "SCRCPYGATE_AUTO_INSTALL_DEPS",
            "SCRCPYGATE_DETECTED_IP",
            "SCRCPYGATE_PACKAGE_MANAGER",
            "SCRCPYGATE_SHOW_PRIVATE_IPS",
            "FAKE_DOCKER_COMPOSE_MISSING",
            "FAKE_EXISTING_ADMIN",
            "FAKE_RESET_EMPTY",
            "FAKE_RESET_FAIL",
            "FAKE_RESET_MULTILINE",
            "FAKE_SCRCPYGATE_STATE",
            "FAKE_SCRCPYGATE_PROJECT",
            "FAKE_SCRCPYGATE_SERVICE",
            "FAKE_SCRCPYGATE_WORKING_DIR",
            "FAKE_SCRCPYGATE_CONFIG_FILES",
            "FAKE_SCRCPYGATE_IMAGE",
            "FAKE_SCRCPYGATE_DATA_TYPE",
            "FAKE_SCRCPYGATE_DATA_SOURCE",
            "FAKE_COMPOSE_DOWN_FAIL",
            "FAKE_CONTAINER_REMAINS_AFTER_DOWN",
            "FAKE_IMAGE_EXISTS",
            "FAKE_IMAGE_RM_FAIL",
            "FAKE_REQUIRE_CLEAN_COMPOSE_ENV",
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
            "COMPOSE_PROFILES",
            "COMPOSE_ENV_FILES",
            "COMPOSE_PATH_SEPARATOR",
            "COMPOSE_REMOVE_ORPHANS",
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

    def shell_realpath(self, path: Path) -> str:
        result = subprocess.run(
            [self.shell(), "-c", 'cd -- "$1" && pwd -P', "sh", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def compose_down_calls(self, target: Path) -> list[str]:
        calls = (target / "docker.log").read_text(encoding="utf-8").splitlines()
        return [line for line in calls if line.startswith("compose ") and line.endswith(" down")]

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
        self.assertIn("--uninstall", result.stdout)

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
        self.assertIn("卸载 ScrcpyGate", result.stdout)
        self.assertFalse((target / ".env").exists())

    def test_menu_configuration_isolated_from_later_actions(self):
        deploy_text = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("9) (configure_only_flow) || true; pause_menu ;;", deploy_text)

    def test_uninstall_without_container_is_idempotent(self):
        target = self.prepare_installer()
        result = self.run_installer(target, "--uninstall", TERM="dumb")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.compose_down_calls(target), [])
        self.assertIn("无需卸载", result.stdout)

    def test_noninteractive_uninstall_removes_owned_service_and_preserves_files(self):
        target = self.prepare_installer()
        shutil.copy2(target / ".env.example", target / ".env")
        data_dir = target / "data"
        data_dir.mkdir()
        marker = data_dir / "keep-me.txt"
        marker.write_text("preserve", encoding="utf-8")

        result = self.run_installer(
            target,
            "--uninstall",
            FAKE_SCRCPYGATE_STATE="running",
            WEB_SCRCPY_DATA_HOST=str(target / "must-not-be-used"),
            COMPOSE_FILE="/srv/other/compose.yaml",
            COMPOSE_PROJECT_NAME="other-project",
            COMPOSE_PROFILES="other-profile",
            COMPOSE_ENV_FILES="/srv/other/.env",
            COMPOSE_PATH_SEPARATOR=";",
            COMPOSE_REMOVE_ORPHANS="true",
            FAKE_REQUIRE_CLEAN_COMPOSE_ENV="1",
            TERM="dumb",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        down_calls = self.compose_down_calls(target)
        self.assertEqual(len(down_calls), 1)
        self.assertIn("--project-name scrcpygate", down_calls[0])
        self.assertIn("--file", down_calls[0])
        self.assertIn("compose.yaml down", down_calls[0])
        self.assertNotIn("remove-orphans", calls)
        self.assertNotIn("image rm scrcpygate:local", calls)
        self.assertTrue(marker.is_file())
        self.assertTrue((target / ".env").is_file())
        self.assertIn("非交互卸载已保留", result.stdout)

    def test_interactive_uninstall_can_remove_image_project_data_and_environment(self):
        target = self.prepare_installer()
        shutil.copy2(target / ".env.example", target / ".env")
        data_dir = target / "data"
        data_dir.mkdir()
        (data_dir / "delete-me.txt").write_text("delete", encoding="utf-8")
        confirmed_path = self.shell_realpath(data_dir)

        result = self.run_installer(
            target,
            "--uninstall",
            input_text=f"y\ny\ny\n{confirmed_path}\ny\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            FAKE_SCRCPYGATE_STATE="running",
            TERM="dumb",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertEqual(len(self.compose_down_calls(target)), 1)
        self.assertNotIn("remove-orphans", calls)
        self.assertIn("image rm scrcpygate:local", calls)
        self.assertFalse(data_dir.exists())
        self.assertFalse((target / ".env").exists())
        self.assertIn("数据目录已删除", result.stdout)
        self.assertIn(".env 部署配置已删除", result.stdout)

    def test_uninstall_keeps_data_when_confirmed_path_does_not_match(self):
        target = self.prepare_installer()
        shutil.copy2(target / ".env.example", target / ".env")
        data_dir = target / "data"
        data_dir.mkdir()

        result = self.run_installer(
            target,
            "--uninstall",
            input_text="y\n\ny\nnot-the-data-path\n\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            FAKE_SCRCPYGATE_STATE="running",
            TERM="dumb",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(data_dir.is_dir())
        self.assertIn("确认路径不匹配", result.stderr)

    def test_uninstall_never_deletes_an_external_data_directory(self):
        target = self.prepare_installer()
        external_data = Path(tempfile.mkdtemp(prefix="scrcpygate-external-data-"))
        self.addCleanup(shutil.rmtree, external_data, True)
        marker = external_data / "keep-me.txt"
        marker.write_text("preserve", encoding="utf-8")
        external_path = self.shell_realpath(external_data)
        env_text = (target / ".env.example").read_text(encoding="utf-8")
        env_text = env_text.replace(
            "WEB_SCRCPY_DATA_HOST=./data",
            f"WEB_SCRCPY_DATA_HOST={external_path}",
        )
        (target / ".env").write_text(env_text, encoding="utf-8", newline="\n")

        result = self.run_installer(
            target,
            "--uninstall",
            input_text="y\n\n\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            FAKE_SCRCPYGATE_STATE="running",
            FAKE_SCRCPYGATE_DATA_SOURCE=external_path,
            TERM="dumb",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(marker.is_file())
        self.assertIn("项目目录之外", result.stderr)

    def test_uninstall_refuses_owned_labels_when_disk_data_path_is_missing(self):
        target = self.prepare_installer()
        shutil.copy2(target / ".env.example", target / ".env")

        result = self.run_installer(
            target,
            "--uninstall",
            FAKE_SCRCPYGATE_STATE="running",
            TERM="dumb",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.compose_down_calls(target), [])
        self.assertIn("无法从磁盘配置解析数据目录", result.stderr)

    def test_uninstall_refuses_containers_without_exact_ownership(self):
        mismatches = {
            "FAKE_SCRCPYGATE_PROJECT": "legacy-project",
            "FAKE_SCRCPYGATE_SERVICE": "other-service",
            "FAKE_SCRCPYGATE_WORKING_DIR": "/srv/other-project",
            "FAKE_SCRCPYGATE_CONFIG_FILES": "/srv/other-project/compose.yaml",
            "FAKE_SCRCPYGATE_IMAGE": "other-image:latest",
            "FAKE_SCRCPYGATE_DATA_SOURCE": "/srv/other-data",
        }
        for variable, value in mismatches.items():
            with self.subTest(variable=variable):
                target = self.prepare_installer()
                shutil.copy2(target / ".env.example", target / ".env")
                (target / "data").mkdir()
                result = self.run_installer(
                    target,
                    "--uninstall",
                    FAKE_SCRCPYGATE_STATE="running",
                    TERM="dumb",
                    **{variable: value},
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.compose_down_calls(target), [])
                self.assertIn("不会自动接管", result.stderr)

    def test_uninstall_stops_before_cleanup_when_compose_down_fails(self):
        target = self.prepare_installer()
        shutil.copy2(target / ".env.example", target / ".env")
        data_dir = target / "data"
        data_dir.mkdir()

        result = self.run_installer(
            target,
            "--uninstall",
            FAKE_SCRCPYGATE_STATE="running",
            FAKE_COMPOSE_DOWN_FAIL="1",
            TERM="dumb",
        )

        self.assertNotEqual(result.returncode, 0)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertEqual(len(self.compose_down_calls(target)), 1)
        self.assertNotIn("image rm", calls)
        self.assertTrue(data_dir.is_dir())
        self.assertTrue((target / ".env").is_file())

    def test_uninstall_stops_before_cleanup_when_container_remains(self):
        target = self.prepare_installer()
        shutil.copy2(target / ".env.example", target / ".env")
        (target / "data").mkdir()

        result = self.run_installer(
            target,
            "--uninstall",
            FAKE_SCRCPYGATE_STATE="running",
            FAKE_CONTAINER_REMAINS_AFTER_DOWN="1",
            TERM="dumb",
        )

        self.assertNotEqual(result.returncode, 0)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertEqual(len(self.compose_down_calls(target)), 1)
        self.assertNotIn("image rm", calls)
        self.assertIn("容器仍然存在", result.stderr)

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

    def test_configuration_wizard_uses_detected_ip_and_default_port(self):
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
        self.assertIn("已使用默认服务端口 5000", result.stdout)
        config = (target / ".env").read_text(encoding="utf-8")
        self.assertIn("WEB_SCRCPY_PORT=5000", config)
        self.assertIn("PUBLIC_BASE_URL=http://192.0.2.77:5000", config)
        self.assertIn("ALLOWED_HOSTS=127.0.0.1,localhost,192.0.2.77", config)

    def test_configuration_wizard_redacts_private_ip_but_saves_real_value(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--configure",
            input_text="2\n\n\n\n\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="10.23.45.67",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("10.23.45.67", result.stdout)
        self.assertIn("<private-ip>", result.stdout)
        config = (target / ".env").read_text(encoding="utf-8")
        self.assertIn("PUBLIC_BASE_URL=http://10.23.45.67:5000", config)
        self.assertIn("ALLOWED_HOSTS=127.0.0.1,localhost,10.23.45.67", config)

    def test_configuration_wizard_can_explicitly_show_private_ip(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--configure",
            input_text="2\n\n\n\n\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="10.23.45.67",
            SCRCPYGATE_SHOW_PRIVATE_IPS="true",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("10.23.45.67", result.stdout)
        self.assertNotIn("<private-ip>", result.stdout)

    def test_running_instance_can_be_recreated_after_configuration(self):
        target = self.prepare_installer()
        (target / "data").mkdir()
        result = self.run_installer(
            target,
            "--configure",
            input_text="1\n\n\n\ny\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.77",
            FAKE_SCRCPYGATE_STATE="running",
            FAKE_SCRCPYGATE_PROJECT="scrcpygate",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("检测到现有 ScrcpyGate 实例", result.stdout)
        self.assertIn("[y/N]", result.stdout)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertIn("compose up -d --force-recreate scrcpygate", calls)

    def test_running_instance_can_move_to_a_new_data_directory(self):
        target = self.prepare_installer()
        (target / "data").mkdir()
        result = self.run_installer(
            target,
            "--configure",
            input_text="1\n\n./new-data\ny\ny\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.77",
            FAKE_SCRCPYGATE_STATE="running",
            TERM="dumb",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((target / "new-data").is_dir())
        config = (target / ".env").read_text(encoding="utf-8")
        self.assertIn("WEB_SCRCPY_DATA_HOST=./new-data", config)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertIn("compose up -d --force-recreate scrcpygate", calls)

    def test_running_instance_is_not_recreated_on_default_no(self):
        target = self.prepare_installer()
        (target / "data").mkdir()
        result = self.run_installer(
            target,
            "--configure",
            input_text="1\n\n\n\n\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.77",
            FAKE_SCRCPYGATE_STATE="running",
            FAKE_SCRCPYGATE_PROJECT="scrcpygate",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("当前运行实例仍使用旧配置", result.stderr)
        config = (target / ".env").read_text(encoding="utf-8")
        self.assertIn("WEB_SCRCPY_PORT=5000", config)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertNotIn("compose up", calls)

    def test_existing_container_mount_recovers_missing_disk_configuration(self):
        target = self.prepare_installer()
        recovered_data = target / "legacy-data"
        recovered_data.mkdir()
        recovered_path = self.shell_realpath(recovered_data)

        result = self.run_installer(
            target,
            "--configure",
            input_text="1\n\n\n\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            FAKE_SCRCPYGATE_STATE="exited",
            FAKE_SCRCPYGATE_PROJECT="scrcpygate",
            FAKE_SCRCPYGATE_DATA_SOURCE=recovered_path,
            TERM="dumb",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("已恢复现有容器挂载", result.stdout)
        config = (target / ".env").read_text(encoding="utf-8")
        self.assertIn(f"WEB_SCRCPY_DATA_HOST={recovered_path}", config)

    def test_existing_named_volume_is_not_auto_managed(self):
        target = self.prepare_installer()
        shutil.copy2(target / ".env.example", target / ".env")
        data_dir = target / "data"
        data_dir.mkdir()

        result = self.run_installer(
            target,
            "--uninstall",
            FAKE_SCRCPYGATE_STATE="running",
            FAKE_SCRCPYGATE_DATA_TYPE="volume",
            FAKE_SCRCPYGATE_DATA_SOURCE=self.shell_realpath(data_dir),
            TERM="dumb",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.compose_down_calls(target), [])
        self.assertIn("不会自动接管", result.stderr)

    def test_guided_install_requires_confirmation_for_any_existing_container(self):
        for state in ("running", "exited"):
            with self.subTest(state=state):
                target = self.prepare_installer()
                (target / "data").mkdir()
                result = self.run_installer(
                    target,
                    "--menu",
                    input_text="1\n1\n\n\n\n\n\n0\n",
                    SCRCPYGATE_FORCE_INTERACTIVE="1",
                    SCRCPYGATE_DETECTED_IP="192.0.2.77",
                    FAKE_SCRCPYGATE_STATE=state,
                    FAKE_SCRCPYGATE_PROJECT="scrcpygate",
                    TERM="dumb",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("[y/N]", result.stdout)
                calls = (target / "docker.log").read_text(encoding="utf-8")
                self.assertNotIn("compose build", calls)
                self.assertNotIn("compose up", calls)

    def test_guided_install_redeploys_existing_container_once_when_confirmed(self):
        target = self.prepare_installer()
        (target / "data").mkdir()
        result = self.run_installer(
            target,
            "--menu",
            input_text="1\n1\n\n\n\ny\n\n0\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.77",
            FAKE_SCRCPYGATE_STATE="running",
            FAKE_SCRCPYGATE_PROJECT="scrcpygate",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (target / "docker.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(calls.count("compose build"), 1)
        self.assertEqual(calls.count("compose up -d"), 1)

    def test_guided_install_does_not_take_over_another_compose_project(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--menu",
            input_text="1\n\n0\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.77",
            FAKE_SCRCPYGATE_STATE="running",
            FAKE_SCRCPYGATE_PROJECT="legacy-project",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("不会自动接管", result.stderr)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertNotIn("compose build", calls)
        self.assertNotIn("compose up", calls)

    def test_configuration_wizard_builds_reverse_proxy_settings(self):
        target = self.prepare_installer()
        answers = (
            "3\n51234\n./proxy-data\n192.0.2.16\nexample.com\nhttps\n"
            "22263\n192.0.2.15\nhttp://192.0.2.16:51234\ntrue\n\n"
        )
        result = self.run_installer(
            target,
            "--configure",
            input_text=answers,
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.16",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = (target / ".env").read_text(encoding="utf-8")
        expected = (
            "WEB_SCRCPY_BIND=192.0.2.16",
            "WEB_SCRCPY_PORT=51234",
            "PUBLIC_BASE_URL=https://example.com:22263",
            "ALLOWED_HOSTS=127.0.0.1,localhost,example.com,192.0.2.16",
            "ALLOWED_ORIGINS=https://example.com:22263,http://192.0.2.16:51234",
            "ALLOW_NULL_ORIGIN=true",
            "TRUST_PROXY=true",
            "TRUSTED_PROXY_IPS=192.0.2.15",
            "SESSION_COOKIE_SECURE=true",
        )
        for item in expected:
            self.assertIn(item, config)

    def test_reverse_proxy_domain_prompt_rejects_a_url(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--configure",
            input_text="3\n\n\n\nhttps://example.com\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.16",
            TERM="dumb",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("示例：example.com", result.stdout)
        self.assertIn("不要输入 http://", result.stdout)
        self.assertIn("请输入纯域名或 IP", result.stderr)
        config = (target / ".env").read_text(encoding="utf-8")
        self.assertIn("PUBLIC_BASE_URL=http://127.0.0.1:5000", config)

    def test_reverse_proxy_default_https_port_is_not_rendered(self):
        target = self.prepare_installer()
        answers = "3\n51235\n\n\nexample.com\n\n\n192.0.2.15\n\n\n\n"
        result = self.run_installer(
            target,
            "--configure",
            input_text=answers,
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            SCRCPYGATE_DETECTED_IP="192.0.2.16",
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

    def test_existing_admin_can_be_reset_during_interactive_install(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--install",
            input_text="y\n",
            SCRCPYGATE_FORCE_INTERACTIVE="1",
            FAKE_EXISTING_ADMIN="1",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("检测到现有管理员账号", result.stdout)
        self.assertIn("保持原密码", result.stdout)
        self.assertIn("管理员密码已重置", result.stdout)
        self.assertIn("TestResetPassword-654321", result.stdout)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertIn("python -m app.cli reset-admin", calls)

    def test_noninteractive_install_never_resets_existing_admin(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--install",
            FAKE_EXISTING_ADMIN="1",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("已保留现有管理员密码", result.stdout)
        self.assertNotIn("[y/N]", result.stdout)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertNotIn("python -m app.cli reset-admin", calls)

    def test_existing_admin_reset_fails_closed_on_invalid_output(self):
        for flag in ("FAKE_RESET_FAIL", "FAKE_RESET_EMPTY", "FAKE_RESET_MULTILINE"):
            with self.subTest(flag=flag):
                target = self.prepare_installer()
                result = self.run_installer(
                    target,
                    "--install",
                    input_text="y\n",
                    SCRCPYGATE_FORCE_INTERACTIVE="1",
                    FAKE_EXISTING_ADMIN="1",
                    TERM="dumb",
                    **{flag: "1"},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("管理员密码已重置（请立即保存）", result.stdout)
                calls = (target / "docker.log").read_text(encoding="utf-8")
                self.assertNotIn("compose up", calls)

    def test_standalone_reset_admin_displays_one_new_password(self):
        target = self.prepare_installer()
        result = self.run_installer(target, "--reset-admin", TERM="dumb")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("管理员密码已重置", result.stdout)
        self.assertIn("TestResetPassword-654321", result.stdout)

    def test_standalone_reset_admin_fails_closed_on_invalid_output(self):
        for flag in ("FAKE_RESET_FAIL", "FAKE_RESET_EMPTY", "FAKE_RESET_MULTILINE"):
            with self.subTest(flag=flag):
                target = self.prepare_installer()
                result = self.run_installer(target, "--reset-admin", TERM="dumb", **{flag: "1"})

                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("新密码", result.stdout)

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

    def test_noninteractive_install_does_not_prompt_for_running_instance(self):
        target = self.prepare_installer()
        result = self.run_installer(
            target,
            "--skip-build",
            FAKE_SCRCPYGATE_STATE="running",
            FAKE_SCRCPYGATE_PROJECT="scrcpygate",
            TERM="dumb",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("[y/N]", result.stdout)
        calls = (target / "docker.log").read_text(encoding="utf-8")
        self.assertIn("compose up -d", calls)

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
