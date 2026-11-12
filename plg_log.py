"""Session log file for support (plg_session.log)."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from plg_paths import app_dir

_LOG_PATH = app_dir() / "plg_session.log"
_logger: logging.Logger | None = None


def _ensure_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("plg.session")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = logging.FileHandler(_LOG_PATH, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
            )
        except OSError:
            # No write access to the app folder (e.g. installed under
            # Program Files without admin rights) — the session log is a
            # nice-to-have for support, not something worth crashing
            # startup over. Fall back to a handler that never touches disk.
            handler = logging.NullHandler()
        logger.addHandler(handler)
    _logger = logger
    return logger


def init_session_log() -> None:
    log = _ensure_logger()
    log.info("--- session start %s ---", datetime.now(timezone.utc).isoformat())


def log_event(message: str) -> None:
    _ensure_logger().info(message)


def read_log_tail(max_chars: int = 12000) -> str:
    if not _LOG_PATH.is_file():
        return ""
    try:
        text = _LOG_PATH.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:]
    except OSError:
        return ""


def log_path() -> Path:
    return _LOG_PATH
