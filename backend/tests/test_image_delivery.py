from __future__ import annotations

from types import SimpleNamespace

from app.core.image_delivery import should_mark_image_ok
from app.core.random_strategy import needs_opportunistic_hydrate


def test_should_mark_image_ok() -> None:
    assert should_mark_image_ok(SimpleNamespace(last_ok_at=None, last_error_code=None)) is True
    assert should_mark_image_ok(SimpleNamespace(last_ok_at="t", last_error_code="UPSTREAM_403")) is True
    assert should_mark_image_ok(SimpleNamespace(last_ok_at="t", last_error_code=None)) is False


def test_needs_opportunistic_hydrate_missing_fields() -> None:
    incomplete = SimpleNamespace(
        width=None,
        height=100,
        x_restrict=0,
        ai_type=0,
        user_id=1,
        user_name="u",
        title="t",
        created_at_pixiv="2020-01-01T00:00:00Z",
        bookmark_count=1,
        view_count=1,
        comment_count=0,
    )
    assert needs_opportunistic_hydrate(incomplete) is True
