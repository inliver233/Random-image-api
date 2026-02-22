from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import sqlalchemy as sa
from cryptography.fernet import Fernet

from app.core.crypto import FieldEncryptor
from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.hydration_runs import HydrationRun
from app.db.models.images import Image
from app.db.models.jobs import JobRow
from app.db.models.pixiv_tokens import PixivToken
from app.db.session import create_sessionmaker
from app.jobs.claim import claim_next_job
from app.jobs.dispatch import JobDispatcher
from app.jobs.executor import execute_claimed_job
from app.jobs.handlers.hydrate_metadata import build_hydrate_metadata_handler


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_hydration_run_backfill_job_processes_missing_metadata(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "handler_hydration_run_backfill.db"
    engine = create_engine(_sqlite_url(db_path))

    field_key = Fernet.generate_key().decode("ascii")
    encryptor = FieldEncryptor.from_key(field_key)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)
    monkeypatch.setenv("PIXIV_OAUTH_CLIENT_ID", "cid_test")
    monkeypatch.setenv("PIXIV_OAUTH_CLIENT_SECRET", "csec_test")
    monkeypatch.setenv("PIXIV_OAUTH_HASH_SECRET", "hsec_test")

    refresh_token = "rt_old"
    access_token = "at_test"
    illust_id = 111

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
                        "refresh_token": "rt_rotated",
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
                        "id": illust_id,
                        "title": "title_test",
                        "user": {"id": 999, "name": "user_test"},
                        "x_restrict": 0,
                        "illust_ai_type": 1,
                        "width": 1200,
                        "height": 800,
                        "create_date": "2020-01-01T00:00:00+00:00",
                        "page_count": 1,
                        "tags": [
                            {"name": "tag1", "translated_name": "t1"},
                            {"name": "tag2"},
                        ],
                        "meta_single_page": {
                            "original_image_url": "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/111_p0.jpg"
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
            token = PixivToken(
                label="acc1",
                enabled=1,
                refresh_token_enc=encryptor.encrypt_text(refresh_token),
                refresh_token_masked="***",
                weight=1.0,
            )
            session.add(token)
            await session.flush()

            img = Image(
                illust_id=int(illust_id),
                page_index=0,
                ext="jpg",
                original_url="https://i.pximg.net/img-original/img/2020/01/01/00/00/00/111_p0.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.123,
                width=None,
                height=None,
                aspect_ratio=None,
                orientation=None,
                x_restrict=None,
                ai_type=None,
                user_id=None,
                user_name=None,
                title=None,
                created_at_pixiv=None,
                status=1,
                fail_count=0,
                last_fail_at=None,
                last_ok_at=None,
                last_error_code=None,
                last_error_msg=None,
                created_import_id=None,
            )
            session.add(img)
            await session.flush()

            run = HydrationRun(type="backfill", status="pending", criteria_json=json.dumps({"missing": ["r18"]}), cursor_json="{}")
            session.add(run)
            await session.flush()

            job = JobRow(
                type="hydrate_metadata",
                status="pending",
                payload_json=json.dumps({"hydration_run_id": int(run.id), "criteria": {"missing": ["r18"]}}),
                last_error=None,
                priority=0,
                run_after=None,
                attempt=0,
                max_attempts=3,
                locked_by=None,
                locked_at=None,
                ref_type="hydration_run",
                ref_id=str(int(run.id)),
            )
            session.add(job)
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register("hydrate_metadata", build_hydrate_metadata_handler(engine, transport=transport))

        claimed = await claim_next_job(engine, worker_id="test-worker")
        assert claimed is not None
        assert str(claimed.get("type")) == "hydrate_metadata"

        await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id="test-worker")

        async with Session() as session:
            run2 = await session.get(HydrationRun, int(run.id))
            assert run2 is not None
            assert run2.status == "completed"
            assert int(run2.processed or 0) == 1
            assert int(run2.success or 0) == 1
            assert int(run2.failed or 0) == 0
            assert run2.started_at is not None
            assert run2.finished_at is not None

            job2 = await session.get(JobRow, int(job.id))
            assert job2 is not None
            assert job2.status == "completed"

            img2 = (
                (await session.execute(sa.select(Image).where(Image.illust_id == int(illust_id), Image.page_index == 0)))
                .scalars()
                .first()
            )
            assert img2 is not None
            assert img2.width == 1200
            assert img2.height == 800
            assert img2.x_restrict == 0
            assert img2.user_id == 999
            assert (img2.title or "") == "title_test"
            assert img2.created_at_pixiv is not None

    asyncio.run(_run())

