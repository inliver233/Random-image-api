from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import sqlalchemy as sa
from cryptography.fernet import Fernet

from app.core.crypto import FieldEncryptor
from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.models.jobs import JobRow
from app.db.models.pixiv_tokens import PixivToken
from app.db.session import create_sessionmaker
from app.jobs.claim import claim_next_job
from app.jobs.dispatch import JobDispatcher
from app.jobs.executor import execute_claimed_job
from app.jobs.handlers.heal_url import build_heal_url_handler


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_job_handler_heal_url_marks_broken_images_active_after_hydrate(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "handler_heal_url.db"
    engine = create_engine(_sqlite_url(db_path))

    field_key = Fernet.generate_key().decode("ascii")
    encryptor = FieldEncryptor.from_key(field_key)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)
    monkeypatch.setenv("PIXIV_OAUTH_CLIENT_ID", "cid_test")
    monkeypatch.setenv("PIXIV_OAUTH_CLIENT_SECRET", "csec_test")
    monkeypatch.setenv("PIXIV_OAUTH_HASH_SECRET", "hsec_test")

    refresh_token = "rt_old"

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == "https://oauth.secure.pixiv.net/auth/token":
            return httpx.Response(
                200,
                json={
                    "response": {
                        "access_token": "at_test",
                        "token_type": "bearer",
                        "expires_in": 3600,
                        "refresh_token": None,
                        "scope": "",
                        "user": {"id": 123},
                    }
                },
            )
        if str(req.url).startswith("https://app-api.pixiv.net/v1/illust/detail"):
            return httpx.Response(
                200,
                json={
                    "illust": {
                        "id": 333,
                        "title": "title_test",
                        "user": {"id": 1, "name": "u"},
                        "x_restrict": 0,
                        "illust_ai_type": 0,
                        "width": 100,
                        "height": 200,
                        "create_date": "2020-01-01T00:00:00+00:00",
                        "page_count": 1,
                        "tags": [],
                        "meta_single_page": {
                            "original_image_url": "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/333_p0.jpg"
                        },
                    }
                },
            )
        return httpx.Response(500, text="unexpected")

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)

        async with Session() as session:
            session.add(
                PixivToken(
                    label="acc1",
                    enabled=1,
                    refresh_token_enc=encryptor.encrypt_text(refresh_token),
                    refresh_token_masked="***",
                    weight=1.0,
                )
            )

            img = Image(
                illust_id=333,
                page_index=0,
                ext="jpg",
                original_url="https://i.pximg.net/img-original/old/333_p0.jpg",
                proxy_path="",
                random_key=0.5,
                status=3,
                last_error_code="UPSTREAM_404",
                last_error_msg="old_error",
            )
            session.add(img)
            await session.flush()
            img.proxy_path = f"/i/{int(img.id)}.jpg"

            session.add(
                JobRow(
                    type="heal_url",
                    status="pending",
                    payload_json=json.dumps({"illust_id": 333, "trigger": "upstream_404"}, ensure_ascii=False, separators=(",", ":")),
                )
            )
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register("heal_url", build_heal_url_handler(engine, transport=transport))

        claimed = await claim_next_job(engine, worker_id="w1")
        assert claimed is not None
        assert claimed["type"] == "heal_url"

        transition = await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id="w1")
        assert transition is not None
        assert transition.status.value == "completed"

        async with Session() as session:
            img2 = (
                (
                    await session.execute(
                        sa.select(Image).where(Image.illust_id == 333).where(Image.page_index == 0)
                    )
                )
                .scalars()
                .one()
            )
            assert img2.status == 1
            assert img2.last_ok_at is not None and img2.last_ok_at
            assert img2.last_error_code is None
            assert img2.last_error_msg is None
            assert img2.original_url.endswith("333_p0.jpg")
            assert img2.proxy_path == f"/i/{int(img2.id)}.jpg"

        await engine.dispose()

    asyncio.run(_run())

