from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


MASKED = "***"

# Process-local cache: Fernet construction validates the key every call; reuse instances.
_FIELD_ENCRYPTOR_CACHE: dict[str, "FieldEncryptor"] = {}
_FIELD_ENCRYPTOR_CACHE_MAX = 8


def mask_secret(value: str | None) -> str:
    value = (value or "").strip()
    return MASKED if value else ""


def reset_field_encryptor_cache_for_tests() -> None:
    """Clear process-local FieldEncryptor cache (tests only)."""
    _FIELD_ENCRYPTOR_CACHE.clear()


@dataclass(frozen=True, slots=True)
class FieldEncryptor:
    _fernet: Fernet

    @classmethod
    def from_key(cls, key: str) -> "FieldEncryptor":
        key_n = (key or "").strip()
        if not key_n:
            raise ValueError("FIELD_ENCRYPTION_KEY is required")
        cached = _FIELD_ENCRYPTOR_CACHE.get(key_n)
        if cached is not None:
            return cached
        try:
            fernet = Fernet(key_n.encode("utf-8"))
        except Exception as exc:
            raise ValueError("Invalid FIELD_ENCRYPTION_KEY") from exc
        inst = cls(_fernet=fernet)
        if len(_FIELD_ENCRYPTOR_CACHE) >= _FIELD_ENCRYPTOR_CACHE_MAX and key_n not in _FIELD_ENCRYPTOR_CACHE:
            try:
                oldest = next(iter(_FIELD_ENCRYPTOR_CACHE))
                _FIELD_ENCRYPTOR_CACHE.pop(oldest, None)
            except StopIteration:
                pass
        _FIELD_ENCRYPTOR_CACHE[key_n] = inst
        return inst

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
