from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from collections.abc import Mapping

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import ApiError, ErrorCode
from app.db.models.api_keys import ApiKey
from app.db.session import create_sessionmaker, with_sqlite_busy_retry


def _coerce_int(value: int, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_api_key(value: str) -> str:
    v = (value or "").strip()
    return v


def hmac_sha256_hex(*, secret_key: str, message: str) -> str:
    secret_key = (secret_key or "").strip()
    if not secret_key:
        raise ValueError("SECRET_KEY is required")
    mac = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), digestmod=hashlib.sha256)
    return mac.hexdigest()


def api_key_hint(api_key: str) -> str:
    api_key = _normalize_api_key(api_key)
    if not api_key:
        return ""
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return digest[:8]


@dataclass(frozen=True, slots=True)
class ApiKeyAuthConfig:
    required: bool
    rpm: int
    burst: int
    secret_key: str


@dataclass(slots=True)
class _CacheEntry:
    api_key_id: int
    enabled: bool
    expires_at_m: float


@dataclass(slots=True)
class ApiKeyAuthenticator:
    engine: AsyncEngine
    cfg: ApiKeyAuthConfig
    cache_ttl_s: float = 5.0
    _cache: dict[str, _CacheEntry] = field(default_factory=dict)

    async def _lookup(self, key_hash: str, *, now_m: float) -> _CacheEntry | None:
        Session = create_sessionmaker(self.engine)

        async def _op() -> _CacheEntry | None:
            async with Session() as session:
                row = (
                    (
                        await session.execute(
                            sa.select(ApiKey.id, ApiKey.enabled).where(ApiKey.key_hash == key_hash).limit(1)
                        )
                    )
                    .first()
                )
                if row is None:
                    return None
                return _CacheEntry(
                    api_key_id=int(row[0]),
                    enabled=bool(int(row[1] or 0)),
                    expires_at_m=float(now_m) + float(self.cache_ttl_s),
                )

        return await with_sqlite_busy_retry(_op)

    async def authenticate(self, api_key: str) -> int | None:
        api_key = _normalize_api_key(api_key)
        if not api_key:
            return None

        key_hash = hmac_sha256_hex(secret_key=self.cfg.secret_key, message=api_key)
        now_m = time.monotonic()

        cached = self._cache.get(key_hash)
        if cached is not None and float(cached.expires_at_m) > float(now_m):
            return int(cached.api_key_id) if bool(cached.enabled) else None

        entry = await self._lookup(key_hash, now_m=now_m)
        if entry is None:
            self._cache[key_hash] = _CacheEntry(api_key_id=0, enabled=False, expires_at_m=float(now_m) + 2.0)
            return None

        self._cache[key_hash] = entry

        if len(self._cache) > 10_000:
            self._cache = {k: v for k, v in self._cache.items() if float(v.expires_at_m) > float(now_m)}

        return int(entry.api_key_id) if bool(entry.enabled) else None


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at_m: float


@dataclass(slots=True)
class ApiKeyRateLimiter:
    rpm: int
    burst: int
    _buckets: dict[int, _Bucket] = field(default_factory=dict)

    def _params(self) -> tuple[float, float]:
        rpm = max(0, _coerce_int(self.rpm))
        if rpm <= 0:
            return 0.0, 0.0
        capacity = max(1.0, float(_coerce_int(self.burst)) or float(rpm))
        refill_per_s = float(rpm) / 60.0
        return capacity, refill_per_s

    def allow(self, api_key_id: int) -> bool:
        api_key_id_i = _coerce_int(api_key_id)
        if api_key_id_i <= 0:
            return False

        capacity, refill_per_s = self._params()
        if capacity <= 0.0 or refill_per_s <= 0.0:
            return True

        now_m = time.monotonic()
        b = self._buckets.get(api_key_id_i)
        if b is None:
            self._buckets[api_key_id_i] = _Bucket(tokens=float(capacity) - 1.0, updated_at_m=now_m)
            return True

        elapsed = max(0.0, float(now_m) - float(b.updated_at_m))
        b.tokens = min(float(capacity), float(b.tokens) + elapsed * float(refill_per_s))
        b.updated_at_m = now_m
        if float(b.tokens) >= 1.0:
            b.tokens -= 1.0
            return True
        return False


def extract_api_key(headers: Mapping[str, str] | None) -> str | None:
    if not headers:
        return None
    return (headers.get("X-API-Key") or headers.get("x-api-key") or "").strip() or None


async def require_public_api_key(
    authenticator: ApiKeyAuthenticator,
    limiter: ApiKeyRateLimiter,
    *,
    headers: Mapping[str, str] | None,
) -> int:
    api_key = extract_api_key(headers)
    if not api_key:
        raise ApiError(code=ErrorCode.UNAUTHORIZED, message="Missing API key", status_code=401)

    api_key_id = await authenticator.authenticate(api_key)
    if api_key_id is None:
        raise ApiError(code=ErrorCode.UNAUTHORIZED, message="Invalid API key", status_code=401)

    if not limiter.allow(api_key_id):
        raise ApiError(code=ErrorCode.RATE_LIMITED, message="Rate limited", status_code=429)

    return int(api_key_id)
