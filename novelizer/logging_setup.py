from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from novelizer.settings.layers import global_config_path

MAX_BYTES = 5 * 1024 * 1024  # 5 MiB per file
BACKUP_COUNT = 5  # keep this many rotated files, plus the active one

_configured = False


def log_dir() -> Path:
    return global_config_path().parent / "logs"


def configure_logging(level: int = logging.INFO) -> Path:
    """Route all logging to a rotating file so it never collides with the TUI's
    own screen rendering. Idempotent — safe to call from every entrypoint."""
    global _configured
    path = log_dir() / "novelizer.log"
    if _configured:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    ))

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    _configured = True
    return path
