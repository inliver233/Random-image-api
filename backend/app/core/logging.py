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


def _install_redact_filter(handler: logging.Handler) -> None:
    if any(isinstance(existing, RedactFilter) for existing in handler.filters):
        return
    handler.addFilter(RedactFilter())


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(levelname)s %(name)s %(message)s")
    seen_handlers: set[int] = set()
    for logger_name in (None, "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers:
            handler_id = id(handler)
            if handler_id in seen_handlers:
                continue
            seen_handlers.add(handler_id)
            _install_redact_filter(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
