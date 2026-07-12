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
    get_api_overlay_bases,
    get_image_overlay_bases,
    set_api_overlay_bases,
    set_image_overlay_bases,
)
from app.core.cf_pool_probe import probe_cf_pool_bases
from app.core.cf_pool_registry import (
    RUNTIME_KEY_API_BASES,
    RUNTIME_KEY_IMAGE_BASES,
    merge_base_url_lists,
    normalize_cf_base_url,
    pool_members_from_bases,
    register_base_url,
    unregister_base_url,
)
from app.core.cf_worker_deploy import CfWorkerDeployError, deploy_cf_worker
from app.core.egress_policy import egress_policy_snapshot
from app.core.errors import ApiError, ErrorCode
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


@router.get(
    "/cf-workers/pool",
    summary="CF Worker egress pool members",
    description=(
        "List API + image CF pool bases (env CSV merged with runtime-registered members). "
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
    settings = _settings(request)
    env_api = _env_api_bases(settings)
    env_img = _env_image_bases(settings)
    rt_api = get_api_overlay_bases()
    rt_img = get_image_overlay_bases()
    api_members = [
        {"kind": m.kind, "base_url": m.base_url, "source": m.source}
        for m in pool_members_from_bases(kind="api", env_bases=env_api, runtime_bases=rt_api)
    ]
    image_members = [
        {"kind": m.kind, "base_url": m.base_url, "source": m.source}
        for m in pool_members_from_bases(kind="image", env_bases=env_img, runtime_bases=rt_img)
    ]
    policy = egress_policy_snapshot(settings)
    return admin_ok(
        request,
        payload={
            "api": {
                "env_base_urls": merge_base_url_lists(env_api),
                "runtime_base_urls": list(rt_api),
                "merged_base_urls": merge_base_url_lists(env_api, rt_api),
                "members": api_members,
            },
            "image": {
                "env_base_urls": merge_base_url_lists(env_img),
                "runtime_base_urls": list(rt_img),
                "merged_base_urls": merge_base_url_lists(env_img, rt_img),
                "members": image_members,
            },
            "egress_policy": policy,
            "note": (
                "Deploy/register does not flip CF_API_PROXY_ENABLED / IMAGE_EDGE_ENABLED. "
                "Probe green, then enable flags. Residential is emergency-only when CF ready "
                "(RESIDENTIAL_EGRESS_EMERGENCY_ONLY, default true)."
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
    "/cf-workers/deploy",
    summary="Deploy CF Worker via Cloudflare API and register into pool",
    description=(
        "ds2api-style one-shot deploy: upload **this repo's** hardened Worker script "
        "(api-worker or img-worker), enable workers.dev, register base into runtime pool. "
        "Requires api_token, account_id, worker_name, kind. "
        "api: proxy_secret (or uses CF_API_PROXY_SECRET). "
        "image: image_edge_secret (or uses IMAGE_EDGE_SECRET). "
        "Never enables cutover flags; never stores CF API token. "
        "Does **not** copy ds2api open whole-site proxy script."
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

    settings = _settings(request)
    proxy_secret = parse_optional_str(data.get("proxy_secret")) or (
        str(getattr(settings, "cf_api_proxy_secret", "") or "").strip() if settings is not None else ""
    )
    image_edge_secret = parse_optional_str(data.get("image_edge_secret")) or (
        str(getattr(settings, "image_edge_secret", "") or "").strip() if settings is not None else ""
    )
    prewarm_secret = parse_optional_str(data.get("prewarm_secret")) or ""
    image_edge_secret_previous = parse_optional_str(data.get("image_edge_secret_previous")) or (
        str(getattr(settings, "image_edge_secret_previous", "") or "").strip() if settings is not None else ""
    )

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

    runtime_bases: list[str] = []
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
        updated_by = str(claims.get("sub") or claims.get("username") or "admin")
        await _persist_overlay(_engine(request), kind=kind, bases=runtime_bases, updated_by=updated_by)
    else:
        runtime_bases = get_api_overlay_bases() if kind == "api" else get_image_overlay_bases()

    # Never echo api_token or secrets.
    return admin_ok(
        request,
        payload={
            "deployed": True,
            "kind": result.kind,
            "worker_name": result.worker_name,
            "worker_host": result.worker_host,
            "base_url": result.base_url,
            "secrets_set": list(result.secrets_set),
            "registered": bool(register),
            "runtime_base_urls": list(runtime_bases),
            "cutover_hint": (
                "Probe healthz, set CF_API_PROXY_BASE_URLS / IMAGE_EDGE_BASE_URLS (or rely on "
                "runtime pool), match secrets, then enable CF_API_PROXY_ENABLED / IMAGE_EDGE_ENABLED."
            ),
        },
        request_id=rid,
    )


@router.get(
    "/cf-workers/egress-policy",
    summary="Residential emergency-only egress policy",
    description="Read-only: whether residential is demoted when CF pools are ready.",
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
                "Hard failures demote process-local base cooldown (~30s)."
            ),
        },
        request_id=rid,
    )
