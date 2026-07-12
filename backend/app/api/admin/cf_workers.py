from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.admin_json import admin_ok
from app.core.admin_request import (
    load_json_object,
    load_json_object_optional,
    parse_bool,
    parse_optional_str,
    parse_required_str,
)
from app.core.cf_pool_overlay import (
    ensure_overlay_fresh,
    get_api_overlay_bases,
    get_api_overlay_enabled,
    get_image_overlay_bases,
    get_image_overlay_enabled,
    set_api_overlay_bases,
    set_api_overlay_enabled,
    set_api_overlay_secret,
    set_image_overlay_bases,
    set_image_overlay_enabled,
    set_image_overlay_secret,
)
from app.core.cf_pool_probe import probe_cf_pool_bases
from app.core.cf_pool_registry import (
    RUNTIME_KEY_API_BASES,
    RUNTIME_KEY_API_ENABLED,
    RUNTIME_KEY_API_SECRET,
    RUNTIME_KEY_IMAGE_BASES,
    RUNTIME_KEY_IMAGE_ENABLED,
    RUNTIME_KEY_IMAGE_SECRET,
    RUNTIME_KEY_IMAGE_SECRET_PREVIOUS,
    merge_base_url_lists,
    normalize_cf_base_url,
    pool_members_from_bases,
    register_base_url,
    unregister_base_url,
)
from app.core.cf_api_proxy import snapshot_cf_base_cooldown
from app.core.cf_worker_deploy import (
    CfWorkerDeployError,
    delete_cf_worker_script,
    deploy_cf_worker,
)
from app.core.egress_policy import (
    egress_policy_snapshot,
    is_force_residential_emergency,
    set_force_residential_emergency,
)
from app.core.errors import ApiError, ErrorCode
from app.core.image_edge import snapshot_image_edge_base_cooldown
from app.core.request_id import get_or_create_request_id
from app.core.runtime_settings import set_runtime_setting

router = APIRouter()


def _settings(request: Request) -> Any:
    return getattr(request.app.state, "settings", None)


def _engine(request: Request) -> Any:
    return getattr(request.app.state, "engine", None)


def _env_api_bases(settings: Any) -> list[str]:
    if settings is None:
        return []
    return list(getattr(settings, "cf_api_proxy_base_urls", None) or [])


def _env_image_bases(settings: Any) -> list[str]:
    if settings is None:
        return []
    return list(getattr(settings, "image_edge_base_urls", None) or [])


async def _persist_overlay(engine: Any, *, kind: str, bases: list[str], updated_by: str) -> None:
    if engine is None:
        return
    key = RUNTIME_KEY_API_BASES if kind == "api" else RUNTIME_KEY_IMAGE_BASES
    await set_runtime_setting(
        engine,
        key=key,
        value=list(bases),
        description=f"CF {kind} pool bases (ops-registered; merged with env)",
        updated_by=updated_by,
    )


async def _persist_enable_and_secret(
    engine: Any,
    *,
    kind: str,
    enabled: bool,
    secret: str,
    secret_previous: str = "",
    updated_by: str,
) -> None:
    """Persist deploy auto-enable + BFF secret overlay (never returns secrets to clients)."""
    if engine is None:
        return
    if kind == "api":
        await set_runtime_setting(
            engine,
            key=RUNTIME_KEY_API_ENABLED,
            value=bool(enabled),
            description="CF API pool business enable (OR with CF_API_PROXY_ENABLED env)",
            updated_by=updated_by,
        )
        if secret:
            await set_runtime_setting(
                engine,
                key=RUNTIME_KEY_API_SECRET,
                value=str(secret),
                description="CF API pool BFF shared secret (fail-closed; never returned by admin APIs)",
                updated_by=updated_by,
            )
        return
    await set_runtime_setting(
        engine,
        key=RUNTIME_KEY_IMAGE_ENABLED,
        value=bool(enabled),
        description="CF image edge business enable (OR with IMAGE_EDGE_ENABLED env)",
        updated_by=updated_by,
    )
    if secret:
        await set_runtime_setting(
            engine,
            key=RUNTIME_KEY_IMAGE_SECRET,
            value=str(secret),
            description="Image edge BFF HMAC secret (never returned by admin APIs)",
            updated_by=updated_by,
        )
    if secret_previous:
        await set_runtime_setting(
            engine,
            key=RUNTIME_KEY_IMAGE_SECRET_PREVIOUS,
            value=str(secret_previous),
            description="Image edge previous HMAC secret for rotation",
            updated_by=updated_by,
        )


@router.get(
    "/cf-workers/pool",
    summary="CF Worker egress pool members",
    description=(
        "List API + image CF pool bases (env CSV merged with runtime-registered members). "
        "Includes process-local base cooldown snapshot (fail_streak / cool_remaining_s; P0-5). "
        "Never returns secrets. Aligns with ds2api multi-member pool mind-set; "
        "Pixiv hardening stays on Worker (secret/HMAC)."
    ),
)
async def cf_workers_pool(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    await ensure_overlay_fresh(_engine(request), force=True)
    settings = _settings(request)
    env_api = _env_api_bases(settings)
    env_img = _env_image_bases(settings)
    rt_api = get_api_overlay_bases()
    rt_img = get_image_overlay_bases()
    api_merged = merge_base_url_lists(env_api, rt_api)
    image_merged = merge_base_url_lists(env_img, rt_img)
    api_members = [
        {"kind": m.kind, "base_url": m.base_url, "source": m.source}
        for m in pool_members_from_bases(kind="api", env_bases=env_api, runtime_bases=rt_api)
    ]
    image_members = [
        {"kind": m.kind, "base_url": m.base_url, "source": m.source}
        for m in pool_members_from_bases(kind="image", env_bases=env_img, runtime_bases=rt_img)
    ]
    policy = egress_policy_snapshot(settings)
    api_cooldown = snapshot_cf_base_cooldown(api_merged)
    image_cooldown = snapshot_image_edge_base_cooldown(image_merged)
    return admin_ok(
        request,
        payload={
            "api": {
                "env_base_urls": merge_base_url_lists(env_api),
                "runtime_base_urls": list(rt_api),
                "merged_base_urls": api_merged,
                "members": api_members,
                "runtime_enabled": bool(get_api_overlay_enabled()),
                "env_enabled": bool(getattr(settings, "cf_api_proxy_enabled", False)) if settings else False,
                "base_cooldown": api_cooldown,
            },
            "image": {
                "env_base_urls": merge_base_url_lists(env_img),
                "runtime_base_urls": list(rt_img),
                "merged_base_urls": image_merged,
                "members": image_members,
                "runtime_enabled": bool(get_image_overlay_enabled()),
                "env_enabled": bool(getattr(settings, "image_edge_enabled", False)) if settings else False,
                "base_cooldown": image_cooldown,
            },
            "egress_policy": policy,
            "note": (
                "Deploy defaults split by kind: image enable_business=true (public Image Edge); "
                "api enable_business=false (Pixiv OAuth/hydrate does not depend on CF API by default). "
                "Explicit enable_business overrides. Runtime flags OR env "
                "CF_API_PROXY_ENABLED / IMAGE_EDGE_ENABLED. Secrets never returned here. "
                "Residential is emergency-only when CF ready "
                "(RESIDENTIAL_EGRESS_EMERGENCY_ONLY, default true). "
                "base_cooldown is process-local exponential demotion (base 30s, cap 300s); "
                "not shared across multi-replica."
            ),
        },
        request_id=rid,
    )


@router.post(
    "/cf-workers/register",
    summary="Register CF Worker base into egress pool",
    description=(
        "Add a Worker base_url to the runtime CF pool (api|image). Merged with env bases at "
        "request time. Does not enable cutover flags. Body: kind, base_url."
    ),
)
async def cf_workers_register(
    request: Request,
    claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    rid = get_or_create_request_id(request)
    data = await load_json_object(request)
    kind = parse_required_str(data.get("kind"), field="kind", max_len=16).lower()
    if kind not in {"api", "image"}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="kind must be api or image", status_code=400)
    base_raw = parse_required_str(data.get("base_url"), field="base_url", max_len=500)
    base = normalize_cf_base_url(base_raw)
    if not base:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid base_url", status_code=400)

    # Multi-process: load current runtime membership before RMW so peer registers
    # are not clobbered by a stale process-local overlay snapshot.
    await ensure_overlay_fresh(_engine(request), force=True)

    if kind == "api":
        next_bases = register_base_url(get_api_overlay_bases(), base)
        set_api_overlay_bases(next_bases)
    else:
        next_bases = register_base_url(get_image_overlay_bases(), base)
        set_image_overlay_bases(next_bases)
        # Invalidate image-edge settings cache so new bases apply immediately.
        try:
            from app.core.image_edge import _EDGE_CFG_FROM_SETTINGS

            _EDGE_CFG_FROM_SETTINGS.clear()
        except Exception:
            pass

    updated_by = str(claims.get("sub") or claims.get("username") or "admin")
    await _persist_overlay(_engine(request), kind=kind, bases=next_bases, updated_by=updated_by)

    return admin_ok(
        request,
        payload={
            "kind": kind,
            "base_url": base,
            "runtime_base_urls": list(next_bases),
            "registered": True,
        },
        request_id=rid,
    )


@router.post(
    "/cf-workers/unregister",
    summary="Unregister CF Worker base from runtime pool",
    description="Remove a runtime-registered base_url (env bases are not deleted).",
)
async def cf_workers_unregister(
    request: Request,
    claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    rid = get_or_create_request_id(request)
    data = await load_json_object(request)
    kind = parse_required_str(data.get("kind"), field="kind", max_len=16).lower()
    if kind not in {"api", "image"}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="kind must be api or image", status_code=400)
    base_raw = parse_required_str(data.get("base_url"), field="base_url", max_len=500)
    base = normalize_cf_base_url(base_raw)
    if not base:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid base_url", status_code=400)

    await ensure_overlay_fresh(_engine(request), force=True)

    if kind == "api":
        next_bases = unregister_base_url(get_api_overlay_bases(), base)
        set_api_overlay_bases(next_bases)
    else:
        next_bases = unregister_base_url(get_image_overlay_bases(), base)
        set_image_overlay_bases(next_bases)
        try:
            from app.core.image_edge import _EDGE_CFG_FROM_SETTINGS

            _EDGE_CFG_FROM_SETTINGS.clear()
        except Exception:
            pass

    updated_by = str(claims.get("sub") or claims.get("username") or "admin")
    await _persist_overlay(_engine(request), kind=kind, bases=next_bases, updated_by=updated_by)

    return admin_ok(
        request,
        payload={
            "kind": kind,
            "base_url": base,
            "runtime_base_urls": list(next_bases),
            "unregistered": True,
        },
        request_id=rid,
    )


@router.post(
    "/cf-workers/delete-script",
    summary="Delete CF Worker script from Cloudflare account",
    description=(
        "Optional ds2api-parity teardown: DELETE the Worker script via Cloudflare API. "
        "Requires api_token + account_id + worker_name (token never stored). "
        "By default also unregisters matching workers.dev base from the runtime pool when "
        "kind is provided. Does not flip env-only members; does not clear BFF secrets."
    ),
)
async def cf_workers_delete_script(
    request: Request,
    claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    rid = get_or_create_request_id(request)
    data = await load_json_object(request)
    api_token = parse_required_str(data.get("api_token"), field="api_token", max_len=200)
    account_id = parse_required_str(data.get("account_id"), field="account_id", max_len=64)
    worker_name = parse_required_str(data.get("worker_name"), field="worker_name", max_len=63)
    kind_raw = parse_optional_str(data.get("kind"))
    kind = (kind_raw or "").strip().lower()
    if kind and kind not in {"api", "image"}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="kind must be api or image", status_code=400)
    unregister_pool = parse_bool(data.get("unregister_pool"), default=True)

    try:
        result = await delete_cf_worker_script(
            api_token=api_token,
            account_id=account_id,
            worker_name=worker_name,
            client=getattr(request.app.state, "httpx_client", None),
        )
    except CfWorkerDeployError as exc:
        status = int(exc.status_code)
        if status < 500:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message=str(exc), status_code=status) from exc
        raise ApiError(code=ErrorCode.UPSTREAM_STREAM_ERROR, message=str(exc), status_code=502) from exc

    unregistered = False
    runtime_bases: list[str] = []
    base_url: str | None = None
    if unregister_pool and kind:
        # Best-effort: derive workers.dev base from known account host if client passed base_url;
        # else strip using provided base_url field, else skip if neither.
        base_hint = parse_optional_str(data.get("base_url")) or ""
        base = normalize_cf_base_url(base_hint) if base_hint else ""
        if not base:
            # Common workers.dev shape: https://{worker_name}.{subdomain}.workers.dev
            # Without account host we cannot invent it — require base_url for pool cleanup.
            base = ""
        if base:
            await ensure_overlay_fresh(_engine(request), force=True)
            if kind == "api":
                runtime_bases = unregister_base_url(get_api_overlay_bases(), base)
                set_api_overlay_bases(runtime_bases)
            else:
                runtime_bases = unregister_base_url(get_image_overlay_bases(), base)
                set_image_overlay_bases(runtime_bases)
                try:
                    from app.core.image_edge import _EDGE_CFG_FROM_SETTINGS

                    _EDGE_CFG_FROM_SETTINGS.clear()
                except Exception:
                    pass
            updated_by = str(claims.get("sub") or claims.get("username") or "admin")
            await _persist_overlay(_engine(request), kind=kind, bases=runtime_bases, updated_by=updated_by)
            unregistered = True
            base_url = base

    note = (
        "CF script deleted (or already absent). Runtime pool unregister requires kind+base_url. "
        "BFF secrets and env bases are unchanged."
    )
    return admin_ok(
        request,
        payload={
            "worker_name": result.worker_name,
            "deleted": bool(result.deleted),
            "already_absent": bool(result.already_absent),
            "kind": kind or None,
            "base_url": base_url,
            "unregistered": unregistered,
            "runtime_base_urls": list(runtime_bases) if unregistered else None,
            "note": note,
        },
        request_id=rid,
    )


@router.post(
    "/cf-workers/deploy",
    summary="Deploy CF Worker via Cloudflare API and register into pool",
    description=(
        "One-shot deploy: upload **this repo's** hardened Worker script "
        "(api-worker or img-worker), enable workers.dev, register base into runtime pool. "
        "**Default enable_business is kind-split:** "
        "image → true (public Image Edge / /random bytes via self-built img-worker); "
        "api → false (Pixiv OAuth/hydrate/metadata default does **not** depend on CF API proxy; "
        "set enable_business=true to opt in). "
        "Requires api_token, account_id, worker_name, kind. "
        "api: proxy_secret (or uses CF_API_PROXY_SECRET). "
        "image: image_edge_secret (or uses IMAGE_EDGE_SECRET). "
        "Never stores CF API token. Does **not** copy ds2api open whole-site proxy script."
    ),
)
async def cf_workers_deploy(
    request: Request,
    claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    rid = get_or_create_request_id(request)
    data = await load_json_object(request)
    kind = parse_required_str(data.get("kind"), field="kind", max_len=16).lower()
    if kind not in {"api", "image"}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="kind must be api or image", status_code=400)

    api_token = parse_required_str(data.get("api_token"), field="api_token", max_len=200)
    account_id = parse_required_str(data.get("account_id"), field="account_id", max_len=64)
    worker_name = parse_required_str(data.get("worker_name"), field="worker_name", max_len=63)
    register = parse_bool(data.get("register"), default=True)
    # Product default split (goal):
    # - Public image path: default enable self-built CF image edge after deploy.
    # - Pixiv business API path: default do NOT enable CF API proxy (opt-in).
    default_enable_business = kind == "image"
    enable_business = parse_bool(data.get("enable_business"), default=default_enable_business)

    settings = _settings(request)
    # Reuse env secret, then process overlay, else generate once (returned only in this response).
    try:
        from app.core.cf_pool_overlay import get_api_overlay_secret, get_image_overlay_secret

        overlay_api_secret = str(get_api_overlay_secret() or "").strip()
        overlay_image_secret = str(get_image_overlay_secret() or "").strip()
    except Exception:
        overlay_api_secret = ""
        overlay_image_secret = ""

    proxy_secret = parse_optional_str(data.get("proxy_secret")) or (
        str(getattr(settings, "cf_api_proxy_secret", "") or "").strip() if settings is not None else ""
    ) or overlay_api_secret
    image_edge_secret = parse_optional_str(data.get("image_edge_secret")) or (
        str(getattr(settings, "image_edge_secret", "") or "").strip() if settings is not None else ""
    ) or overlay_image_secret
    prewarm_secret = parse_optional_str(data.get("prewarm_secret")) or ""
    image_edge_secret_previous = parse_optional_str(data.get("image_edge_secret_previous")) or (
        str(getattr(settings, "image_edge_secret_previous", "") or "").strip() if settings is not None else ""
    )

    secret_generated = False
    generated_secret_once: str | None = None
    if kind == "api" and not proxy_secret:
        import secrets as _secrets

        proxy_secret = _secrets.token_urlsafe(32)
        secret_generated = True
        generated_secret_once = proxy_secret
    elif kind == "image" and not image_edge_secret:
        import secrets as _secrets

        image_edge_secret = _secrets.token_urlsafe(32)
        secret_generated = True
        generated_secret_once = image_edge_secret

    try:
        result = await deploy_cf_worker(
            kind="api" if kind == "api" else "image",
            api_token=api_token,
            account_id=account_id,
            worker_name=worker_name,
            proxy_secret=proxy_secret or "",
            image_edge_secret=image_edge_secret or "",
            prewarm_secret=prewarm_secret or "",
            image_edge_secret_previous=image_edge_secret_previous or "",
            client=getattr(request.app.state, "httpx_client", None),
        )
    except CfWorkerDeployError as exc:
        status = int(exc.status_code)
        if status < 500:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message=str(exc), status_code=status) from exc
        raise ApiError(code=ErrorCode.UPSTREAM_STREAM_ERROR, message=str(exc), status_code=502) from exc

    updated_by = str(claims.get("sub") or claims.get("username") or "admin")
    runtime_bases: list[str] = []
    business_enabled = False
    # Refresh before RMW so multi-process deploy+register does not drop peer bases.
    await ensure_overlay_fresh(_engine(request), force=True)
    if register:
        if kind == "api":
            runtime_bases = register_base_url(get_api_overlay_bases(), result.base_url)
            set_api_overlay_bases(runtime_bases)
        else:
            runtime_bases = register_base_url(get_image_overlay_bases(), result.base_url)
            set_image_overlay_bases(runtime_bases)
            try:
                from app.core.image_edge import _EDGE_CFG_FROM_SETTINGS

                _EDGE_CFG_FROM_SETTINGS.clear()
            except Exception:
                pass
        await _persist_overlay(_engine(request), kind=kind, bases=runtime_bases, updated_by=updated_by)
    else:
        runtime_bases = get_api_overlay_bases() if kind == "api" else get_image_overlay_bases()

    # Default product path: write BFF secret overlay + enable business flag.
    # Secrets used for Worker upload are the same shared secrets BFF needs for ready.
    if enable_business:
        if kind == "api":
            secret_for_bff = str(proxy_secret or "").strip()
            if secret_for_bff:
                set_api_overlay_secret(secret_for_bff)
            set_api_overlay_enabled(True)
            business_enabled = True
            await _persist_enable_and_secret(
                _engine(request),
                kind="api",
                enabled=True,
                secret=secret_for_bff,
                updated_by=updated_by,
            )
        else:
            secret_for_bff = str(image_edge_secret or "").strip()
            prev = str(image_edge_secret_previous or "").strip()
            if secret_for_bff:
                set_image_overlay_secret(secret_for_bff, previous=prev)
            set_image_overlay_enabled(True)
            business_enabled = True
            try:
                from app.core.image_edge import _EDGE_CFG_FROM_SETTINGS

                _EDGE_CFG_FROM_SETTINGS.clear()
            except Exception:
                pass
            await _persist_enable_and_secret(
                _engine(request),
                kind="image",
                enabled=True,
                secret=secret_for_bff,
                secret_previous=prev,
                updated_by=updated_by,
            )

    # Human success: whether egress is actually ready after this deploy.
    if kind == "api":
        from app.core.cf_api_proxy import load_cf_api_proxy_config_from_settings

        cfg = load_cf_api_proxy_config_from_settings(settings)
        ready = bool(cfg is not None and cfg.ready)
        success_message = (
            f"API 出口已部署并加入池：{result.worker_host}"
            + ("；业务已启用" if business_enabled else "；未启用业务（enable_business=false）")
            + ("；当前 ready" if ready else "；尚未 ready（检查 secret/bases）")
        )
    else:
        from app.core.image_edge import image_edge_is_ready

        ready = bool(settings is not None and image_edge_is_ready(settings))
        success_message = (
            f"出图边缘已部署并加入池：{result.worker_host}"
            + ("；业务已启用" if business_enabled else "；未启用业务（enable_business=false）")
            + ("；当前 ready（默认反代 i.pximg.net）" if ready else "；尚未 ready（检查 secret/bases）")
        )

    # Never echo api_token. Generated secret is returned once only (ops must save it).
    payload: dict[str, Any] = {
        "deployed": True,
        "kind": result.kind,
        "worker_name": result.worker_name,
        "worker_host": result.worker_host,
        "base_url": result.base_url,
        "secrets_set": list(result.secrets_set),
        "registered": bool(register),
        "business_enabled": bool(business_enabled),
        "ready": bool(ready),
        "message": success_message,
        "runtime_base_urls": list(runtime_bases),
        "secret_generated": bool(secret_generated),
        # R2 bucket binding is never attached by this deploy path (wrangler/dashboard only).
        "r2_binding": bool(getattr(result, "r2_binding", False)),
        "r2_note": str(getattr(result, "r2_note", "") or ""),
        "cutover_hint": (
            "出图 (image)：默认 register + enable Image Edge（公开 /random、入库读图）。"
            " API (api)：默认只 register、不 enable 业务（OAuth/hydrate 不依赖 CF API；"
            " 需要时 enable_business=true 或 CF_API_PROXY_ENABLED）。"
            " 若 secret_generated=true，请立即保存 generated_secret（仅此响应回显一次）。"
            " 出图主路径：img-worker → i.pximg.net。"
        ),
    }
    if secret_generated and generated_secret_once:
        payload["generated_secret"] = generated_secret_once
        payload["generated_secret_note"] = (
            "系统生成的共享密钥，仅此响应回显一次；已写入 Worker 与 BFF runtime overlay。"
        )
    return admin_ok(request, payload=payload, request_id=rid)


@router.get(
    "/cf-workers/egress-policy",
    summary="Residential emergency-only egress policy",
    description=(
        "Read-only: whether residential is demoted when CF pools are ready. "
        "Includes process-local force_residential_emergency override."
    ),
)
async def cf_workers_egress_policy(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    return admin_ok(
        request,
        payload=dict(egress_policy_snapshot(_settings(request))),
        request_id=rid,
    )


@router.post(
    "/cf-workers/egress-policy",
    summary="Set process-local force residential emergency override",
    description=(
        "Body: force_residential_emergency=true|false. Process-local only (not durable; "
        "does not change RESIDENTIAL_EGRESS_EMERGENCY_ONLY env). When true, residential "
        "egress is allowed even if CF API / image edge is ready. Ops emergency path."
    ),
)
async def cf_workers_egress_policy_set(
    request: Request,
    claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = claims
    rid = get_or_create_request_id(request)
    data = await load_json_object(request)
    if "force_residential_emergency" not in data:
        raise ApiError(
            code=ErrorCode.BAD_REQUEST,
            message="force_residential_emergency is required",
            status_code=400,
        )
    enabled = parse_bool(data.get("force_residential_emergency"), default=False)
    set_force_residential_emergency(enabled)
    snap = dict(egress_policy_snapshot(_settings(request)))
    snap["updated"] = True
    snap["force_residential_emergency"] = is_force_residential_emergency()
    return admin_ok(request, payload=snap, request_id=rid)


@router.post(
    "/cf-workers/probe",
    summary="Probe CF Worker pool bases (healthz)",
    description=(
        "Outbound GET {base}/healthz for each merged pool member (or body base_urls). "
        "Records process-local base cooldown on hard failure (does not flip enable flags). "
        "Body optional: kind=api|image|all (default all), base_urls=[…] override "
        "(override requires kind=api|image, not all), timeout_s."
    ),
)
async def cf_workers_probe(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    data = await load_json_object_optional(request)
    kind_raw = (parse_optional_str(data.get("kind")) or "all").strip().lower()
    if kind_raw not in {"api", "image", "all"}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="kind must be api, image, or all", status_code=400)
    timeout_s = 3.0
    try:
        if data.get("timeout_s") is not None:
            timeout_s = float(data.get("timeout_s"))
    except Exception:
        timeout_s = 3.0
    timeout_s = max(0.5, min(timeout_s, 15.0))

    # Force-refresh so multi-process peers probe current runtime membership.
    await ensure_overlay_fresh(_engine(request), force=True)
    settings = _settings(request)
    env_api = _env_api_bases(settings)
    env_img = _env_image_bases(settings)
    rt_api = get_api_overlay_bases()
    rt_img = get_image_overlay_bases()

    override_raw = data.get("base_urls")
    override: list[str] = []
    if isinstance(override_raw, list):
        override = [str(x) for x in override_raw if str(x or "").strip()]
    elif isinstance(override_raw, str) and override_raw.strip():
        override = [override_raw.strip()]
    # Override bases apply to one kind only — probing the same list as both api+image
    # would write cooldown into both maps. kind=all without override still uses each pool.
    if override and kind_raw == "all":
        raise ApiError(
            code=ErrorCode.BAD_REQUEST,
            message="base_urls override requires kind=api or kind=image (not all)",
            status_code=400,
        )

    http_client = getattr(request.app.state, "httpx_client", None)
    owns_client = False
    if http_client is None:
        http_client = httpx.AsyncClient()
        owns_client = True

    api_results: list[dict[str, Any]] = []
    image_results: list[dict[str, Any]] = []
    try:
        if kind_raw in {"api", "all"}:
            api_bases = merge_base_url_lists(override) if override else merge_base_url_lists(env_api, rt_api)
            api_results = await probe_cf_pool_bases(
                http_client, kind="api", bases=api_bases, timeout_s=timeout_s
            )
        if kind_raw in {"image", "all"}:
            img_bases = merge_base_url_lists(override) if override else merge_base_url_lists(env_img, rt_img)
            image_results = await probe_cf_pool_bases(
                http_client, kind="image", bases=img_bases, timeout_s=timeout_s
            )
    finally:
        if owns_client:
            await http_client.aclose()

    def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        ok_n = sum(1 for r in rows if r.get("ok"))
        return {"total": len(rows), "ok": ok_n, "fail": len(rows) - ok_n}

    return admin_ok(
        request,
        payload={
            "probed": True,
            "kind": kind_raw,
            "timeout_s": timeout_s,
            "api": {"results": api_results, "summary": _summary(api_results)},
            "image": {"results": image_results, "summary": _summary(image_results)},
            "note": (
                "Probe never enables CF_API_PROXY_ENABLED / IMAGE_EDGE_ENABLED. "
                "Hard failures demote process-local base cooldown "
                "(exponential: base 30s × 2^(streak-1), cap 300s; success clears)."
            ),
        },
        request_id=rid,
    )
