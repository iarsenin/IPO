from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


class StreamToLogger:
    """Redirect stdout/stderr to logger."""

    def __init__(self, logger: logging.Logger, log_level: int):
        self.logger = logger
        self.log_level = log_level
        self.linebuf = ""

    def write(self, buf: str) -> None:
        for line in buf.rstrip().splitlines():
            self.logger.log(self.log_level, line.rstrip())

    def flush(self) -> None:
        pass


def setup_logging(log_dir: Path | str = "log") -> logging.Logger:
    """Configure logging with date-based file and stdout/stderr capture."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"ipo_update_{timestamp}.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    stdout_logger = StreamToLogger(root_logger, logging.INFO)
    stderr_logger = StreamToLogger(root_logger, logging.ERROR)
    sys.stdout = stdout_logger
    sys.stderr = stderr_logger

    root_logger.info(f"Logging initialized: {log_file}")
    return root_logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
