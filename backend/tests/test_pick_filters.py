from __future__ import annotations

from app.db.pick_filters import (
    build_pick_filter_clauses,
    clamp_random_key,
    clean_tag_groups,
    r18_where_clause,
)


def test_clamp_random_key_bounds() -> None:
    assert clamp_random_key(-1.0) == 0.0
    assert clamp_random_key(0.5) == 0.5
    assert clamp_random_key(1.0) == 0.999999999
    assert clamp_random_key(2.0) == 0.999999999


def test_clean_tag_groups_dedupes_order_insensitive() -> None:
    groups = clean_tag_groups(["girl|boy", " boy | girl ", "solo", "solo", ""])
    assert groups == [["girl", "boy"], ["solo"]]


def test_r18_where_clause_any_is_none() -> None:
    assert r18_where_clause(r18=2, r18_strict=True) is None


def test_build_pick_filter_clauses_empty_allow_set_returns_none() -> None:
    assert build_pick_filter_clauses(ai_type_allowed=set()) is None
    assert build_pick_filter_clauses(illust_type_allowed=set()) is None


def test_build_pick_filter_clauses_status_active() -> None:
    clauses = build_pick_filter_clauses(r18=2)
    assert clauses is not None
    assert len(clauses) >= 1
