from __future__ import annotations

import json
import time

from fastapi import FastAPI, Request

from app.api.admin.router import router as admin_router
from app.api.metrics import router as metrics_router
from app.api.public.healthz import router as healthz_router
from app.api.public.docs_page import router as docs_page_router
from app.api.public.authors import router as authors_router
from app.api.public.images import router as images_router
from app.api.public.legacy import router as legacy_router
from app.api.public.random import router as random_router
from app.api.public.tags import router as tags_router
from app.api.public.version import router as version_router
from app.core.config import load_settings
from app.core.api_keys import ApiKeyAuthConfig, ApiKeyAuthenticator, ApiKeyRateLimiter, require_public_api_key
from app.core.errors import ApiError, ErrorCode, json_error_response
from app.core.logging import configure_logging, get_logger
from app.core.metrics import observe_random_result
from app.core.random_request_stats import RandomRequestStats
from app.core.request_id import build_request_id_middleware, get_or_create_request_id, set_request_id_on_state
from app.core.security import decode_jwt, parse_bearer_token
from app.db.engine import create_engine
from app.db.models.admin_audit import AdminAudit
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.web.admin_ui import mount_admin_ui

log = get_logger(__name__)


def create_app() -> FastAPI:
    configure_logging()
    settings = load_settings()

    # NOTE: We reserve `/docs` for a public human-readable documentation page.
    # Keep Swagger/Redoc available under `/api/*` paths for troubleshooting.
    app = FastAPI(title="new-pixiv-api", docs_url="/api/docs", redoc_url="/api/redoc")

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError):  # type: ignore[no-redef]
        return json_error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            request=request,
            details=exc.details,
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):  # type: ignore[no-redef]
        try:
            log.exception("unhandled_exception path=%s", str(getattr(request, "url", "")))
        except Exception:
            pass
        return json_error_response(
            code=ErrorCode.INTERNAL_ERROR,
            message="服务器内部错误",
            status_code=500,
            request=request,
            details={"error_type": type(exc).__name__},
        )

    request_id_middleware = build_request_id_middleware()
    if request_id_middleware is not None:
        app.add_middleware(request_id_middleware)

    engine = create_engine(settings.database_url)
    app.state.engine = engine

    api_key_cfg = ApiKeyAuthConfig(
        required=bool(settings.public_api_key_required),
        rpm=int(settings.public_api_key_rpm),
        burst=int(settings.public_api_key_burst),
        secret_key=str(settings.secret_key),
    )
    app.state.api_key_authenticator = ApiKeyAuthenticator(engine, api_key_cfg)
    app.state.api_key_limiter = ApiKeyRateLimiter(rpm=int(api_key_cfg.rpm), burst=int(api_key_cfg.burst))
    app.state.random_request_stats = RandomRequestStats(window_seconds=60)

    @app.middleware("http")
    async def _public_api_key_middleware(request: Request, call_next):  # type: ignore[no-redef]
        if not bool(settings.public_api_key_required):
            return await call_next(request)

        path = request.url.path
        if path.startswith("/admin") or path.startswith("/metrics"):
            return await call_next(request)
        if path in {"/healthz", "/version", "/openapi.json", "/docs", "/api/docs", "/api/redoc"}:
            return await call_next(request)

        rid = get_or_create_request_id(request)
        set_request_id_on_state(request, rid)

        try:
            api_key_id = await require_public_api_key(
                request.app.state.api_key_authenticator,
                request.app.state.api_key_limiter,
                headers=request.headers,
            )
        except ApiError as exc:
            return json_error_response(
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                request=request,
                details=exc.details,
            )
        request.state.api_key_id = int(api_key_id)

        return await call_next(request)

    def _best_effort_admin_actor(request: Request) -> str | None:
        authorization = request.headers.get("Authorization") or request.headers.get("authorization")
        token = parse_bearer_token(authorization)
        if not token:
            return None
        try:
            claims = decode_jwt(token, secret_key=str(settings.secret_key))
        except Exception:
            return None
        sub = str(claims.get("sub") or "").strip()
        return sub or None

    def _best_effort_client_ip(request: Request) -> str | None:
        xff = request.headers.get("X-Forwarded-For") or request.headers.get("x-forwarded-for")
        if xff:
            ip = str(xff).split(",", 1)[0].strip()
            return ip or None
        client = getattr(request, "client", None)
        host = getattr(client, "host", None) if client is not None else None
        return str(host).strip() if host else None

    @app.middleware("http")
    async def _admin_audit_middleware(request: Request, call_next):  # type: ignore[no-redef]
        path = request.url.path
        method = (request.method or "").upper()

        response = await call_next(request)

        if not path.startswith("/admin/api/"):
            return response
        if method in {"GET", "HEAD", "OPTIONS"}:
            return response

        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
        except Exception:
            status_code = 0
        if status_code >= 400:
            return response

        try:
            rid = get_or_create_request_id(request)
            actor = _best_effort_admin_actor(request)
            ip = _best_effort_client_ip(request)
            user_agent = request.headers.get("User-Agent") or request.headers.get("user-agent")

            segments = [seg for seg in path.split("/") if seg]
            record_id = next((seg for seg in reversed(segments) if seg.isdigit()), None)

            detail_json = {"status": status_code, "query": dict(request.query_params)}

            engine = request.app.state.engine
            Session = create_sessionmaker(engine)

            async def _op() -> None:
                async with Session() as session:
                    session.add(
                        AdminAudit(
                            actor=actor,
                            action=method,
                            resource=path,
                            record_id=str(record_id) if record_id else None,
                            request_id=str(rid),
                            ip=ip,
                            user_agent=str(user_agent) if user_agent else None,
                            detail_json=json.dumps(detail_json, ensure_ascii=False, separators=(",", ":")),
                        )
                    )
                    await session.commit()

            await with_sqlite_busy_retry(_op)
        except Exception:
            pass

        return response

    def _random_result_from_status(status: int) -> str:
        if status in {200, 301, 302, 303, 307, 308}:
            return "ok"
        if status == 404:
            return "no_match"
        if status == 502:
            return "upstream_error"
        if status == 400:
            return "bad_request"
        return "error"

    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next):  # type: ignore[no-redef]
        if request.url.path != "/random":
            return await call_next(request)

        started = time.monotonic()
        stats = getattr(request.app.state, "random_request_stats", None)
        if stats is not None:
            try:
                await stats.on_begin()
            except Exception:
                pass
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_s = time.monotonic() - started
            status_code = int(getattr(response, "status_code", 0) or 0)
            observe_random_result(result=_random_result_from_status(status_code), duration_s=duration_s)
            if stats is not None:
                try:
                    await stats.on_end(status_code=int(status_code))
                except Exception:
                    pass

    app.state.settings = settings

    @app.on_event("shutdown")
    async def _shutdown() -> None:  # type: ignore[no-redef]
        engine = getattr(app.state, "engine", None)
        if engine is not None:
            await engine.dispose()

    app.include_router(healthz_router)
    app.include_router(docs_page_router)
    app.include_router(authors_router)
    app.include_router(images_router)
    app.include_router(legacy_router)
    app.include_router(random_router)
    app.include_router(tags_router)
    app.include_router(version_router)
    app.include_router(metrics_router)
    app.include_router(admin_router)

    mount_admin_ui(app)

    return app


app = create_app()
