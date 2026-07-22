import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ResolutionSafetyTests(unittest.TestCase):
    def test_no_device_display_mutation_commands_exist_in_runtime_code(self):
        banned = [
            "wm" + " " + "size",
            "wm" + " " + "density",
            "modify" + "dev",
            "override" + " size:",
            "physical" + " size:",
        ]
        extensions = {".py", ".html", ".js", ".sh"}
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            if path.parts[-2:-1] == ("tests",):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            for pattern in banned:
                if pattern in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{pattern}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
