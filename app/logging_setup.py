# app/logging_setup.py
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta, timezone


def _cleanup_old_logs(log_dir: Path, retention_days: int) -> None:
    """
    Delete log files older than retention_days.
    Works even if retention_days is reduced after logs already exist.
    """
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        for p in log_dir.glob("doc_sorter.log*"):
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    p.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def setup_logging(log_dir: Path, retention_days: int = 7) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    retention_days = int(retention_days or 7)
    if retention_days < 1:
        retention_days = 1

    logger = logging.getLogger("doc_sorter")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # alte Handler entfernen/close
    for h in list(logger.handlers):
        try:
            h.flush()
            h.close()
        except Exception:
            pass
        logger.removeHandler(h)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Daily rotation at midnight, keep last N days
    file_handler = TimedRotatingFileHandler(
        filename=str(log_dir / "doc_sorter.log"),
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8",
        delay=False,
        utc=False,  # local time rotation (Europe/Berlin via container TZ)
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    _cleanup_old_logs(log_dir, retention_days)

    logger.info("Logger initialized. Log dir=%s | retention_days=%s", str(log_dir), retention_days)
    for h in logger.handlers:
        try:
            h.flush()
        except Exception:
            pass

    return logger
