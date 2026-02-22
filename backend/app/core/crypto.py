from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


MASKED = "***"


def mask_secret(value: str | None) -> str:
    value = (value or "").strip()
    return MASKED if value else ""


@dataclass(frozen=True, slots=True)
class FieldEncryptor:
    _fernet: Fernet

    @classmethod
    def from_key(cls, key: str) -> "FieldEncryptor":
        key = (key or "").strip()
        if not key:
            raise ValueError("FIELD_ENCRYPTION_KEY is required")
        try:
            fernet = Fernet(key.encode("utf-8"))
        except Exception as exc:
            raise ValueError("Invalid FIELD_ENCRYPTION_KEY") from exc
        return cls(_fernet=fernet)

    def encrypt_text(self, plaintext: str) -> str:
        if not isinstance(plaintext, str):
            raise TypeError("plaintext must be str")
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt_text(self, token: str) -> str:
        if not isinstance(token, str):
            raise TypeError("token must be str")
        try:
            plaintext = self._fernet.decrypt(token.encode("utf-8"))
        except InvalidToken as exc:
            raise ValueError("Invalid encrypted value") from exc
        return plaintext.decode("utf-8")
