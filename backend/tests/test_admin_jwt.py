from __future__ import annotations

import pytest

from app.core.errors import ApiError, ErrorCode
from app.core.security import create_jwt, decode_jwt, require_admin


def test_admin_auth_jwt_roundtrip() -> None:
    token = create_jwt(secret_key="secret", subject="admin", ttl_s=60)
    claims = decode_jwt(token, secret_key="secret")
    assert claims["sub"] == "admin"
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)


def test_admin_auth_jwt_expired() -> None:
    token = create_jwt(secret_key="secret", subject="admin", ttl_s=-1)
    with pytest.raises(ValueError, match="expired"):
        decode_jwt(token, secret_key="secret")


def test_admin_auth_jwt_invalid_signature() -> None:
    token = create_jwt(secret_key="secret1", subject="admin", ttl_s=60)
    with pytest.raises(ValueError, match="signature"):
        decode_jwt(token, secret_key="secret2")


def test_admin_auth_require_admin_allows_valid_token() -> None:
    token = create_jwt(secret_key="secret", subject="admin", ttl_s=60)
    claims = require_admin(
        {"Authorization": f"Bearer {token}"},
        secret_key="secret",
        admin_username="admin",
    )
    assert claims["sub"] == "admin"


def test_admin_auth_require_admin_rejects_missing_token() -> None:
    with pytest.raises(ApiError) as excinfo:
        require_admin({}, secret_key="secret", admin_username="admin")
    assert excinfo.value.code == ErrorCode.UNAUTHORIZED
    assert excinfo.value.status_code == 401


def test_admin_auth_require_admin_rejects_wrong_subject() -> None:
    token = create_jwt(secret_key="secret", subject="not-admin", ttl_s=60)
    with pytest.raises(ApiError) as excinfo:
        require_admin({"Authorization": f"Bearer {token}"}, secret_key="secret", admin_username="admin")
    assert excinfo.value.code == ErrorCode.FORBIDDEN
    assert excinfo.value.status_code == 403
