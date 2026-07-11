"""Worker dispatcher Settings inject (one load_settings at process bootstrap)."""

from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet

from app.core.config import load_settings
from app.db.engine import create_engine
from app.jobs.handlers.heal_url import build_heal_url_handler
from app.jobs.handlers.import_images import build_import_images_handler
from app.worker import build_default_dispatcher


def test_build_default_dispatcher_accepts_settings(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker_settings.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))

    settings = load_settings()
    engine = create_engine(settings.database_url)
    dispatcher = build_default_dispatcher(engine, settings=settings)
    # Core catalog-writing + ops handlers registered.
    for job_type in (
        "import_images",
        "hydrate_metadata",
        "heal_url",
        "proxy_probe",
        "easy_proxies_import",
    ):
        assert job_type in dispatcher.handlers


def test_build_heal_url_handler_accepts_tag_store(tmp_path: Path, monkeypatch) -> None:
    """heal_url forwards injected TagStore into hydrate (no second factory when provided)."""
    db_path = tmp_path / "heal_tag.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))

    settings = load_settings()
    engine = create_engine(settings.database_url)

    class _TagPort:
        backend = "test-tags"

    handler = build_heal_url_handler(
        engine,
        tag_store=_TagPort(),  # type: ignore[arg-type]
        settings=settings,
    )
    assert callable(handler)


def test_build_import_images_handler_accepts_injected_ports(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "import_ports.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")

    settings = load_settings()
    engine = create_engine(settings.database_url)

    class _Catalog:
        backend = "test-catalog"

    class _Tags:
        backend = "test-tags"

    handler = build_import_images_handler(
        engine,
        catalog=_Catalog(),  # type: ignore[arg-type]
        tag_store=_Tags(),  # type: ignore[arg-type]
        settings=settings,
    )
    assert callable(handler)