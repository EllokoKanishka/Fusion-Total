from __future__ import annotations

import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "fusion_reader_v2"
_CONFIGURE_LOCK = threading.Lock()


class ContextDefaults(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for name in ("request_id", "job_id", "provider"):
            if not hasattr(record, name):
                setattr(record, name, "-")
        return True


def configure_logging(log_file: Path, *, level: int = logging.INFO) -> logging.Logger:
    target = Path(log_file).resolve(strict=False)
    logger = logging.getLogger(LOGGER_NAME)
    with _CONFIGURE_LOCK:
        existing = {
            Path(handler.baseFilename).resolve(strict=False)
            for handler in logger.handlers
            if isinstance(handler, RotatingFileHandler)
        }
        if target not in existing:
            target.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(target, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            handler.addFilter(ContextDefaults())
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s thread=%(threadName)s request_id=%(request_id)s "
                    "job_id=%(job_id)s provider=%(provider)s %(message)s"
                )
            )
            logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
