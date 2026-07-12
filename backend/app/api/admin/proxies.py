from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.admin_cursor_query import parse_admin_int_cursor, slice_id_cursor_page
from app.core.admin_json import admin_cursor_list, admin_ok
from app.core.admin_request import (
    load_json_object,
    load_json_object_optional,
    parse_choice,
    parse_int_in_range,
    parse_max_tokens_per_proxy,
    parse_optional_str,
    parse_positive_int,
    parse_required_bool,
    parse_required_str,
    require_positive_id,
)
from app.core.bindings_recompute import recompute_token_proxy_bindings
from app.core.crypto import FieldEncryptor
from app.core.errors import ApiError, ErrorCode
from app.core.proxy_routing import invalidate_proxy_pool_caches
from app.core.proxy_uri import mask_proxy_uri, parse_proxy_uri
from app.core.source_ref import sanitize_source_ref
from app.core.request_id import get_or_create_request_id
from app.core.time import iso_utc_ms
from app.db.images_upsert import dialect_name_from_session, insert_for_dialect
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.models.proxy_pool_endpoints import ProxyPoolEndpoint
from app.db.models.proxy_pools import ProxyPool
from app.db.models.token_proxy_bindings import TokenProxyBinding
from app.db.session import resolve_sessionmaker, with_sqlite_busy_retry
from app.jobs.queue import resolve_job_queue
from app.easy_proxies.client import EasyProxiesError, easy_proxies_auth, easy_proxies_export
from app.easy_proxies.normalize import normalize_exported_proxy_host, resolve_export_host

router = APIRouter()


@router.get("/proxies/endpoints")
async def list_proxy_endpoints(
    request: Request,
    limit: int = 50,
    cursor: str | None = None,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    parsed = parse_admin_int_cursor(limit=limit, cursor=cursor, limit_max=500)
    limit = parsed.limit
    cursor_i = parsed.cursor_i

    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)
    async with Session() as session:
        stmt = sa.select(ProxyEndpoint).order_by(ProxyEndpoint.id.desc()).limit(int(limit) + 1)
        if cursor_i is not None:
            stmt = stmt.where(ProxyEndpoint.id < int(cursor_i))
        rows = (await session.execute(stmt)).scalars().all()
        endpoints, next_cursor_i = slice_id_cursor_page(list(rows), int(limit))

        endpoint_ids = [int(p.id) for p in endpoints]

        pools_by_eid: dict[int, list[dict[str, Any]]] = {}
        if endpoint_ids:
            pool_rows = (
                (
                    await session.execute(
                        sa.select(
                            ProxyPoolEndpoint.endpoint_id,
                            ProxyPoolEndpoint.pool_id,
                            ProxyPoolEndpoint.enabled,
                            ProxyPoolEndpoint.weight,
                            ProxyPool.name,
                            ProxyPool.enabled,
                        )
                        .join(ProxyPool, ProxyPool.id == ProxyPoolEndpoint.pool_id)
                        .where(ProxyPoolEndpoint.endpoint_id.in_(endpoint_ids))
                        .order_by(ProxyPoolEndpoint.pool_id.asc())
                    )
                )
                .all()
            )
            for endpoint_id, pool_id, member_enabled, weight, pool_name, pool_enabled in pool_rows:
                pools_by_eid.setdefault(int(endpoint_id), []).append(
                    {
                        "id": str(pool_id),
                        "name": str(pool_name),
                        "pool_enabled": bool(pool_enabled),
                        "member_enabled": bool(member_enabled),
                        "weight": int(weight or 0),
                    }
                )

        primary_counts: dict[int, int] = {}
        override_counts: dict[int, int] = {}
        if endpoint_ids:
            rows = (
                (
                    await session.execute(
                        sa.select(TokenProxyBinding.primary_proxy_id, sa.func.count())
                        .where(TokenProxyBinding.primary_proxy_id.in_(endpoint_ids))
                        .group_by(TokenProxyBinding.primary_proxy_id)
                    )
                )
                .all()
            )
            for pid, c in rows:
                primary_counts[int(pid)] = int(c)

            rows2 = (
                (
                    await session.execute(
                        sa.select(TokenProxyBinding.override_proxy_id, sa.func.count())
                        .where(TokenProxyBinding.override_proxy_id.is_not(None))
                        .where(TokenProxyBinding.override_proxy_id.in_(endpoint_ids))
                        .group_by(TokenProxyBinding.override_proxy_id)
                    )
                )
                .all()
            )
            for pid, c in rows2:
                if pid is None:
                    continue
                override_counts[int(pid)] = int(c)

    now_iso = iso_utc_ms()
    invalid_hosts = {"0.0.0.0", "127.0.0.1", "localhost", "::", "::1", "[::]", "[::1]"}

    items = [
        {
            "id": str(p.id),
            "enabled": bool(p.enabled),
            "source": str(p.source or "manual"),
            "source_ref": sanitize_source_ref(p.source_ref),
            "scheme": str(p.scheme),
            "host": str(p.host),
            "port": int(p.port),
            "invalid_host": str(p.host or "").strip().lower() in invalid_hosts,
            "uri_masked": mask_proxy_uri(
                scheme=str(p.scheme),
                host=str(p.host),
                port=int(p.port),
                username=str(p.username or ""),
                password_set=bool(str(p.password_enc or "").strip()),
            ),
            "latency_ms": float(p.last_latency_ms) if p.last_latency_ms is not None else None,
            "status": (
                "blacklisted"
                if (p.blacklisted_until and str(p.blacklisted_until) > str(now_iso))
                else "ok"
                if p.last_ok_at and (not p.last_fail_at or str(p.last_ok_at) >= str(p.last_fail_at))
                else "fail"
                if p.last_fail_at
                else "unknown"
            ),
            "blacklisted_until": p.blacklisted_until,
            "last_error": p.last_error,
            "success_count": int(p.success_count or 0),
            "failure_count": int(p.failure_count or 0),
            "last_ok_at": p.last_ok_at,
            "last_fail_at": p.last_fail_at,
            "pools": pools_by_eid.get(int(p.id), []),
            "bindings": {
                "primary_count": int(primary_counts.get(int(p.id), 0)),
                "override_count": int(override_counts.get(int(p.id), 0)),
            },
        }
        for p in endpoints
    ]

    return admin_cursor_list(request, items=items, next_cursor=next_cursor_i, request_id=rid)


def _parse_conflict_policy(value: Any) -> str:
    return parse_choice(
        value,
        field="conflict_policy",
        choices=frozenset({"skip", "overwrite"}),
        default="skip",
    )


def _parse_easy_conflict_policy(value: Any) -> str:
    return parse_choice(
        value,
        field="conflict_policy",
        choices=frozenset({"skip_non_easy_proxies", "skip", "overwrite"}),
        default="skip_non_easy_proxies",
    )



async def _load_import_json(request: Request) -> dict[str, Any]:
    data = await load_json_object(request)

    # Preserve raw text (line endings / trailing spaces); only emptiness uses strip.
    text = str(data.get("text") or "")
    if not text.strip():
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing text", status_code=400)

    source = parse_optional_str(data.get("source"), field="source") or "manual"
    conflict_policy = _parse_conflict_policy(data.get("conflict_policy"))

    return {"text": text, "source": source, "conflict_policy": conflict_policy}


async def _load_update_endpoint_json(request: Request) -> dict[str, Any]:
    data = await load_json_object(request)
    if "enabled" not in data:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing enabled", status_code=400)

    enabled = parse_required_bool(
        data.get("enabled"),
        field="enabled",
        invalid_message="Unsupported enabled",
    )
    return {"enabled": bool(enabled)}


async def _load_easy_import_json(request: Request) -> dict[str, Any]:
    data = await load_json_object(request)

    base_url = parse_required_str(data.get("base_url"), field="base_url")
    password = str(data.get("password") or "").strip()

    host_override = parse_optional_str(
        data.get("host_override"),
        max_len=200,
        field="host_override",
        invalid_message="Invalid host_override",
    )

    attach_pool_id: int | None = None
    if "attach_pool_id" in data:
        raw = data.get("attach_pool_id")
        if raw is None or (isinstance(raw, str) and not str(raw).strip()):
            attach_pool_id = None
        else:
            attach_pool_id = parse_positive_int(raw, field="attach_pool_id")

    attach_weight = 1
    if "attach_weight" in data:
        attach_weight = parse_int_in_range(
            data.get("attach_weight"),
            field="attach_weight",
            min_value=0,
            max_value=1000,
            invalid_message="Invalid attach_weight",
        )

    recompute_bindings = False
    if "recompute_bindings" in data:
        recompute_bindings = parse_required_bool(
            data.get("recompute_bindings"),
            field="recompute_bindings",
            invalid_message="Invalid recompute_bindings",
        )

    max_tokens_per_proxy = 2
    if "max_tokens_per_proxy" in data:
        max_tokens_per_proxy = parse_max_tokens_per_proxy(data.get("max_tokens_per_proxy"))

    strict = True
    if "strict" in data:
        strict = parse_required_bool(
            data.get("strict"),
            field="strict",
            invalid_message="Invalid strict",
        )

    conflict_policy = _parse_easy_conflict_policy(data.get("conflict_policy"))
    if recompute_bindings and attach_pool_id is None:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="recompute_bindings requires attach_pool_id", status_code=400)
    return {
        "base_url": base_url,
        "password": password,
        "conflict_policy": conflict_policy,
        "host_override": host_override,
        "attach_pool_id": attach_pool_id,
        "attach_weight": int(attach_weight),
        "recompute_bindings": bool(recompute_bindings),
        "max_tokens_per_proxy": int(max_tokens_per_proxy),
        "strict": bool(strict),
    }


async def _load_probe_json(request: Request) -> dict[str, Any]:
    data = await load_json_object_optional(request)
    out: dict[str, Any] = {}

    if "probe_url" in data:
        probe_url = parse_optional_str(
            data.get("probe_url"),
            max_len=2000,
            field="probe_url",
            invalid_message="Invalid probe_url",
        )
        if probe_url:
            if "://" not in probe_url:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid probe_url", status_code=400)
            out["probe_url"] = probe_url

    if "timeout_ms" in data:
        out["timeout_ms"] = parse_int_in_range(
            data.get("timeout_ms"),
            field="timeout_ms",
            min_value=1,
            max_value=600_000,
            invalid_message="Invalid timeout_ms",
        )

    if "concurrency" in data:
        out["concurrency"] = parse_int_in_range(
            data.get("concurrency"),
            field="concurrency",
            min_value=1,
            max_value=200,
            invalid_message="Invalid concurrency",
        )

    return out


async def _load_cleanup_invalid_hosts_json(request: Request) -> dict[str, Any]:
    data = await load_json_object_optional(request)
    out: dict[str, Any] = {}

    for key in ("dry_run", "delete_orphans", "recompute_bindings", "strict"):
        if key not in data:
            continue
        out[key] = parse_required_bool(
            data.get(key),
            field=key,
            invalid_message=f"Invalid {key}",
        )

    if "max_tokens_per_proxy" in data:
        out["max_tokens_per_proxy"] = parse_max_tokens_per_proxy(data.get("max_tokens_per_proxy"))

    return out


@router.post("/proxies/endpoints/import")
async def import_proxy_endpoints(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)

    body = await _load_import_json(request)

    text = str(body["text"])
    source = str(body["source"])
    conflict_policy = str(body["conflict_policy"])

    created = 0
    updated = 0
    skipped = 0
    errors: list[dict[str, Any]] = []

    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)

    settings = request.app.state.settings
    encryptor: FieldEncryptor | None = None

    async def _op() -> None:
        nonlocal created, updated, skipped, errors, encryptor
        async with Session() as session:
            for line_no, raw in enumerate(text.splitlines(), start=1):
                uri = raw.strip()
                if not uri:
                    continue
                try:
                    parsed = parse_proxy_uri(uri)
                except Exception:
                    errors.append({"line": line_no, "code": "invalid_proxy_uri", "message": "invalid_proxy_uri"})
                    continue

                username = (parsed.username or "").strip()
                password = parsed.password
                password_enc = ""
                if password:
                    if encryptor is None:
                        try:
                            encryptor = FieldEncryptor.from_key(settings.field_encryption_key)
                        except Exception:
                            errors.append(
                                {
                                    "line": line_no,
                                    "code": "encryption_not_configured",
                                    "message": "代理包含密码，但未配置加密密钥（FIELD_ENCRYPTION_KEY）",
                                }
                            )
                            continue
                    password_enc = encryptor.encrypt_text(password)

                existing = (
                    (
                        await session.execute(
                            sa.select(ProxyEndpoint).where(
                                ProxyEndpoint.scheme == parsed.scheme,
                                ProxyEndpoint.host == parsed.host,
                                ProxyEndpoint.port == int(parsed.port),
                                ProxyEndpoint.username == username,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )

                if existing is None:
                    session.add(
                        ProxyEndpoint(
                            scheme=parsed.scheme,
                            host=parsed.host,
                            port=int(parsed.port),
                            username=username,
                            password_enc=password_enc,
                            enabled=1,
                            source=source,
                            source_ref=None,
                            updated_at=now,
                        )
                    )
                    created += 1
                    continue

                if conflict_policy == "overwrite":
                    existing.password_enc = password_enc
                    existing.enabled = 1
                    existing.source = source
                    existing.updated_at = now
                    updated += 1
                else:
                    skipped += 1

            await session.commit()

    await with_sqlite_busy_retry(_op)

    invalidate_proxy_pool_caches()
    return admin_ok(request, payload={"created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:200]}, request_id=rid)


@router.put("/proxies/endpoints/{endpoint_id}")
async def update_proxy_endpoint(
    endpoint_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    endpoint_id = require_positive_id(endpoint_id, invalid_message="Invalid endpoint id")

    rid = get_or_create_request_id(request)
    body = await _load_update_endpoint_json(request)
    enabled = bool(body["enabled"])

    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)
    async with Session() as session:
        row = await session.get(ProxyEndpoint, endpoint_id)
        if row is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Proxy endpoint not found", status_code=404)

        row.enabled = 1 if enabled else 0
        row.updated_at = now
        await session.commit()

    invalidate_proxy_pool_caches()
    return admin_ok(request, payload={"endpoint_id": str(endpoint_id), "enabled": enabled}, request_id=rid)


@router.post("/proxies/endpoints/{endpoint_id}/reset-failures")
async def reset_proxy_failures(
    endpoint_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    endpoint_id = require_positive_id(endpoint_id, invalid_message="Invalid endpoint id")

    rid = get_or_create_request_id(request)
    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)

    async with Session() as session:
        row = await session.get(ProxyEndpoint, endpoint_id)
        if row is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Proxy endpoint not found", status_code=404)

        row.failure_count = 0
        row.blacklisted_until = None
        row.last_fail_at = None
        row.last_error = None
        row.updated_at = now

        await session.commit()

    invalidate_proxy_pool_caches()
    return admin_ok(request, payload={"endpoint_id": str(endpoint_id)}, request_id=rid)


@router.post("/proxies/endpoints/cleanup-invalid-hosts")
async def cleanup_invalid_hosts(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)

    body = await _load_cleanup_invalid_hosts_json(request)
    dry_run = bool(body.get("dry_run", False))
    delete_orphans = bool(body.get("delete_orphans", False))
    recompute_bindings = bool(body.get("recompute_bindings", True))
    max_tokens_per_proxy = int(body.get("max_tokens_per_proxy", 2))
    strict = bool(body.get("strict", False))

    invalid_hosts = {"0.0.0.0", "127.0.0.1", "localhost", "::", "::1", "[::]", "[::1]"}

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)
    now = iso_utc_ms()

    async with Session() as session:
        endpoint_rows = (
            (
                await session.execute(
                    sa.select(ProxyEndpoint.id, ProxyEndpoint.host)
                    .where(sa.func.lower(sa.func.trim(ProxyEndpoint.host)).in_(sorted(invalid_hosts)))
                    .order_by(ProxyEndpoint.id.asc())
                )
            )
            .all()
        )
        endpoint_ids = [int(r[0]) for r in endpoint_rows]

        affected_pool_ids: list[int] = []
        if endpoint_ids:
            pool_rows = (
                (
                    await session.execute(
                        sa.select(sa.distinct(ProxyPoolEndpoint.pool_id))
                        .where(ProxyPoolEndpoint.endpoint_id.in_(endpoint_ids))
                        .order_by(ProxyPoolEndpoint.pool_id.asc())
                    )
                )
                .all()
            )
            affected_pool_ids = [int(r[0]) for r in pool_rows]

        if dry_run:
            return admin_ok(request, payload={"dry_run": True,
                "invalid_hosts": sorted(invalid_hosts),
                "matched": len(endpoint_ids),
                "endpoint_ids": [str(x) for x in endpoint_ids[:200]],
                "affected_pool_ids": [str(x) for x in affected_pool_ids]}, request_id=rid)

        disabled = 0
        memberships_removed = 0
        overrides_cleared = 0
        deleted = 0
        binding_results: list[dict[str, Any]] = []
        warnings: list[str] = []

        if endpoint_ids:
            disabled = int(
                (
                    await session.execute(
                        sa.update(ProxyEndpoint)
                        .where(ProxyEndpoint.id.in_(endpoint_ids))
                        .values(enabled=0, updated_at=now)
                    )
                ).rowcount
                or 0
            )

            memberships_removed = int(
                (
                    await session.execute(
                        sa.delete(ProxyPoolEndpoint).where(ProxyPoolEndpoint.endpoint_id.in_(endpoint_ids))
                    )
                ).rowcount
                or 0
            )

            overrides_cleared = int(
                (
                    await session.execute(
                        sa.update(TokenProxyBinding)
                        .where(TokenProxyBinding.override_proxy_id.is_not(None))
                        .where(TokenProxyBinding.override_proxy_id.in_(endpoint_ids))
                        .values(override_proxy_id=None, override_expires_at=None, updated_at=now)
                    )
                ).rowcount
                or 0
            )

        if recompute_bindings and affected_pool_ids:
            for pid in affected_pool_ids:
                try:
                    result = await recompute_token_proxy_bindings(
                        session,
                        pool_id=int(pid),
                        now=now,
                        max_tokens_per_proxy=int(max_tokens_per_proxy),
                        strict=bool(strict),
                    )
                except ApiError as exc:
                    warnings.append(f"pool#{pid} 重算绑定失败: {exc.message}")
                    continue
                binding_results.append({"pool_id": str(pid), **result})

        if delete_orphans and endpoint_ids:
            refs_primary = (
                (
                    await session.execute(
                        sa.select(sa.distinct(TokenProxyBinding.primary_proxy_id)).where(
                            TokenProxyBinding.primary_proxy_id.in_(endpoint_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            refs_override = (
                (
                    await session.execute(
                        sa.select(sa.distinct(TokenProxyBinding.override_proxy_id))
                        .where(TokenProxyBinding.override_proxy_id.is_not(None))
                        .where(TokenProxyBinding.override_proxy_id.in_(endpoint_ids))
                    )
                )
                .scalars()
                .all()
            )
            referenced: set[int] = {int(x) for x in refs_primary if x is not None} | {int(x) for x in refs_override if x is not None}
            deletable = [int(eid) for eid in endpoint_ids if int(eid) not in referenced]
            if deletable:
                deleted = int(
                    (await session.execute(sa.delete(ProxyEndpoint).where(ProxyEndpoint.id.in_(deletable)))).rowcount or 0
                )

        await session.commit()

    invalidate_proxy_pool_caches()
    return admin_ok(request, payload={"dry_run": False,
        "invalid_hosts": sorted(invalid_hosts),
        "matched": len(endpoint_ids),
        "disabled": int(disabled),
        "memberships_removed": int(memberships_removed),
        "overrides_cleared": int(overrides_cleared),
        "deleted": int(deleted),
        "affected_pool_ids": [str(x) for x in affected_pool_ids],
        "bindings": binding_results,
        "warnings": warnings}, request_id=rid)


@router.post(
    "/proxies/easy-proxies/import",
    summary="Import easy-proxies endpoints",
    description=(
        "Fetch EasyProxies export and upsert ProxyEndpoint rows; optional pool attach uses "
        "dialect-aware `insert_for_dialect` ON CONFLICT for ProxyPoolEndpoint. "
        "Optional `recompute_bindings` rebuilds TokenProxyBinding for the pool. "
        "Inline control-plane import (not a JobQueuePort job) — worker path has a separate handler."
    ),
)
async def import_easy_proxies(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    body = await _load_easy_import_json(request)

    base_url_raw = str(body["base_url"])
    base_url = sanitize_source_ref(base_url_raw) or base_url_raw
    password = str(body["password"])
    conflict_policy = str(body["conflict_policy"])
    host_override = body.get("host_override")
    attach_pool_id = body.get("attach_pool_id")
    attach_weight = int(body.get("attach_weight", 1))
    recompute_bindings = bool(body.get("recompute_bindings", False))
    max_tokens_per_proxy = int(body.get("max_tokens_per_proxy", 2))
    strict = bool(body.get("strict", True))

    export_host = resolve_export_host(base_url=base_url_raw, host_override=host_override)

    transport = getattr(request.app.state, "httpx_transport", None)

    try:
        bearer_token: str | None = None
        if password.strip():
            auth = await easy_proxies_auth(base_url=base_url_raw, password=password, transport=transport)
            bearer_token = auth.token
        uris = await easy_proxies_export(base_url=base_url_raw, bearer_token=bearer_token, transport=transport)
    except EasyProxiesError as exc:
        raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="easy_proxies import failed", status_code=502) from exc
    except Exception as exc:
        raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="easy_proxies import failed", status_code=502) from exc

    warnings: list[str] = []
    raw_count = len(uris)
    seen_keys: set[tuple[str, str, int, str]] = set()
    deduped: list[str] = []
    invalid_uris: list[str] = []
    duplicates = 0
    invalid = 0
    placeholder_hosts = 0
    rewritten_hosts = 0
    for raw in uris:
        uri = (raw or "").strip()
        if not uri:
            continue
        try:
            parsed = parse_proxy_uri(uri)
        except Exception:
            invalid += 1
            invalid_uris.append(uri)
            continue

        desired_host, rewrote = normalize_exported_proxy_host(exported_host=str(parsed.host), export_host=export_host)
        if str(parsed.host or "").strip().lower() in {"0.0.0.0", "127.0.0.1", "localhost", "::", "::1"}:
            placeholder_hosts += 1
        if rewrote:
            rewritten_hosts += 1

        key = (str(parsed.scheme), str(desired_host), int(parsed.port), str((parsed.username or "").strip()))
        if key in seen_keys:
            duplicates += 1
            continue
        seen_keys.add(key)
        deduped.append(uri)

    uris = deduped
    if duplicates > 0:
        warnings.append(f"检测到导出结果包含重复入口（已去重 {duplicates} 条）。")
    if placeholder_hosts > 0 and rewritten_hosts <= 0:
        warnings.append("检测到 easy_proxies 导出 host 为 0.0.0.0/127.0.0.1/localhost 等占位符，但无法确定可用的替换 host；请检查 base_url 或使用 host_override。")
    if rewritten_hosts > 0 and export_host:
        warnings.append(f"检测到 easy_proxies 导出 host 为占位符，已自动替换为 {export_host}。")
    if raw_count > 1 and len(deduped) <= 1:
        warnings.append("当前 easy_proxies 可能处于 pool 模式（所有节点共享同一入口端口），导入后只会得到一个入口。若要每节点独立端口，请在 easy_proxies 启用 multi-port 或 hybrid 模式后再导入。")
    if invalid > 0:
        warnings.append(f"有 {invalid} 条导出内容不是合法代理 URI，已跳过。")

    created = 0
    updated = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    attach_created = 0
    attach_updated = 0
    attach_total = 0
    binding_result: dict[str, Any] | None = None

    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)

    settings = request.app.state.settings
    encryptor: FieldEncryptor | None = None

    async def _op() -> None:
        nonlocal created, updated, skipped, errors, encryptor, attach_created, attach_updated, attach_total, binding_result
        async with Session() as session:
            for uri in deduped + invalid_uris:
                try:
                    parsed = parse_proxy_uri(uri)
                except Exception:
                    errors.append({"code": "invalid_proxy_uri", "message": "invalid_proxy_uri"})
                    continue

                username = (parsed.username or "").strip()
                password_v = parsed.password
                password_enc = ""
                if password_v:
                    if encryptor is None:
                        try:
                            encryptor = FieldEncryptor.from_key(settings.field_encryption_key)
                        except Exception:
                            errors.append(
                                {
                                    "code": "encryption_not_configured",
                                    "message": "代理包含密码，但未配置加密密钥（FIELD_ENCRYPTION_KEY）",
                                }
                            )
                            continue
                    password_enc = encryptor.encrypt_text(password_v)

                desired_host, rewrote = normalize_exported_proxy_host(
                    exported_host=str(parsed.host),
                    export_host=export_host,
                )
                original_host = str(parsed.host)

                existing = (
                    (
                        await session.execute(
                            sa.select(ProxyEndpoint).where(
                                ProxyEndpoint.scheme == parsed.scheme,
                                ProxyEndpoint.host == desired_host,
                                ProxyEndpoint.port == int(parsed.port),
                                ProxyEndpoint.username == username,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )

                moved_from_placeholder = False
                if existing is None and rewrote and desired_host and desired_host != original_host:
                    placeholder = (
                        (
                            await session.execute(
                                sa.select(ProxyEndpoint).where(
                                    ProxyEndpoint.scheme == parsed.scheme,
                                    ProxyEndpoint.host == original_host,
                                    ProxyEndpoint.port == int(parsed.port),
                                    ProxyEndpoint.username == username,
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if placeholder is not None:
                        can_update_placeholder = conflict_policy == "overwrite" or (
                            conflict_policy == "skip_non_easy_proxies" and (placeholder.source or "") == "easy_proxies"
                        )
                        if can_update_placeholder:
                            existing = placeholder
                            existing.host = desired_host
                            moved_from_placeholder = True

                if existing is None:
                    session.add(
                        ProxyEndpoint(
                            scheme=parsed.scheme,
                            host=desired_host or original_host,
                            port=int(parsed.port),
                            username=username,
                            password_enc=password_enc,
                            enabled=1,
                            source="easy_proxies",
                            source_ref=base_url,
                            updated_at=now,
                        )
                    )
                    created += 1
                    continue

                if conflict_policy == "skip_non_easy_proxies" and (existing.source or "") != "easy_proxies":
                    if moved_from_placeholder:
                        # can't update, but the desired host identity is new; create a new easy_proxies entry
                        session.add(
                            ProxyEndpoint(
                                scheme=parsed.scheme,
                                host=desired_host or original_host,
                                port=int(parsed.port),
                                username=username,
                                password_enc=password_enc,
                                enabled=1,
                                source="easy_proxies",
                                source_ref=base_url,
                                updated_at=now,
                            )
                        )
                        created += 1
                    else:
                        skipped += 1
                    continue
                if conflict_policy == "skip":
                    skipped += 1
                    continue

                existing.password_enc = password_enc
                existing.enabled = 1
                existing.source = "easy_proxies"
                existing.source_ref = base_url
                # easy-proxies export only contains currently-healthy endpoints; clear local blacklist so they can be used immediately.
                existing.blacklisted_until = None
                existing.last_error = None
                existing.updated_at = now
                updated += 1

            if attach_pool_id is not None:
                pool = await session.get(ProxyPool, int(attach_pool_id))
                if pool is None:
                    raise ApiError(code=ErrorCode.NOT_FOUND, message="Proxy pool not found", status_code=404)

                endpoint_ids = (
                    (
                        await session.execute(
                            sa.select(ProxyEndpoint.id)
                            .where(ProxyEndpoint.source == "easy_proxies")
                            .where(ProxyEndpoint.source_ref == base_url)
                            .where(ProxyEndpoint.enabled == 1)
                            .order_by(ProxyEndpoint.id.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
                attach_total = len(endpoint_ids)
                if endpoint_ids:
                    existing_members = (
                        (
                            await session.execute(
                                sa.select(ProxyPoolEndpoint.endpoint_id)
                                .where(ProxyPoolEndpoint.pool_id == int(attach_pool_id))
                                .where(ProxyPoolEndpoint.endpoint_id.in_([int(x) for x in endpoint_ids]))
                            )
                        )
                        .scalars()
                        .all()
                    )
                    existing_set = {int(x) for x in existing_members}
                    attach_created = len([x for x in endpoint_ids if int(x) not in existing_set])
                    attach_updated = len([x for x in endpoint_ids if int(x) in existing_set])

                    dialect = dialect_name_from_session(session)
                    insert = insert_for_dialect(ProxyPoolEndpoint, dialect_name=dialect)
                    for endpoint_id in endpoint_ids:
                        stmt = insert.values(
                            pool_id=int(attach_pool_id),
                            endpoint_id=int(endpoint_id),
                            enabled=1,
                            weight=int(attach_weight),
                            updated_at=now,
                        )
                        stmt = stmt.on_conflict_do_update(
                            index_elements=[ProxyPoolEndpoint.pool_id, ProxyPoolEndpoint.endpoint_id],
                            set_={"enabled": 1, "weight": int(attach_weight), "updated_at": now},
                        )
                        await session.execute(stmt)

                if recompute_bindings:
                    binding_result = await recompute_token_proxy_bindings(
                        session,
                        pool_id=int(attach_pool_id),
                        now=str(now),
                        max_tokens_per_proxy=int(max_tokens_per_proxy),
                        strict=bool(strict),
                    )

            await session.commit()

    await with_sqlite_busy_retry(_op)

    invalidate_proxy_pool_caches()
    payload: dict[str, Any] = {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:200],
        "warnings": warnings[:50],
    }
    if attach_pool_id is not None:
        payload["attach"] = {
            "pool_id": str(attach_pool_id),
            "endpoints_total": int(attach_total),
            "created": int(attach_created),
            "updated": int(attach_updated),
        }
    if binding_result is not None:
        payload["bindings"] = {"pool_id": str(attach_pool_id), **binding_result}
    return admin_ok(request, payload=payload, request_id=rid)


@router.post(
    "/proxies/probe",
    summary="Enqueue proxy probe job",
    description=(
        "Enqueue a `proxy_probe` job via JobQueuePort (`resolve_job_queue` → `queue.enqueue`). "
        "Payload/status land on the SQLite `jobs` table; worker claim uses the active queue backend "
        "(sqlite/memory today; redis/nats fail-loud). Returns `job_id` only — does not run the probe inline."
    ),
)
async def probe_proxies(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    opts = await _load_probe_json(request)

    engine = request.app.state.engine
    queue = resolve_job_queue(getattr(request.app.state, "job_queue", None), engine)
    job_id = await queue.enqueue(
        type="proxy_probe",
        payload_json=json.dumps({"scope": "all", **opts}, ensure_ascii=False),
        ref_type="proxy_probe",
        ref_id="all",
    )

    return admin_ok(request, payload={"job_id": str(job_id)}, request_id=rid)
