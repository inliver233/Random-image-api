from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.models.api_keys import ApiKey
from app.db.session import create_sessionmaker, with_sqlite_busy_retry

log = get_logger(__name__)

# Hard ceiling for Redis EVAL on the public auth hot path (fail open past this).
_REDIS_RL_CALL_TIMEOUT_S = 0.15

# Redis token-bucket Lua: KEYS[1]=bucket, ARGV=capacity, refill_per_s, now_s, cost
# Returns 1 if allowed, 0 if limited. Best-effort multi-instance rate limit.
_REDIS_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local data = redis.call('HMGET', key, 'tokens', 'updated')
local tokens = tonumber(data[1])
local updated = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  updated = now
end
local elapsed = now - updated
if elapsed < 0 then
  elapsed = 0
end
tokens = math.min(capacity, tokens + elapsed * refill)
local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end
redis.call('HMSET', key, 'tokens', tokens, 'updated', now)
-- Keep key around long enough for a full refill + slack.
local ttl = math.ceil(capacity / refill) + 5
if ttl < 60 then
  ttl = 60
end
redis.call('EXPIRE', key, ttl)
return allowed
"""


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


@runtime_checkable
class ApiKeyRateLimiterPort(Protocol):
    """Public API key rate-limit port (process-local memory or Redis)."""

    backend: str

    async def allow(self, api_key_id: int) -> bool: ...

    async def aclose(self) -> None: ...


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at_m: float


def _token_bucket_params(*, rpm: int, burst: int) -> tuple[float, float]:
    rpm_i = max(0, _coerce_int(rpm))
    if rpm_i <= 0:
        return 0.0, 0.0
    capacity = max(1.0, float(_coerce_int(burst)) or float(rpm_i))
    refill_per_s = float(rpm_i) / 60.0
    return capacity, refill_per_s


@dataclass(slots=True)
class ApiKeyRateLimiter:
    """Process-local token bucket (default backend)."""

    rpm: int
    burst: int
    backend: str = "memory"
    _buckets: dict[int, _Bucket] = field(default_factory=dict)

    def _params(self) -> tuple[float, float]:
        return _token_bucket_params(rpm=self.rpm, burst=self.burst)

    async def allow(self, api_key_id: int) -> bool:
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

    async def aclose(self) -> None:
        return None


@dataclass(slots=True)
class RedisApiKeyRateLimiter:
    """Cross-instance token bucket via Redis EVAL (optional; requires redis package + URL)."""

    rpm: int
    burst: int
    redis_url: str
    key_prefix: str = "np:api_key_rl:"
    backend: str = "redis"
    _client: Any = field(default=None, repr=False)
    _script: Any = field(default=None, repr=False)
    _fallback: ApiKeyRateLimiter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._fallback = ApiKeyRateLimiter(rpm=int(self.rpm), burst=int(self.burst), backend="memory")

    async def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            import redis.asyncio as redis_async  # type: ignore[import-not-found]
        except Exception as exc:
            log.warning("api_key_rate_limit_redis_import_failed err=%s", type(exc).__name__)
            return None
        try:
            client = redis_async.from_url(
                str(self.redis_url),
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
            )
            # Lazy connect; ping once to surface bad URLs early (budgeted).
            await asyncio.wait_for(client.ping(), timeout=_REDIS_RL_CALL_TIMEOUT_S)
            self._client = client
            return client
        except Exception as exc:
            log.warning("api_key_rate_limit_redis_connect_failed err=%s", type(exc).__name__)
            return None

    async def allow(self, api_key_id: int) -> bool:
        api_key_id_i = _coerce_int(api_key_id)
        if api_key_id_i <= 0:
            return False

        capacity, refill_per_s = _token_bucket_params(rpm=self.rpm, burst=self.burst)
        if capacity <= 0.0 or refill_per_s <= 0.0:
            return True

        try:
            client = await asyncio.wait_for(self._get_client(), timeout=_REDIS_RL_CALL_TIMEOUT_S)
        except Exception as exc:
            log.warning("api_key_rate_limit_redis_client_timeout err=%s", type(exc).__name__)
            return await self._fallback.allow(api_key_id_i)
        if client is None:
            # Fail open to process-local bucket so Redis outage does not 500 public API.
            return await self._fallback.allow(api_key_id_i)

        key = f"{self.key_prefix}{api_key_id_i}"
        now_s = time.time()
        try:
            allowed = await asyncio.wait_for(
                client.eval(
                    _REDIS_TOKEN_BUCKET_LUA,
                    1,
                    key,
                    float(capacity),
                    float(refill_per_s),
                    float(now_s),
                    1.0,
                ),
                timeout=_REDIS_RL_CALL_TIMEOUT_S,
            )
            return int(allowed or 0) == 1
        except Exception as exc:
            log.warning("api_key_rate_limit_redis_eval_failed err=%s", type(exc).__name__)
            return await self._fallback.allow(api_key_id_i)

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.aclose()
        except Exception:
            try:
                await client.close()
            except Exception:
                pass


def normalize_rate_limit_backend(raw: str | None) -> str:
    v = (raw or "memory").strip().lower()
    if v in {"redis", "memory"}:
        return v
    return "memory"


def build_api_key_rate_limiter(
    *,
    rpm: int,
    burst: int,
    backend: str = "memory",
    redis_url: str = "",
) -> ApiKeyRateLimiterPort:
    """Build rate limiter. Redis only when backend=redis and redis_url set; else memory."""
    backend_norm = normalize_rate_limit_backend(backend)
    url = (redis_url or "").strip()
    if backend_norm == "redis" and url:
        return RedisApiKeyRateLimiter(rpm=int(rpm), burst=int(burst), redis_url=url, backend="redis")
    if backend_norm == "redis" and not url:
        log.warning("api_key_rate_limit_redis_url_missing fallback=memory")
    return ApiKeyRateLimiter(rpm=int(rpm), burst=int(burst), backend="memory")


def extract_api_key(headers: Mapping[str, str] | None) -> str | None:
    if not headers:
        return None
    return (headers.get("X-API-Key") or headers.get("x-api-key") or "").strip() or None


async def require_public_api_key(
    authenticator: ApiKeyAuthenticator,
    limiter: ApiKeyRateLimiterPort,
    *,
    headers: Mapping[str, str] | None,
) -> int:
    api_key = extract_api_key(headers)
    if not api_key:
        raise ApiError(code=ErrorCode.UNAUTHORIZED, message="Missing API key", status_code=401)

    api_key_id = await authenticator.authenticate(api_key)
    if api_key_id is None:
        raise ApiError(code=ErrorCode.UNAUTHORIZED, message="Invalid API key", status_code=401)

    if not await limiter.allow(api_key_id):
        try:
            from app.core.metrics import observe_api_key_rate_limit

            observe_api_key_rate_limit(result="limited", backend=str(getattr(limiter, "backend", "memory") or "memory"))
        except Exception:
            pass
        raise ApiError(code=ErrorCode.RATE_LIMITED, message="Rate limited", status_code=429)

    try:
        from app.core.metrics import observe_api_key_rate_limit

        observe_api_key_rate_limit(result="allowed", backend=str(getattr(limiter, "backend", "memory") or "memory"))
    except Exception:
        pass

    return int(api_key_id)
