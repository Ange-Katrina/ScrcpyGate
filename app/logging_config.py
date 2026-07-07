import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


DATA_DIR = Path(os.environ.get("WEB_SCRCPY_DATA_DIR", "data"))
LOG_FILE = DATA_DIR / "webscrcpy.log"
_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.setLevel(level)
    root.addHandler(stream)

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    _configured = True


def tail_log(max_lines: int = 300) -> list[str]:
    limit = max(20, min(int(max_lines), 2000))
    if not LOG_FILE.exists():
        return []
    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    return [line.rstrip("\n") for line in lines[-limit:]]
