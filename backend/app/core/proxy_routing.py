from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.crypto import FieldEncryptor
from app.core.errors import ApiError, ErrorCode
from app.core.runtime_settings import RuntimeConfig
from app.core.time import iso_utc_ms
from app.db.session import with_sqlite_busy_retry


_PIXIV_HOST_SUFFIXES = (
    "pixiv.net",
    "pximg.net",
    "secure.pixiv.net",
)


def _normalize_host(host: str) -> str:
    return (host or "").strip().lower().strip(".")


def host_from_url(url: str) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = _normalize_host(parsed.hostname or "")
    return host or None


def _suffix_match(*, host: str, suffix: str) -> bool:
    host = _normalize_host(host)
    suffix = _normalize_host(suffix)
    if not host or not suffix:
        return False
    return host == suffix or host.endswith("." + suffix)


def should_use_proxy_for_host(runtime: RuntimeConfig, *, host: str) -> bool:
    if not bool(runtime.proxy_enabled):
        return False

    host_n = _normalize_host(host)
    if not host_n:
        return False

    mode = (runtime.proxy_route_mode or "").strip().lower()
    if mode in {"off"}:
        return False
    if mode == "all":
        return True
    if mode == "allowlist":
        return any(_suffix_match(host=host_n, suffix=d) for d in (runtime.proxy_allowlist_domains or []))
    if mode == "pixiv_only":
        return any(_suffix_match(host=host_n, suffix=s) for s in _PIXIV_HOST_SUFFIXES)
    return False


def resolve_pool_id_for_host(runtime: RuntimeConfig, *, host: str) -> int | None:
    host_n = _normalize_host(host)
    if not host_n:
        return None

    best_len = -1
    best: int | None = None
    for suffix, pool_id in (runtime.proxy_route_pools or {}).items():
        suf = _normalize_host(str(suffix))
        if not suf:
            continue
        if not _suffix_match(host=host_n, suffix=suf):
            continue
        if len(suf) > best_len:
            best_len = len(suf)
            best = int(pool_id)

    if best is not None and int(best) > 0:
        return int(best)

    if runtime.proxy_default_pool_id is not None and int(runtime.proxy_default_pool_id) > 0:
        return int(runtime.proxy_default_pool_id)

    return None


def _weighted_choice(items: list[tuple[int, int]]) -> int | None:
    if not items:
        return None
    total = sum(max(0, int(w)) for _pid, w in items)
    if total <= 0:
        items2 = [pid for pid, _w in items]
        return int(random.choice(items2)) if items2 else None
    r = random.random() * total
    for pid, w in items:
        w_i = max(0, int(w))
        if w_i <= 0:
            continue
        if r < w_i:
            return int(pid)
        r -= w_i
    return int(items[-1][0])


@dataclass(frozen=True, slots=True)
class ProxyUri:
    uri: str
    endpoint_id: int
    pool_id: int


async def _first_enabled_pool_id(engine: AsyncEngine) -> int | None:
    sql = "SELECT id FROM proxy_pools WHERE enabled=1 ORDER BY id ASC LIMIT 1;"

    async def _op() -> int | None:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(sql)
            value = result.scalar_one_or_none()
            return int(value) if value is not None else None

    return await with_sqlite_busy_retry(_op)


async def _list_enabled_pool_ids(engine: AsyncEngine) -> list[int]:
    sql = "SELECT id FROM proxy_pools WHERE enabled=1 ORDER BY id ASC;"

    async def _op() -> list[int]:
        async with engine.connect() as conn:
            rows = (await conn.exec_driver_sql(sql)).fetchall()
        out: list[int] = []
        seen: set[int] = set()
        for (pid,) in rows:
            try:
                pool_id = int(pid)
            except Exception:
                continue
            if pool_id <= 0 or pool_id in seen:
                continue
            seen.add(pool_id)
            out.append(pool_id)
        return out

    return await with_sqlite_busy_retry(_op)


async def _pool_health_stats(engine: AsyncEngine, *, pool_id: int, now_iso: str) -> dict[str, Any]:
    sql = """
SELECT
  COUNT(*) AS endpoints_total,
  COALESCE(SUM(CASE
    WHEN pe.blacklisted_until IS NULL OR pe.blacklisted_until <= :now THEN 1
    ELSE 0
  END), 0) AS endpoints_eligible,
  MIN(CASE
    WHEN pe.blacklisted_until > :now THEN pe.blacklisted_until
    ELSE NULL
  END) AS next_available_at
FROM proxy_pools pp
JOIN proxy_pool_endpoints ppe
  ON ppe.pool_id = pp.id AND ppe.enabled = 1
JOIN proxy_endpoints pe
  ON pe.id = ppe.endpoint_id AND pe.enabled = 1
WHERE pp.id = :pool_id AND pp.enabled = 1;
""".strip()

    async def _op() -> dict[str, Any]:
        async with engine.connect() as conn:
            row = (await conn.exec_driver_sql(sql, {"pool_id": int(pool_id), "now": str(now_iso)})).first()
        if row is None:
            return {"endpoints_total": 0, "endpoints_eligible": 0, "next_available_at": None}
        total = int(row[0] or 0)
        eligible = int(row[1] or 0)
        next_available_at = str(row[2]).strip() if row[2] is not None else None
        return {
            "endpoints_total": max(0, total),
            "endpoints_eligible": max(0, eligible),
            "next_available_at": next_available_at or None,
        }

    return await with_sqlite_busy_retry(_op)


async def _pick_endpoint_in_pool(engine: AsyncEngine, *, pool_id: int, now_iso: str) -> tuple[int, str, str, int, str, str] | None:
    sql = """
SELECT pe.id, pe.scheme, pe.host, pe.port, pe.username, pe.password_enc, ppe.weight, pe.last_ok_at, pe.last_fail_at
FROM proxy_pools pp
JOIN proxy_pool_endpoints ppe
  ON ppe.pool_id = pp.id AND ppe.enabled = 1
JOIN proxy_endpoints pe
  ON pe.id = ppe.endpoint_id AND pe.enabled = 1
WHERE pp.id = :pool_id AND pp.enabled = 1
  AND (pe.blacklisted_until IS NULL OR pe.blacklisted_until <= :now)
ORDER BY pe.id ASC;
""".strip()

    async def _op() -> tuple[int, str, str, int, str, str] | None:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(sql, {"pool_id": int(pool_id), "now": now_iso})
            rows = result.fetchall()
        ok_weighted: list[tuple[int, int]] = []
        unknown_weighted: list[tuple[int, int]] = []
        fail_weighted: list[tuple[int, int]] = []

        for r in rows:
            endpoint_id = int(r[0])
            weight = int(r[6] or 0)
            last_ok_at = str(r[7]).strip() if r[7] is not None else ""
            last_fail_at = str(r[8]).strip() if r[8] is not None else ""

            if last_ok_at and (not last_fail_at or last_ok_at >= last_fail_at):
                ok_weighted.append((endpoint_id, weight))
            elif not last_ok_at and not last_fail_at:
                unknown_weighted.append((endpoint_id, weight))
            else:
                fail_weighted.append((endpoint_id, weight))

        weighted = ok_weighted or unknown_weighted or fail_weighted
        chosen_id = _weighted_choice(weighted)
        if chosen_id is None:
            return None
        for r in rows:
            if int(r[0]) == int(chosen_id):
                return (
                    int(r[0]),
                    str(r[1]),
                    str(r[2]),
                    int(r[3]),
                    str(r[4] or ""),
                    str(r[5] or ""),
                )
        return None

    return await with_sqlite_busy_retry(_op)


async def _load_token_binding(
    engine: AsyncEngine,
    *,
    token_id: int,
    pool_id: int,
) -> tuple[int, int | None, str | None] | None:
    sql = """
SELECT primary_proxy_id, override_proxy_id, override_expires_at
FROM token_proxy_bindings
WHERE token_id=:token_id AND pool_id=:pool_id
LIMIT 1;
""".strip()

    async def _op() -> tuple[int, int | None, str | None] | None:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(sql, {"token_id": int(token_id), "pool_id": int(pool_id)})
            row = result.first()
            if row is None:
                return None
            primary_id = int(row[0])
            override_id = int(row[1]) if row[1] is not None else None
            override_expires_at = str(row[2]) if row[2] is not None else None
            return primary_id, override_id, override_expires_at

    return await with_sqlite_busy_retry(_op)


async def _load_endpoint_in_pool(
    engine: AsyncEngine,
    *,
    pool_id: int,
    endpoint_id: int,
    now_iso: str,
) -> tuple[int, str, str, int, str, str] | None:
    sql = """
SELECT pe.id, pe.scheme, pe.host, pe.port, pe.username, pe.password_enc
FROM proxy_pools pp
JOIN proxy_pool_endpoints ppe
  ON ppe.pool_id = pp.id AND ppe.enabled = 1
JOIN proxy_endpoints pe
  ON pe.id = ppe.endpoint_id AND pe.enabled = 1
WHERE pp.id = :pool_id AND pp.enabled = 1
  AND pe.id = :endpoint_id
  AND (pe.blacklisted_until IS NULL OR pe.blacklisted_until <= :now)
LIMIT 1;
""".strip()

    async def _op() -> tuple[int, str, str, int, str, str] | None:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(
                sql,
                {"pool_id": int(pool_id), "endpoint_id": int(endpoint_id), "now": now_iso},
            )
            row = result.first()
            if row is None:
                return None
            return (
                int(row[0]),
                str(row[1]),
                str(row[2]),
                int(row[3]),
                str(row[4] or ""),
                str(row[5] or ""),
            )

    return await with_sqlite_busy_retry(_op)


def _build_proxy_uri(
    encryptor: FieldEncryptor | None,
    *,
    scheme: str,
    host: str,
    port: int,
    username: str,
    password_enc: str,
) -> str:
    scheme = (scheme or "").strip().lower()
    host = (host or "").strip()
    username = (username or "").strip()
    password_enc = (password_enc or "").strip()
    if not scheme or not host or int(port) <= 0:
        raise ValueError("invalid proxy endpoint")

    password = ""
    if password_enc:
        if encryptor is None:
            raise ValueError("FIELD_ENCRYPTION_KEY is required to decrypt proxy password")
        password = encryptor.decrypt_text(password_enc) if password_enc else ""

    host_part = host
    if ":" in host_part and not host_part.startswith("["):
        host_part = f"[{host_part}]"

    auth = ""
    if username:
        user_q = quote(username, safe="")
        pass_q = quote(password or "", safe="")
        auth = f"{user_q}:{pass_q}@"

    return f"{scheme}://{auth}{host_part}:{int(port)}"


def _proxy_uri_from_endpoint_row(
    settings: Settings,
    *,
    endpoint_id: int,
    pool_id: int,
    scheme: str,
    host: str,
    port: int,
    username: str,
    password_enc: str,
) -> ProxyUri:
    encryptor = None
    if str(password_enc or "").strip():
        try:
            encryptor = FieldEncryptor.from_key(settings.field_encryption_key)
        except Exception as exc:
            raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="Encryption not configured", status_code=500) from exc

    try:
        uri = _build_proxy_uri(
            encryptor,
            scheme=scheme,
            host=host,
            port=int(port),
            username=username,
            password_enc=password_enc,
        )
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="Invalid proxy endpoint", status_code=500) from exc

    return ProxyUri(uri=uri, endpoint_id=int(endpoint_id), pool_id=int(pool_id))


async def select_proxy_uri_for_url(
    engine: AsyncEngine,
    settings: Settings,
    runtime: RuntimeConfig,
    *,
    url: str,
    now_iso: str | None = None,
    token_id: int | None = None,
) -> ProxyUri | None:
    host = host_from_url(url)
    if host is None:
        return None

    if not should_use_proxy_for_host(runtime, host=host):
        return None

    now_iso = now_iso or iso_utc_ms()

    preferred_pool_id = resolve_pool_id_for_host(runtime, host=host)
    enabled_pools = await _list_enabled_pool_ids(engine)
    enabled_pool_set = {int(pid) for pid in enabled_pools}

    pool_candidates: list[int] = []
    if preferred_pool_id is not None and int(preferred_pool_id) > 0:
        preferred_pool_id_i = int(preferred_pool_id)
        if preferred_pool_id_i in enabled_pool_set:
            pool_candidates.append(preferred_pool_id_i)

    for pid in enabled_pools:
        if pid not in pool_candidates:
            pool_candidates.append(int(pid))

    if not pool_candidates:
        if bool(runtime.proxy_fail_closed):
            raise ApiError(
                code=ErrorCode.PROXY_REQUIRED,
                message="需要代理，但未配置代理池",
                status_code=502,
                details={
                    "reason": "no_proxy_pool_configured",
                    "host": host,
                    "url": url,
                },
            )
        return None

    async def _select_in_pool(pool_id: int) -> ProxyUri | None:
        if int(pool_id) <= 0:
            return None

        if token_id is not None and int(token_id) > 0:
            binding = await _load_token_binding(engine, token_id=int(token_id), pool_id=int(pool_id))
            if binding is not None:
                primary_proxy_id, override_proxy_id, override_expires_at = binding
                override_active = bool(
                    override_proxy_id is not None
                    and override_expires_at
                    and str(override_expires_at) > str(now_iso)
                )

                candidates: list[int] = []
                if override_active and override_proxy_id is not None:
                    candidates.append(int(override_proxy_id))
                candidates.append(int(primary_proxy_id))

                for endpoint_id in candidates:
                    picked_by_binding = await _load_endpoint_in_pool(
                        engine,
                        pool_id=int(pool_id),
                        endpoint_id=int(endpoint_id),
                        now_iso=str(now_iso),
                    )
                    if picked_by_binding is None:
                        continue

                    eid, scheme, host_v, port, username, password_enc = picked_by_binding
                    return _proxy_uri_from_endpoint_row(
                        settings,
                        endpoint_id=int(eid),
                        pool_id=int(pool_id),
                        scheme=scheme,
                        host=host_v,
                        port=int(port),
                        username=username,
                        password_enc=password_enc,
                    )

        picked = await _pick_endpoint_in_pool(engine, pool_id=int(pool_id), now_iso=now_iso)
        if picked is None:
            return None

        endpoint_id, scheme, host_v, port, username, password_enc = picked
        return _proxy_uri_from_endpoint_row(
            settings,
            endpoint_id=int(endpoint_id),
            pool_id=int(pool_id),
            scheme=scheme,
            host=host_v,
            port=int(port),
            username=username,
            password_enc=password_enc,
        )

    for candidate_pool_id in pool_candidates:
        picked = await _select_in_pool(int(candidate_pool_id))
        if picked is not None:
            return picked

    if bool(runtime.proxy_fail_closed):
        pool_stats: list[dict[str, Any]] = []
        next_available_at: str | None = None
        total_any = 0
        eligible_any = 0
        any_next = False
        for pid in pool_candidates:
            stats = await _pool_health_stats(engine, pool_id=int(pid), now_iso=str(now_iso))
            total = int(stats.get("endpoints_total") or 0)
            eligible = int(stats.get("endpoints_eligible") or 0)
            total_any += max(0, total)
            eligible_any += max(0, eligible)
            nxt = stats.get("next_available_at")
            if isinstance(nxt, str) and nxt.strip():
                any_next = True
                if next_available_at is None or str(nxt) < str(next_available_at):
                    next_available_at = str(nxt)
            pool_stats.append({"pool_id": int(pid), **stats})

        reason = "no_healthy_proxy_available"
        if total_any <= 0:
            reason = "pool_has_no_endpoints"
        elif eligible_any <= 0 and any_next and next_available_at:
            reason = "all_endpoints_blacklisted"

        primary_pool_id = (
            int(preferred_pool_id)
            if preferred_pool_id is not None and int(preferred_pool_id) > 0
            else int(pool_candidates[0])
        )

        raise ApiError(
            code=ErrorCode.PROXY_REQUIRED,
            message="需要代理，但当前没有可用代理节点",
            status_code=502,
            details={
                "reason": reason,
                "host": host,
                "url": url,
                "pool_id": primary_pool_id,
                "attempted_pool_ids": [int(pid) for pid in pool_candidates],
                "pools": pool_stats,
                "endpoints_total": int(total_any),
                "endpoints_eligible": int(eligible_any),
                "next_available_at": next_available_at,
            },
        )

    return None
