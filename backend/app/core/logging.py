from __future__ import annotations

import logging
from typing import Any

from app.core.redact import redact_any


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            record.msg = redact_any(message)
            record.args = ()
            for key, value in list(record.__dict__.items()):
                if key.startswith("_"):
                    continue
                record.__dict__[key] = redact_any(value)
        except Exception:
            pass
        return True


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger().addFilter(RedactFilter())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
