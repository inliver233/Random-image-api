from __future__ import annotations

import io
import logging
from contextlib import contextmanager
from collections.abc import Iterator

from app.core.logging import RedactFilter, configure_logging
from app.core.redact import REDACTED, redact_any, redact_text


@contextmanager
def _preserve_configured_logging_state() -> Iterator[None]:
    loggers = [
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    ]
    logger_states = [(logger, list(logger.handlers), logger.level, logger.propagate) for logger in loggers]
    handlers = {id(handler): handler for logger in loggers for handler in logger.handlers}
    handler_filters = [(handler, list(handler.filters)) for handler in handlers.values()]
    try:
        yield
    finally:
        for logger, previous_handlers, previous_level, previous_propagate in logger_states:
            logger.handlers = previous_handlers
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate
        for handler, previous_filters in handler_filters:
            handler.filters = previous_filters


def test_redact_proxy_uri_password_with_at() -> None:
    raw = "http://user:pa@ss@1.2.3.4:2323"
    redacted = redact_text(raw)
    assert "pa@ss" not in redacted
    assert redacted == "http://user:***@1.2.3.4:2323"


def test_redact_proxy_uri_password_when_embedded_in_text_and_punctuated() -> None:
    raw = "ProxyError: cannot connect to http://user:pa@ss@1.2.3.4:2323, retry"
    redacted = redact_text(raw)
    assert "pa@ss" not in redacted
    assert "http://user:***@1.2.3.4:2323," in redacted


def test_redact_multiple_proxy_uris_in_text() -> None:
    raw = "p1=http://u:p@1.1.1.1:1 p2=socks5://a:b@2.2.2.2:2"
    redacted = redact_text(raw)
    assert "u:p@" not in redacted
    assert "a:b@" not in redacted
    assert "http://u:***@1.1.1.1:1" in redacted
    assert "socks5://a:***@2.2.2.2:2" in redacted


def test_redact_bearer_token() -> None:
    raw = "Authorization: Bearer abc.def.ghi"
    redacted = redact_text(raw)
    assert "abc.def.ghi" not in redacted
    assert "Bearer ***" in redacted


def test_redact_mapping_sensitive_keys() -> None:
    raw = {"refresh_token": "secret", "nested": {"password": "p"}, "ok": 1}
    redacted = redact_any(raw)
    assert redacted["refresh_token"] == REDACTED
    assert redacted["nested"]["password"] == REDACTED
    assert redacted["ok"] == 1


def test_redact_refresh_token_query_param() -> None:
    raw = "GET /auth/token?refresh_token=abc123&x=1"
    redacted = redact_text(raw)
    assert "abc123" not in redacted
    assert "refresh_token=***" in redacted


def test_redact_api_key_query_param() -> None:
    raw = "unhandled_exception path=http://api.example/i/1.jpg?api_key=supersecret&x=1"
    redacted = redact_text(raw)
    assert "supersecret" not in redacted
    assert "api_key=***" in redacted
    assert "x=1" in redacted


def test_logging_filter_redacts_output() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("redact_test")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addFilter(RedactFilter())

    logger.info("Authorization: Bearer %s", "supersecret")
    out = stream.getvalue()
    assert "supersecret" not in out
    assert "***" in out


def test_configure_logging_redacts_records_from_propagating_child_logger() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    logger = logging.getLogger("app.tests.configured_redaction")
    previous_logger_handlers = list(logger.handlers)
    previous_logger_level = logger.level
    previous_logger_propagate = logger.propagate
    with _preserve_configured_logging_state():
        try:
            root.handlers = [handler]
            root.setLevel(logging.INFO)
            logger.handlers = []
            logger.setLevel(logging.INFO)
            logger.propagate = True

            configure_logging()
            logger.info(
                "Authorization: Bearer %s refresh_token=%s proxy=%s",
                "bearer-secret",
                "refresh-secret",
                "http://proxy-user:proxy-password@127.0.0.1:8080",
            )
        finally:
            logger.handlers = previous_logger_handlers
            logger.setLevel(previous_logger_level)
            logger.propagate = previous_logger_propagate

    out = stream.getvalue()
    assert "bearer-secret" not in out
    assert "refresh-secret" not in out
    assert "proxy-password" not in out
    assert "Bearer ***" in out


def test_configure_logging_redacts_uvicorn_access_handler() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("uvicorn.access")
    with _preserve_configured_logging_state():
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        configure_logging()
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1234",
            "GET",
            "/random?api_key=browser-secret&x=1",
            "1.1",
            200,
        )

    out = stream.getvalue()
    assert "browser-secret" not in out
    assert "api_key=***" in out


def test_configure_logging_keeps_uvicorn_access_line_with_real_access_formatter() -> None:
    from uvicorn.logging import AccessFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        AccessFormatter(
            '%(client_addr)s - "%(request_line)s" %(status_code)s',
            use_colors=False,
        )
    )
    logger = logging.getLogger("uvicorn.access")
    with _preserve_configured_logging_state():
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        configure_logging()
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1234",
            "GET",
            "/random?api_key=browser-secret&x=1",
            "1.1",
            200,
        )

    out = stream.getvalue()
    assert '127.0.0.1:1234 - "GET /random?api_key=***&x=1 HTTP/1.1" 200' in out
    assert "browser-secret" not in out


def test_configure_logging_redacts_uvicorn_error_and_is_idempotent() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("uvicorn.error")
    with _preserve_configured_logging_state():
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        configure_logging()
        configure_logging()
        logger.error("upstream Authorization: Bearer %s", "uvicorn-error-secret")

        redact_filters = [item for item in handler.filters if isinstance(item, RedactFilter)]
        assert len(redact_filters) == 1

    out = stream.getvalue()
    assert "uvicorn-error-secret" not in out
    assert "Bearer ***" in out
