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
    # Catalog list + delivery + version links (parity with OpenAPI public summaries).
    assert 'href="https://example.test/tags"' in html
    assert 'href="https://example.test/authors"' in html
    assert 'href="https://example.test/images"' in html
    assert 'href="https://example.test/version"' in html
    assert "/i/" in html or "/i/{" in html


def test_docs_html_public_key_required_banner() -> None:
    html = _build_docs_html(base_url="", public_api_key_required=True)
    assert "PUBLIC_API_KEY_REQUIRED" in html
    assert "engine_status" in html
    assert "data.random_engine" in html
    assert "data.image_edge" in html
    assert "data.cf_api_proxy" in html
    assert "data.r2_prewarm" in html
    assert "/images" in html
    assert "/version" in html
