"""Logging configuration for FinSight.

Design decisions:
- One formatter everywhere: "[2026-10-31 14:03:22] [INFO] [sec_client] message"
  Timestamps + level + module make it possible to reconstruct what happened
  during any incident ("why did ingestion stall at 14:03?").
- Console output for interactive use, plus a rotating file so long-running
  monitoring (M8) keeps history without filling the disk.
- get_logger(name) pattern: each module gets its own logger rather than using
  the root logger, so we can tune verbosity per-component later.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_configured = False


def configure_logging(level: str = "INFO", logs_dir: str | None = None) -> None:
    """Set up root logging once per process.

    Idempotent: calling it twice (e.g. from a script that also imports a
    service that calls it) must not duplicate handlers -- that's the classic
    'my log lines appear twice' bug.
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(level.upper())
    formatter = _make_formatter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if logs_dir:
        # 5 MB per file, keep 3 backups: enough history for post-mortems,
        # bounded disk usage for a laptop.
        file_handler = RotatingFileHandler(
            f"{logs_dir}/finsight.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    _configured = True


def _make_formatter() -> logging.Formatter:
    return logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger (call after configure_logging)."""
    return logging.getLogger(name)
