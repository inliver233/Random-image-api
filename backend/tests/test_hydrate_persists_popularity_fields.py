from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.crypto import FieldEncryptor
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.models.jobs import JobRow
from app.db.models.pixiv_tokens import PixivToken
from app.db.session import create_sessionmaker
from app.jobs.claim import claim_next_job
from app.jobs.dispatch import JobDispatcher
from app.jobs.executor import execute_claimed_job
from app.jobs.handlers.hydrate_metadata import build_hydrate_metadata_handler
from app.main import create_app


def test_hydrate_persists_popularity_fields_and_public_api_outputs(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "hydrate_popularity.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")
    encryptor = FieldEncryptor.from_key(field_key)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)
    monkeypatch.setenv("PIXIV_OAUTH_CLIENT_ID", "cid_test")
    monkeypatch.setenv("PIXIV_OAUTH_CLIENT_SECRET", "csec_test")
    monkeypatch.setenv("PIXIV_OAUTH_HASH_SECRET", "hsec_test")

    refresh_token = "rt_test"
    access_token = "at_test"

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == "https://oauth.secure.pixiv.net/auth/token":
            body = (req.content or b"").decode("utf-8")
            assert f"refresh_token={refresh_token}" in body
            return httpx.Response(
                200,
                json={
                    "response": {
                        "access_token": access_token,
                        "token_type": "bearer",
                        "expires_in": 3600,
                        "refresh_token": refresh_token,
                        "scope": "",
                        "user": {"id": 123},
                    }
                },
            )

        if str(req.url).startswith("https://app-api.pixiv.net/v1/illust/detail"):
            assert req.headers.get("Authorization") == f"Bearer {access_token}"
            return httpx.Response(
                200,
                json={
                    "illust": {
                        "id": 111,
                        "title": "title_test",
                        "user": {"id": 999, "name": "user_test"},
                        "x_restrict": 0,
                        "illust_type": 0,
                        "illust_ai_type": 1,
                        "width": 1200,
                        "height": 800,
                        "create_date": "2020-01-01T00:00:00+00:00",
                        "page_count": 2,
                        "total_bookmarks": 123,
                        "total_view": 456,
                        "total_comments": 7,
                        "tags": [{"name": "tag1"}],
                        "meta_pages": [
                            {
                                "image_urls": {
                                    "original": "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/111_p0.jpg"
                                }
                            },
                            {
                                "image_urls": {
                                    "original": "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/111_p1.png"
                                }
                            },
                        ],
                    }
                },
            )

        return httpx.Response(500, text="unexpected")

    transport = httpx.MockTransport(handler)

    app = create_app()
    image0_id: int | None = None

    async def _run() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)

        async with Session() as session:
            token_row = PixivToken(
                label="acc1",
                enabled=1,
                refresh_token_enc=encryptor.encrypt_text(refresh_token),
                refresh_token_masked="***",
                weight=1.0,
            )
            session.add(token_row)

            job = JobRow(
                type="hydrate_metadata",
                status="pending",
                payload_json=json.dumps({"illust_id": 111}, ensure_ascii=False, separators=(",", ":")),
            )
            session.add(job)
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register("hydrate_metadata", build_hydrate_metadata_handler(app.state.engine, transport=transport))

        claimed = await claim_next_job(app.state.engine, worker_id="w1")
        assert claimed is not None

        transition = await execute_claimed_job(app.state.engine, dispatcher, job_row=claimed, worker_id="w1")
        assert transition is not None
        assert transition.status.value == "completed"

        async with Session() as session:
            images = (
                (
                    await session.execute(
                        sa.select(Image).where(Image.illust_id == 111).order_by(Image.page_index.asc())
                    )
                )
                .scalars()
                .all()
            )
            assert len(images) == 2
            assert images[0].page_index == 0
            assert images[1].page_index == 1
            for img in images:
                assert img.bookmark_count == 123
                assert img.view_count == 456
                assert img.comment_count == 7
                assert img.illust_type == 0

            nonlocal image0_id
            image0_id = int(images[0].id)

        await app.state.engine.dispose()

    asyncio.run(_run())
    assert image0_id is not None

    with TestClient(app) as client:
        resp = client.get("/images", params={"r18": 2}, headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"
        found = [
            it
            for it in body["items"]
            if int(it["illust_id"]) == 111 and int(it["page_index"]) == 0
        ]
        assert found
        item0 = found[0]
        assert item0["bookmark_count"] == 123
        assert item0["view_count"] == 456
        assert item0["comment_count"] == 7

        resp2 = client.get(f"/images/{image0_id}", headers={"X-Request-Id": "req_test2"})
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["ok"] is True
        assert body2["request_id"] == "req_test2"
        item = body2["item"]["image"]
        assert item["bookmark_count"] == 123
        assert item["view_count"] == 456
        assert item["comment_count"] == 7
