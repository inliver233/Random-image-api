from __future__ import annotations

import asyncio
import json
from pathlib import Path

import sqlalchemy as sa

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.imports import Import
from app.db.models.jobs import JobRow
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker
from app.jobs.claim import claim_next_job
from app.jobs.dispatch import JobDispatcher
from app.jobs.executor import execute_claimed_job
from app.jobs.handlers.import_images import build_import_images_handler


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_job_handler_import_images_happy_path_and_enqueues_hydrate(tmp_path: Path) -> None:
    db_path = tmp_path / "handler_import_images.db"
    engine = create_engine(_sqlite_url(db_path))

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            imp = Import(created_by="admin", source="manual")
            session.add(imp)
            await session.commit()
            await session.refresh(imp)

            payload = {
                "import_id": int(imp.id),
                "hydrate_on_import": True,
                "text_lines": [
                    "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/111_p0.jpg",
                    "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/111_p1.jpg",
                    "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/111_p0.jpg",
                    "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/222_p0.png",
                    "https://example.com/not_pximg.jpg",
                ],
            }
            job = JobRow(
                type="import_images",
                status="pending",
                payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ref_type="import",
                ref_id=str(int(imp.id)),
            )
            session.add(job)
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register("import_images", build_import_images_handler(engine))

        claimed = await claim_next_job(engine, worker_id="w1")
        assert claimed is not None
        assert claimed["type"] == "import_images"
        assert claimed["status"] == "running"

        transition = await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id="w1")
        assert transition is not None
        assert transition.status.value == "completed"

        async with Session() as session:
            imp2 = await session.get(Import, int(payload["import_id"]))
            assert imp2 is not None
            assert int(imp2.total) == 5
            assert int(imp2.accepted) == 3
            assert int(imp2.success) == 3
            assert int(imp2.failed) == 1

            detail = json.loads(imp2.detail_json or "{}")
            assert detail["deduped"] == 1
            assert len(detail["errors"]) == 1

            images = (
                (await session.execute(sa.select(Image).order_by(Image.id.asc()))).scalars().all()
            )
            assert len(images) == 3
            for img in images:
                assert img.proxy_path.startswith("/i/")
                assert img.created_import_id == int(payload["import_id"])

            hydrate_jobs = (
                (
                    await session.execute(
                        sa.select(JobRow)
                        .where(JobRow.type == "hydrate_metadata")
                        .where(JobRow.ref_type == "import")
                        .order_by(JobRow.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            assert len(hydrate_jobs) == 2
            ref_ids = {j.ref_id for j in hydrate_jobs}
            assert ref_ids == {f"{imp2.id}:111", f"{imp2.id}:222"}

            import_job = (
                (await session.execute(sa.select(JobRow).where(JobRow.type == "import_images")))
                .scalars()
                .first()
            )
            assert import_job is not None
            assert import_job.status == "completed"
            assert import_job.locked_by is None
            assert import_job.locked_at is None

        await engine.dispose()

    asyncio.run(_run())


def test_job_handler_import_images_retries_on_sqlite_busy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", "100")
    monkeypatch.setenv("SQLITE_BUSY_RETRIES", "20")

    db_path = tmp_path / "handler_import_images_busy.db"
    engine = create_engine(_sqlite_url(db_path))

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            imp = Import(created_by="admin", source="manual")
            session.add(imp)
            await session.commit()
            await session.refresh(imp)

            payload = {
                "import_id": int(imp.id),
                "hydrate_on_import": False,
                "text_lines": [
                    "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/111_p0.jpg",
                    "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/222_p0.png",
                ],
            }
            job = JobRow(
                type="import_images",
                status="pending",
                payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ref_type="import",
                ref_id=str(int(imp.id)),
            )
            session.add(job)
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register("import_images", build_import_images_handler(engine))

        claimed = await claim_next_job(engine, worker_id="w1")
        assert claimed is not None
        assert claimed["type"] == "import_images"

        lock_acquired = asyncio.Event()

        async def _hold_write_lock() -> None:
            async with engine.connect() as conn:
                await conn.exec_driver_sql("BEGIN IMMEDIATE")
                await conn.exec_driver_sql(
                    "UPDATE imports SET source=source WHERE id=:id",
                    {"id": int(payload["import_id"])},
                )
                lock_acquired.set()
                await asyncio.sleep(0.6)
                await conn.exec_driver_sql("COMMIT")

        async def _run_job() -> None:
            await lock_acquired.wait()
            transition = await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id="w1")
            assert transition is not None
            assert transition.status.value == "completed"

        await asyncio.gather(_hold_write_lock(), _run_job())

        async with Session() as session:
            imgs = ((await session.execute(sa.select(Image))).scalars().all())
            assert len(imgs) == 2
            for img in imgs:
                assert img.proxy_path.startswith("/i/")

        await engine.dispose()

    asyncio.run(_run())


def test_job_handler_import_images_supports_file_ref(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "handler_import_images_file_ref.db"
    db_url = _sqlite_url(db_path)
    engine = create_engine(db_url)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    payload_dir = tmp_path / "imports_payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    payload_path = payload_dir / "urls.txt"
    payload_path.write_text(
        "\n".join(
            [
                "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/111_p0.jpg",
                "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/111_p0.jpg",
                "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/222_p0.png",
                "https://example.com/not_pximg.jpg",
            ]
        ),
        encoding="utf-8",
    )

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            imp = Import(created_by="admin", source="manual")
            session.add(imp)
            await session.commit()
            await session.refresh(imp)

            payload = {
                "import_id": int(imp.id),
                "hydrate_on_import": False,
                "file_ref": "imports_payloads/urls.txt",
            }
            session.add(
                JobRow(
                    type="import_images",
                    status="pending",
                    payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    ref_type="import",
                    ref_id=str(int(imp.id)),
                )
            )
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register("import_images", build_import_images_handler(engine))

        claimed = await claim_next_job(engine, worker_id="w1")
        assert claimed is not None

        transition = await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id="w1")
        assert transition is not None
        assert transition.status.value == "completed"

        async with Session() as session:
            imp2 = await session.get(Import, int(imp.id))
            assert imp2 is not None
            assert int(imp2.total) == 4
            assert int(imp2.accepted) == 2
            assert int(imp2.success) == 2
            assert int(imp2.failed) == 1

            images = ((await session.execute(sa.select(Image))).scalars().all())
            assert len(images) == 2

        assert payload_path.exists() is False

        await engine.dispose()

    asyncio.run(_run())


def test_job_handler_import_images_supports_pixiv_batch_downloader_json(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "handler_import_images_pbd_json.db"
    db_url = _sqlite_url(db_path)
    engine = create_engine(db_url)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    payload_dir = tmp_path / "imports_payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    payload_path = payload_dir / "pbd.json"
    payload_path.write_text(
        json.dumps(
            [
                {
                    "id": "12345678_p0",
                    "index": 0,
                    "original": "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg",
                    "fullWidth": 1000,
                    "fullHeight": 1500,
                    "xRestrict": 1,
                    "aiType": 2,
                    "type": 0,
                    "userId": "42",
                    "user": "alice",
                    "title": "hello",
                    "date": "2023-01-01T00:00:00+09:00",
                    "bmk": 12,
                    "viewCount": 34,
                    "commentCount": 5,
                    "tags": ["loli", "cute", "loli"],
                }
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            imp = Import(created_by="admin", source="pixiv_batch_downloader")
            session.add(imp)
            await session.commit()
            await session.refresh(imp)

            payload = {
                "import_id": int(imp.id),
                "hydrate_on_import": True,  # should be ignored for pbd json
                "input_format": "pixiv_batch_downloader_json",
                "file_ref": "imports_payloads/pbd.json",
            }
            session.add(
                JobRow(
                    type="import_images",
                    status="pending",
                    payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    ref_type="import",
                    ref_id=str(int(imp.id)),
                )
            )
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register("import_images", build_import_images_handler(engine))

        claimed = await claim_next_job(engine, worker_id="w1")
        assert claimed is not None

        transition = await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id="w1")
        assert transition is not None
        assert transition.status.value == "completed"

        async with Session() as session:
            imgs = ((await session.execute(sa.select(Image))).scalars().all())
            assert len(imgs) == 1
            img = imgs[0]
            assert img.width == 1000
            assert img.height == 1500
            assert img.orientation == 1
            assert img.aspect_ratio is not None
            assert abs(float(img.aspect_ratio) - (1000.0 / 1500.0)) < 1e-6
            assert img.x_restrict == 1
            assert img.ai_type == 1
            assert img.illust_type == 0
            assert img.user_id == 42
            assert img.user_name == "alice"
            assert img.title == "hello"
            assert img.created_at_pixiv == "2022-12-31T15:00:00Z"
            assert img.bookmark_count == 12
            assert img.view_count == 34
            assert img.comment_count == 5

            tags = ((await session.execute(sa.select(Tag).order_by(Tag.name.asc()))).scalars().all())
            assert [t.name for t in tags] == ["cute", "loli"]

            links = ((await session.execute(sa.select(ImageTag))).scalars().all())
            assert len(links) == 2

            hydrate_jobs = (
                (
                    await session.execute(
                        sa.select(JobRow).where(JobRow.type == "hydrate_metadata")
                    )
                )
                .scalars()
                .all()
            )
            assert hydrate_jobs == []

        assert payload_path.exists() is False

        await engine.dispose()

    asyncio.run(_run())


def test_job_handler_import_images_missing_import_id_moves_to_dlq(tmp_path: Path) -> None:
    db_path = tmp_path / "handler_import_images_missing_import_id.db"
    engine = create_engine(_sqlite_url(db_path))

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add(
                JobRow(
                    type="import_images",
                    status="pending",
                    payload_json=json.dumps({}, ensure_ascii=False, separators=(",", ":")),
                )
            )
            await session.commit()

        dispatcher = JobDispatcher()
        dispatcher.register("import_images", build_import_images_handler(engine))

        claimed = await claim_next_job(engine, worker_id="w1")
        assert claimed is not None

        transition = await execute_claimed_job(engine, dispatcher, job_row=claimed, worker_id="w1")
        assert transition is not None
        assert transition.status.value == "dlq"
        assert transition.attempt == 1
        assert transition.run_after is None
        assert transition.last_error

        async with Session() as session:
            row = (
                (
                    await session.execute(
                        sa.select(JobRow).where(JobRow.id == int(claimed["id"]))
                    )
                )
                .scalars()
                .one()
            )
            assert row.status == "dlq"
            assert int(row.attempt) == 1

        await engine.dispose()

    asyncio.run(_run())
