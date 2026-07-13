from __future__ import annotations

from app.api.public.wtf_page import _build_wtf_html


def test_wtf_uses_one_shared_feed_batch_request() -> None:
    html = _build_wtf_html(base_url="https://example.test", public_api_key_required=False)

    assert "async function fetchFeedBatch(limit, signal)" in html
    assert 'fetch(url, { cache: "no-store", signal: signal })' in html
    assert "if (feedFetchPromise)" in html
    assert "await feedFetchPromise" in html
    assert "feedFetchPromise = promise" in html
    assert "fetchRandomData" in html
    assert "feedQueue.shift()" in html
    assert "fetch(buildRandom" not in html
    assert "fetch(randomUrl" not in html


def test_wtf_reset_aborts_and_fences_stale_feed_response() -> None:
    html = _build_wtf_html(base_url="", public_api_key_required=False)

    assert "const myGeneration = generation" in html
    assert "const owner = ++feedFetchOwner" in html
    assert "const controller = new AbortController()" in html
    assert "controller.signal.aborted || myGeneration !== generation || owner !== feedFetchOwner" in html
    assert "if (feedAbortController) feedAbortController.abort()" in html
    assert "if (feedFetchPromise === promise) feedFetchPromise = null" in html
    assert "if (feedAbortController === controller) feedAbortController = null" in html
    assert "async function fetchRandomData(expectedGeneration)" in html
    assert "if (expectedGeneration !== generation)" in html
    assert "fetchRandomData(myGen)" in html


def test_wtf_image_failure_does_not_consume_replacement_feed_items() -> None:
    html = _build_wtf_html(base_url="", public_api_key_required=False)
    error_handler = html.split("img.onerror = () => {", 1)[1].split("attempt();", 1)[0]

    assert "cascadeFallback" in error_handler
    assert 'finishFail("IMAGE_ERROR")' in error_handler
    assert "setTimeout(() => attempt()" not in error_handler


def test_wtf_bad_item_does_not_consume_replacement_feed_items() -> None:
    html = _build_wtf_html(base_url="", public_api_key_required=False)
    attempt_body = html.split("const attempt = async () => {", 1)[1].split("img.onload =", 1)[0]

    assert "let consumedFeedItem = false" in attempt_body
    assert "consumedFeedItem = true" in attempt_body
    assert "if (!consumedFeedItem && tries < 3)" in attempt_body
