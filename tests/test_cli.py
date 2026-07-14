import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.data_dir = Path(tempfile.mkdtemp(prefix="scrcpygate-cli-test-"))

    def tearDown(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def run_cli(self, command: str):
        env = os.environ.copy()
        env["WEB_SCRCPY_DATA_DIR"] = str(self.data_dir)
        env["INITIAL_ADMIN_PASSWORD"] = "StrongInitialPwd123"
        return subprocess.run(
            [sys.executable, "-m", "app.cli", command],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )

    def test_bootstrap_password_is_only_printed_when_admin_is_created(self):
        first = self.run_cli("bootstrap-admin")
        second = self.run_cli("bootstrap-admin")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout.strip(), "StrongInitialPwd123")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
