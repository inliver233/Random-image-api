from __future__ import annotations

import io
import logging

from app.core.logging import RedactFilter
from app.core.redact import REDACTED, redact_any, redact_text


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
