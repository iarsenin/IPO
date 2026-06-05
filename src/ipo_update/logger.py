from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
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


def cleanup_old_logs(log_dir: Path | str, retention_days: int = 30) -> int:
    """Delete IPO update logs older than the retention window."""
    if retention_days <= 0:
        return 0

    log_dir = Path(log_dir).expanduser()
    if not log_dir.exists():
        return 0

    cutoff = datetime.now() - timedelta(days=retention_days)
    deleted = 0
    for path in log_dir.glob("ipo_update_*.log"):
        try:
            if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted


def setup_logging(log_dir: Path | str = "log", retention_days: int = 30) -> logging.Logger:
    """Configure logging with date-based file and stdout/stderr capture."""
    log_dir = Path(log_dir).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    deleted_logs = cleanup_old_logs(log_dir, retention_days)

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
    if deleted_logs:
        root_logger.info(
            f"Cleaned up {deleted_logs} log files older than {retention_days} days in {log_dir}"
        )
    return root_logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
