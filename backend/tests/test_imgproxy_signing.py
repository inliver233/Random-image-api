from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.imgproxy import ImgproxyConfig, sign_path
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_imgproxy_signature_matches_docs_example() -> None:
    cfg = ImgproxyConfig(
        base_url="http://imgproxy.example.com",
        key=bytes.fromhex("736563726574"),
        salt=bytes.fromhex("68656C6C6F"),
        max_dim=2048,
        default_options="rs:fit:2048:2048",
        url_chunk_size=16,
    )

    path = "/rs:fill:300:400:0/g:sm/aHR0cDovL2V4YW1w/bGUuY29tL2ltYWdl/cy9jdXJpb3NpdHku/anBn.png"
    assert sign_path(cfg, path) == "oKfUtW34Dvo2BGQehJFR4Nr0_rIjOtdtzJ3QFsUcXH8"


def test_random_json_imgproxy_uses_proxy_source_when_origin_hidden(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "imgproxy_random.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("IMGPROXY_BASE_URL", "http://imgproxy.example.com")
    monkeypatch.setenv("IMGPROXY_KEY", "736563726574")
    monkeypatch.setenv("IMGPROXY_SALT", "68656C6C6F")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=123,
                    page_index=0,
                    ext="jpg",
                    original_url="https://example.test/origin.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.5,
                    x_restrict=0,
                )
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    origin_b64 = base64.urlsafe_b64encode(b"https://example.test/origin.jpg").rstrip(b"=").decode("ascii")
    origin_b64_chunked = "/".join(origin_b64[i : i + 16] for i in range(0, len(origin_b64), 16))

    with TestClient(app) as client:
        resp = client.get("/random?format=json", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["urls"]["origin"] is None
        imgproxy_url = body["data"]["urls"]["imgproxy"]
        assert isinstance(imgproxy_url, str)
        assert imgproxy_url.startswith("http://imgproxy.example.com/")
        assert "/rs:fit:" in imgproxy_url
        assert origin_b64 not in imgproxy_url
        assert origin_b64_chunked not in imgproxy_url

        image_id = body["data"]["image"]["id"]
        proxy_source = f"http://testserver/i/{image_id}.jpg".encode("utf-8")
        proxy_b64 = base64.urlsafe_b64encode(proxy_source).rstrip(b"=").decode("ascii")
        proxy_b64_chunked = "/".join(proxy_b64[i : i + 16] for i in range(0, len(proxy_b64), 16))
        assert proxy_b64_chunked in imgproxy_url
