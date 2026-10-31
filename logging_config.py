"""Logging configuration for takeout-media-bridge.

Provides `setup_logging` which configures both console and optional file logging
with rotation. The file handler is enabled only when ``log_file`` is provided.
"""

import logging
import logging.handlers
from pathlib import Path
from typing import Optional


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> None:
    """Configure root logger with console and optional rotating file handler.

    Args:
        level: Logging level string (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
        log_file: Path to the log file.  When ``None``, file logging is disabled.
        max_bytes: Maximum size of a single log file before rotation (bytes).
        backup_count: Number of rotated backup files to keep.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    fmt = "[%(asctime)s] [%(levelname)s] %(name)s – %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # Optional rotating file handler
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            str(file_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        logging.info("File logging enabled: %s", file_path)
