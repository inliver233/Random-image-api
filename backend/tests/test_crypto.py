from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import FieldEncryptor, MASKED, mask_secret


def test_mask_secret() -> None:
    assert mask_secret(None) == ""
    assert mask_secret("") == ""
    assert mask_secret("   ") == ""
    assert mask_secret("secret") == MASKED


def test_encrypt_decrypt_roundtrip() -> None:
    key = Fernet.generate_key().decode("utf-8")
    crypto = FieldEncryptor.from_key(key)

    plaintext = "refresh_token_123"
    token = crypto.encrypt_text(plaintext)

    assert isinstance(token, str)
    assert token != plaintext
    assert crypto.decrypt_text(token) == plaintext


def test_requires_key() -> None:
    with pytest.raises(ValueError, match="FIELD_ENCRYPTION_KEY is required"):
        FieldEncryptor.from_key("")


def test_invalid_key_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid FIELD_ENCRYPTION_KEY"):
        FieldEncryptor.from_key("not-a-fernet-key")


def test_invalid_token_rejected() -> None:
    key = Fernet.generate_key().decode("utf-8")
    crypto = FieldEncryptor.from_key(key)
    with pytest.raises(ValueError, match="Invalid encrypted value"):
        crypto.decrypt_text("not-a-fernet-token")

