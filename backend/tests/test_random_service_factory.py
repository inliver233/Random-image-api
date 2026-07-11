from __future__ import annotations

from app.core.random_delivery import resolve_random_service_factory
from app.core.random_pick_context import (
    DefaultRandomServiceFactory,
    RandomServiceFactory,
    build_random_service_factory,
)


def test_build_random_service_factory_default() -> None:
    factory = build_random_service_factory()
    assert isinstance(factory, DefaultRandomServiceFactory)
    assert factory.backend == "default"
    assert isinstance(factory, RandomServiceFactory)


def test_resolve_random_service_factory_fallback_and_inject() -> None:
    # Import order: resolve path must not circular-import with random_engine_pick.
    fallback = resolve_random_service_factory(None)
    assert isinstance(fallback, DefaultRandomServiceFactory)
    injected = DefaultRandomServiceFactory()
    assert resolve_random_service_factory(injected) is injected
