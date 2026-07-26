from __future__ import annotations

from app.api.public.docs_page import _build_docs_html


def test_docs_html_documents_debug_engine_status() -> None:
    html = _build_docs_html(base_url="https://example.test", public_api_key_required=False)
    assert "/random?format=json&debug=1" in html
    assert "/feed?limit=8&debug=1" in html
    assert "engine_status" in html
    assert "skipped_circuit" in html
    assert "batch_count" in html
    # Default JSON examples remain without forcing debug on every link.
    assert "/random?format=json" in html
    # Public status honesty: dual-run circuit on /status.json (parity with /healthz).
    assert "data.random_engine" in html
    assert "modules.random_engine.circuit" in html
    assert "/status.json" in html
    # Image Edge readiness on public status (parity with /healthz modules.image_edge).
    assert "data.image_edge" in html
    assert "modules.image_edge" in html
    # CF API proxy readiness on public status (parity with /healthz modules.cf_api_proxy).
    assert "data.cf_api_proxy" in html
    assert "modules.cf_api_proxy" in html
    # R2 prewarm readiness on public status (parity with /healthz modules.r2_prewarm).
    assert "data.r2_prewarm" in html
    assert "modules.r2_prewarm" in html
    # API key rate-limit honesty on public status (parity with /healthz modules.api_key_rate_limit).
    assert "data.api_key_rate_limit" in html
    assert "modules.api_key_rate_limit" in html
    # Modular ports honesty on public status.
    assert "data.job_queue" in html
    assert "modules.job_queue" in html
    assert "data.recent_dedup" in html
    assert "modules.recent_dedup" in html
    # Dialect labels on public status.
    assert "data.catalog.backend" in html
    assert "data.tags.backend" in html
    assert "data.random_service.backend" in html
    assert "data.random_pick.backend" in html
    # Catalog list + delivery + version links (parity with OpenAPI public summaries).
    assert 'href="https://example.test/tags"' in html
    assert 'href="https://example.test/authors"' in html
    assert 'href="https://example.test/images"' in html
    assert 'href="https://example.test/version"' in html
    assert 'href="https://example.test/healthz"' in html
    assert 'href="https://example.test/status.json"' in html
    assert "/i/" in html or "/i/{" in html
    assert "legacy" in html.lower() or "{illust_id}" in html


def test_docs_html_public_key_required_banner() -> None:
    html = _build_docs_html(base_url="", public_api_key_required=True)
    assert "PUBLIC_API_KEY_REQUIRED" in html
    assert "engine_status" in html
    assert "data.random_engine" in html
    assert "data.image_edge" in html
    assert "data.cf_api_proxy" in html
    assert "data.r2_prewarm" in html
    assert "data.api_key_rate_limit" in html
    assert "data.job_queue" in html
    assert "data.recent_dedup" in html
    assert "data.catalog.backend" in html
    assert "data.random_pick.backend" in html
    assert "/images" in html
    assert "/version" in html
    assert "/healthz" in html


def test_public_html_pages_openapi_route_metadata_documents_modular_surfaces() -> None:
    """/docs /status /wtf stay schema-hidden but carry modular-coupled route metadata."""
    from app.main import create_app

    app = create_app()
    # include_in_schema=False → not in openapi paths; assert on route.openapi_extra / endpoint.
    # Newer FastAPI keeps included routers nested instead of flattening app.routes,
    # so collect routes recursively through both layouts.
    by_path: dict[str, object] = {}

    def _collect(routes: object) -> None:
        for route in routes:  # type: ignore[union-attr]
            path = getattr(route, "path", None)
            if path in {"/docs", "/status", "/wtf"}:
                by_path[str(path)] = route
            nested = getattr(route, "original_router", None)
            if nested is not None:
                _collect(nested.routes)
            elif getattr(route, "routes", None):
                _collect(route.routes)

    _collect(app.routes)

    docs = by_path["/docs"]
    assert getattr(docs, "summary", None) == "Public API docs HTML"
    assert "PUBLIC_API_KEY" in str(getattr(docs, "description", "") or "") or "API-key" in str(
        getattr(docs, "description", "") or ""
    )
    assert getattr(docs, "include_in_schema", True) is False

    status = by_path["/status"]
    assert getattr(status, "summary", None) == "Public modular status HTML"
    assert "status.json" in str(getattr(status, "description", "") or "")
    assert getattr(status, "include_in_schema", True) is False

    wtf = by_path["/wtf"]
    assert getattr(wtf, "summary", None) == "Public WTF waterfall HTML"
    assert "feed" in str(getattr(wtf, "description", "") or "").lower()
    assert getattr(wtf, "include_in_schema", True) is False
