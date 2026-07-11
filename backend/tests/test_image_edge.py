from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.image_edge import (
    ImageEdgeConfig,
    build_image_edge_url,
    load_image_edge_config,
    load_image_edge_config_from_settings,
    pximg_path_from_original_url,
    resolve_public_proxy_url,
    sign_image_edge_path,
    verify_image_edge_signature,
)
from app.core.config import load_settings
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_pximg_path_from_original_url_accepts_pximg_only() -> None:
    assert (
        pximg_path_from_original_url("https://i.pximg.net/img-original/img/2020/01/01/00/00/00/1_p0.jpg")
        == "/img-original/img/2020/01/01/00/00/00/1_p0.jpg"
    )
    assert pximg_path_from_original_url("https://example.com/x.jpg") is None
    assert pximg_path_from_original_url("https://i.pximg.net/../etc/passwd") is None


def test_sign_image_edge_path_matches_worker_contract() -> None:
    cfg = ImageEdgeConfig(
        enabled=True,
        base_urls=["https://img.example.com"],
        secret="test-secret",
        sign_ttl_seconds=3600,
    )
    path = "/img-original/img/2020/01/01/00/00/00/12345_p0.jpg"
    now = 1_700_000_000
    url = sign_image_edge_path(cfg, path, now=now)
    exp = now + 3600
    msg = f"{exp}\n{path}".encode("utf-8")
    expect_sig = _b64url(hmac.new(b"test-secret", msg, hashlib.sha256).digest())
    expect_b64path = _b64url(path.encode("utf-8"))
    assert url == f"https://img.example.com/u/{exp}/{expect_sig}/{expect_b64path}"


def test_load_image_edge_config_requires_all_fields() -> None:
    assert load_image_edge_config({}) is None
    assert load_image_edge_config({"IMAGE_EDGE_ENABLED": "true", "IMAGE_EDGE_SECRET": "x"}) is None
    cfg = load_image_edge_config(
        {
            "IMAGE_EDGE_ENABLED": "true",
            "IMAGE_EDGE_SECRET": "s",
            "IMAGE_EDGE_BASE_URLS": "https://img.example.com,https://img2.example.com",
            "IMAGE_EDGE_SIGN_TTL_SECONDS": "120",
        }
    )
    assert cfg is not None
    assert cfg.primary_base_url == "https://img.example.com"
    assert cfg.base_urls == ["https://img.example.com", "https://img2.example.com"]
    assert cfg.sign_ttl_seconds == 120
    assert cfg.secret_previous == ""
    assert cfg.verify_secrets == ["s"]


def test_dual_secret_verify_accepts_previous_but_signs_primary_only() -> None:
    cfg = load_image_edge_config(
        {
            "IMAGE_EDGE_ENABLED": "true",
            "IMAGE_EDGE_SECRET": "new-secret",
            "IMAGE_EDGE_SECRET_PREVIOUS": "old-secret",
            "IMAGE_EDGE_BASE_URLS": "https://img.example.com",
            "IMAGE_EDGE_SIGN_TTL_SECONDS": "600",
        }
    )
    assert cfg is not None
    assert cfg.secret == "new-secret"
    assert cfg.secret_previous == "old-secret"
    assert cfg.verify_secrets == ["new-secret", "old-secret"]

    path = "/img-original/img/2020/01/01/00/00/00/1_p0.jpg"
    now = 1_700_000_000
    url = sign_image_edge_path(cfg, path, now=now)
    exp = now + 600
    # Signed with primary only.
    primary_sig = _b64url(hmac.new(b"new-secret", f"{exp}\n{path}".encode("utf-8"), hashlib.sha256).digest())
    assert f"/{primary_sig}/" in url
    assert verify_image_edge_signature(cfg, path=path, exp=exp, sig=primary_sig) is True

    old_sig = _b64url(hmac.new(b"old-secret", f"{exp}\n{path}".encode("utf-8"), hashlib.sha256).digest())
    assert verify_image_edge_signature(cfg, path=path, exp=exp, sig=old_sig) is True
    assert verify_image_edge_signature(cfg, path=path, exp=exp, sig="not-a-real-sig") is False


def test_dual_secret_dedupes_identical_previous() -> None:
    cfg = load_image_edge_config(
        {
            "IMAGE_EDGE_ENABLED": "true",
            "IMAGE_EDGE_SECRET": "same",
            "IMAGE_EDGE_SECRET_PREVIOUS": "same",
            "IMAGE_EDGE_BASE_URLS": "https://img.example.com",
        }
    )
    assert cfg is not None
    assert cfg.secret_previous == ""
    assert cfg.verify_secrets == ["same"]

    s = load_settings(
        {
            "APP_ENV": "dev",
            "IMAGE_EDGE_ENABLED": "true",
            "IMAGE_EDGE_SECRET": "same",
            "IMAGE_EDGE_SECRET_PREVIOUS": "same",
            "IMAGE_EDGE_BASE_URLS": "https://img.example.com",
        }
    )
    assert s.image_edge_secret_previous == ""


def test_resolve_public_proxy_url_falls_back_when_disabled() -> None:
    s = load_settings({"APP_ENV": "dev", "IMAGE_EDGE_ENABLED": "false"})
    assert (
        resolve_public_proxy_url(
            settings=s,
            original_url="https://i.pximg.net/img-original/img/a.jpg",
            local_proxy_path="/i/9.jpg",
        )
        == "/i/9.jpg"
    )


def test_resolve_public_proxy_url_signs_when_enabled() -> None:
    s = load_settings(
        {
            "APP_ENV": "dev",
            "IMAGE_EDGE_ENABLED": "true",
            "IMAGE_EDGE_SECRET": "edge-secret",
            "IMAGE_EDGE_BASE_URLS": "https://img.example.com",
            "IMAGE_EDGE_SIGN_TTL_SECONDS": "600",
        }
    )
    cfg = load_image_edge_config_from_settings(s)
    assert cfg is not None
    out = resolve_public_proxy_url(
        settings=s,
        original_url="https://i.pximg.net/img-original/img/2020/01/01/00/00/00/9_p0.png",
        local_proxy_path="/i/9.png",
    )
    assert out.startswith("https://img.example.com/u/")
    assert "/i/9.png" not in out


def test_build_image_edge_url_rejects_non_pximg() -> None:
    cfg = ImageEdgeConfig(
        enabled=True,
        base_urls=["https://img.example.com"],
        secret="s",
        sign_ttl_seconds=60,
    )
    assert build_image_edge_url(cfg, original_url="https://cdn.example/x.jpg") is None


def test_pick_image_edge_base_url_is_sticky() -> None:
    from app.core.image_edge import pick_image_edge_base_url

    cfg = ImageEdgeConfig(
        enabled=True,
        base_urls=["https://img-a.example.com", "https://img-b.example.com", "https://img-c.example.com"],
        secret="s",
        sign_ttl_seconds=60,
    )
    path = "/img-original/img/2020/01/01/00/00/00/1_p0.jpg"
    a = pick_image_edge_base_url(cfg, path)
    b = pick_image_edge_base_url(cfg, path)
    assert a == b
    assert a in cfg.base_urls
    other = pick_image_edge_base_url(cfg, "/img-original/img/2020/01/01/00/00/00/2_p0.jpg")
    assert other in cfg.base_urls


def test_sign_rejects_disallowed_path() -> None:
    cfg = ImageEdgeConfig(
        enabled=True,
        base_urls=["https://img.example.com"],
        secret="s",
        sign_ttl_seconds=60,
    )
    try:
        sign_image_edge_path(cfg, "/etc/passwd.jpg")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_random_image_redirect_prefers_image_edge(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "image_edge_redirect.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("IMAGE_EDGE_ENABLED", "true")
    monkeypatch.setenv("IMAGE_EDGE_SECRET", "edge-secret")
    monkeypatch.setenv("IMAGE_EDGE_BASE_URLS", "https://img.example.com")
    monkeypatch.setenv("IMAGE_EDGE_SIGN_TTL_SECONDS", "3600")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=1001,
                    page_index=0,
                    ext="jpg",
                    original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/1001_p0.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.11,
                    x_restrict=0,
                )
            )
            await session.commit()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/random?format=image&redirect=1", headers={"X-Request-Id": "req_edge_redir"}, follow_redirects=False)
        assert resp.status_code == 302
        loc = resp.headers.get("location") or ""
        assert loc.startswith("https://img.example.com/u/")
        assert resp.headers.get("x-image-edge") == "1"

        local = client.get(
            "/random?format=image&redirect=1&local=1",
            headers={"X-Request-Id": "req_edge_redir_local"},
            follow_redirects=False,
        )
        assert local.status_code == 302
        local_loc = local.headers.get("location") or ""
        assert local_loc.startswith("/i/")
        assert local.headers.get("x-image-edge") != "1"
        assert not local_loc.startswith("https://img.example.com/")


def test_random_simple_json_proxy_prefers_image_edge(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "image_edge_random.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("IMAGE_EDGE_ENABLED", "true")
    monkeypatch.setenv("IMAGE_EDGE_SECRET", "edge-secret")
    monkeypatch.setenv("IMAGE_EDGE_BASE_URLS", "https://img.example.com")
    monkeypatch.setenv("IMAGE_EDGE_SIGN_TTL_SECONDS", "3600")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=999,
                    page_index=0,
                    ext="jpg",
                    original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/999_p0.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.42,
                    x_restrict=0,
                )
            )
            await session.commit()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/random?format=simple_json", headers={"X-Request-Id": "req_edge"})
        assert resp.status_code == 200
        body = resp.json()
        proxy = body["data"]["urls"]["proxy"]
        assert isinstance(proxy, str)
        assert proxy.startswith("https://img.example.com/u/")
        assert body["data"]["urls"]["local"].startswith("/i/")
        assert body["data"]["urls"]["local"].endswith(".jpg")
        parts = proxy.removeprefix("https://img.example.com/u/").split("/")
        assert len(parts) == 3
        exp_s, sig, b64path = parts
        exp = int(exp_s)
        assert exp > int(time.time())
        path = base64.urlsafe_b64decode(b64path + "=" * (-len(b64path) % 4)).decode("utf-8")
        assert path == "/img-original/img/2021/02/03/04/05/06/999_p0.jpg"
        expect = _b64url(hmac.new(b"edge-secret", f"{exp}\n{path}".encode("utf-8"), hashlib.sha256).digest())
        assert sig == expect


def test_proxy_image_route_prefers_image_edge(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "image_edge_i_route.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("IMAGE_EDGE_ENABLED", "true")
    monkeypatch.setenv("IMAGE_EDGE_SECRET", "edge-secret")
    monkeypatch.setenv("IMAGE_EDGE_BASE_URLS", "https://img.example.com")
    monkeypatch.setenv("IMAGE_EDGE_SIGN_TTL_SECONDS", "3600")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=42,
                    page_index=0,
                    ext="png",
                    original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/42_p0.png",
                    proxy_path="/i/1.png",
                    random_key=0.5,
                    x_restrict=0,
                )
            )
            await session.commit()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/i/1.png", headers={"X-Request-Id": "req_i_edge"}, follow_redirects=False)
        assert resp.status_code == 302
        loc = resp.headers.get("location") or ""
        assert loc.startswith("https://img.example.com/u/")
        assert resp.headers.get("x-image-edge") == "1"

        local = client.get("/i/1.png?local=1", headers={"X-Request-Id": "req_i_local"}, follow_redirects=False)
        # Local stream path may fail upstream in unit env; must NOT be edge 302.
        assert local.headers.get("x-image-edge") != "1"
        assert not (local.headers.get("location") or "").startswith("https://img.example.com/")


def test_random_image_default_prefers_edge_redirect(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "image_edge_random_default.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("IMAGE_EDGE_ENABLED", "true")
    monkeypatch.setenv("IMAGE_EDGE_SECRET", "edge-secret")
    monkeypatch.setenv("IMAGE_EDGE_BASE_URLS", "https://img.example.com")
    monkeypatch.setenv("IMAGE_EDGE_SIGN_TTL_SECONDS", "3600")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=77,
                    page_index=0,
                    ext="jpg",
                    original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/77_p0.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.33,
                    x_restrict=0,
                )
            )
            await session.commit()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/random?format=image", headers={"X-Request-Id": "req_edge_img"}, follow_redirects=False)
        assert resp.status_code == 302
        assert (resp.headers.get("location") or "").startswith("https://img.example.com/u/")
        assert resp.headers.get("x-image-edge") == "1"

        forced = client.get(
            "/random?format=image&local=1",
            headers={"X-Request-Id": "req_edge_local"},
            follow_redirects=False,
        )
        assert forced.headers.get("x-image-edge") != "1"
