from __future__ import annotations

import os
import json
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine
from urllib.parse import urlparse, urlunparse

from app.core.bindings_recompute import recompute_token_proxy_bindings
from app.core.config import load_settings
from app.core.crypto import FieldEncryptor
from app.core.proxy_uri import parse_proxy_uri
from app.core.time import iso_utc_ms
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.models.proxy_pool_endpoints import ProxyPoolEndpoint
from app.db.models.proxy_pools import ProxyPool
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.easy_proxies.client import EasyProxiesError, easy_proxies_auth, easy_proxies_export
from app.easy_proxies.normalize import normalize_exported_proxy_host, resolve_export_host
from app.jobs.errors import JobPermanentError


def _parse_payload(payload_json: str) -> dict[str, Any]:
    try:
        data = json.loads(payload_json)
    except Exception as exc:
        raise JobPermanentError("payload_json is not valid JSON") from exc
    if not isinstance(data, dict):
        raise JobPermanentError("payload_json must be an object")
    return data


def _parse_conflict_policy(value: Any) -> str:
    v = str(value or "").strip().lower() or "skip_non_easy_proxies"
    if v not in {"skip_non_easy_proxies", "skip", "overwrite"}:
        raise JobPermanentError("payload.conflict_policy invalid")
    return v


def _sanitize_source_ref(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    try:
        parsed = urlparse(raw)
    except Exception:
        return raw[:200]

    if not parsed.scheme or not parsed.netloc:
        return raw[:200]

    host = parsed.hostname
    if not host:
        return raw[:200]

    port = parsed.port
    netloc = f"{host}:{int(port)}" if port else host
    path = parsed.path or ""

    return urlunparse((parsed.scheme, netloc, path, "", "", ""))


def build_easy_proxies_import_handler(engine: AsyncEngine, *, transport: httpx.BaseTransport | None = None) -> Any:
    settings = load_settings()
    encryptor = FieldEncryptor.from_key(settings.field_encryption_key)
    Session = create_sessionmaker(engine)

    async def _handler(job: dict[str, Any]) -> None:
        payload_json = str(job.get("payload_json") or "")
        payload = _parse_payload(payload_json)

        base_url = str(payload.get("base_url") or "").strip()
        if not base_url:
            raise JobPermanentError("payload.base_url is required")

        password = str(payload.get("password") or "").strip()
        if not password:
            password = str(os.environ.get("EASY_PROXIES_PASSWORD") or "").strip()
        conflict_policy = _parse_conflict_policy(payload.get("conflict_policy"))

        host_override = str(payload.get("host_override") or "").strip() or None

        attach_pool_id_raw = payload.get("attach_pool_id")
        attach_pool_id: int | None = None
        if attach_pool_id_raw is not None and str(attach_pool_id_raw).strip():
            try:
                attach_pool_id = int(attach_pool_id_raw)
            except Exception as exc:
                raise JobPermanentError("payload.attach_pool_id invalid") from exc
            if attach_pool_id <= 0:
                raise JobPermanentError("payload.attach_pool_id invalid")

        attach_weight_raw = payload.get("attach_weight", 1)
        try:
            attach_weight = int(attach_weight_raw)
        except Exception as exc:
            raise JobPermanentError("payload.attach_weight invalid") from exc
        if attach_weight < 0 or attach_weight > 1000:
            raise JobPermanentError("payload.attach_weight invalid")

        recompute_bindings = bool(payload.get("recompute_bindings") or False)
        max_tokens_per_proxy_raw = payload.get("max_tokens_per_proxy", 2)
        try:
            max_tokens_per_proxy = int(max_tokens_per_proxy_raw)
        except Exception as exc:
            raise JobPermanentError("payload.max_tokens_per_proxy invalid") from exc
        if max_tokens_per_proxy <= 0 or max_tokens_per_proxy > 1000:
            raise JobPermanentError("payload.max_tokens_per_proxy invalid")

        strict = bool(payload.get("strict", True))
        if recompute_bindings and attach_pool_id is None:
            raise JobPermanentError("payload.recompute_bindings requires attach_pool_id")

        base_url_ref = _sanitize_source_ref(base_url) or base_url
        export_host = resolve_export_host(base_url=base_url, host_override=host_override)

        try:
            bearer_token: str | None = None
            if password:
                auth = await easy_proxies_auth(base_url=base_url, password=password, transport=transport)
                bearer_token = auth.token
            uris = await easy_proxies_export(base_url=base_url, bearer_token=bearer_token, transport=transport)
        except EasyProxiesError as exc:
            raise RuntimeError("easy_proxies import failed") from exc
        except Exception as exc:
            raise RuntimeError("easy_proxies import failed") from exc

        seen_keys: set[tuple[str, str, int, str]] = set()
        deduped: list[str] = []
        for raw in uris:
            uri = (raw or "").strip()
            if not uri:
                continue
            try:
                parsed = parse_proxy_uri(uri)
            except Exception:
                continue
            desired_host, _rewrote = normalize_exported_proxy_host(exported_host=str(parsed.host), export_host=export_host)
            key = (str(parsed.scheme), str(desired_host), int(parsed.port), str((parsed.username or "").strip()))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(uri)
        uris = deduped

        now = iso_utc_ms()

        async def _op() -> None:
            async with Session() as session:
                for uri in uris:
                    uri = (uri or "").strip()
                    if not uri:
                        continue
                    try:
                        parsed = parse_proxy_uri(uri)
                    except Exception:
                        continue

                    username = (parsed.username or "").strip()
                    password_v = parsed.password
                    password_enc = encryptor.encrypt_text(password_v) if password_v else ""

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
                                conflict_policy == "skip_non_easy_proxies"
                                and (placeholder.source or "") == "easy_proxies"
                            )
                            if can_update_placeholder:
                                existing = placeholder
                                existing.host = desired_host

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
                                source_ref=base_url_ref,
                                updated_at=now,
                            )
                        )
                        continue

                    if conflict_policy == "skip_non_easy_proxies" and (existing.source or "") != "easy_proxies":
                        continue
                    if conflict_policy == "skip":
                        continue

                    existing.password_enc = password_enc
                    existing.enabled = 1
                    existing.source = "easy_proxies"
                    existing.source_ref = base_url_ref
                    # easy-proxies export only contains currently-healthy endpoints; clear local blacklist so they can be used immediately.
                    existing.blacklisted_until = None
                    existing.last_error = None
                    existing.updated_at = now

                if attach_pool_id is not None:
                    pool = await session.get(ProxyPool, int(attach_pool_id))
                    if pool is None:
                        raise JobPermanentError("payload.attach_pool_id not found")

                    endpoint_ids = (
                        (
                            await session.execute(
                                sa.select(ProxyEndpoint.id)
                                .where(ProxyEndpoint.source == "easy_proxies")
                                .where(ProxyEndpoint.source_ref == base_url_ref)
                                .where(ProxyEndpoint.enabled == 1)
                                .order_by(ProxyEndpoint.id.asc())
                            )
                        )
                        .scalars()
                        .all()
                    )

                    for endpoint_id in endpoint_ids:
                        stmt = sqlite_insert(ProxyPoolEndpoint).values(
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
                        await recompute_token_proxy_bindings(
                            session,
                            pool_id=int(attach_pool_id),
                            now=str(now),
                            max_tokens_per_proxy=int(max_tokens_per_proxy),
                            strict=bool(strict),
                        )

                await session.commit()

        await with_sqlite_busy_retry(_op)

    return _handler
