from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cryptography.fernet import Fernet

from app.db.models.base import Base
from app.db.models.imports import Import
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker
from app.main import create_app
from app.worker import build_default_dispatcher, poll_and_execute_jobs


def test_worker_claims_and_executes_pending_job(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker_jobs_loop.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    async def _seed_and_run() -> tuple[str, int, int]:
        Session = create_sessionmaker(app.state.engine)

        async with Session() as session:
            imp = Import(created_by="test", source="manual")
            session.add(imp)
            await session.flush()
            import_id = int(imp.id)

            text = "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg\n"
            job = JobRow(
                type="import_images",
                status="pending",
                payload_json=json.dumps(
                    {"import_id": import_id, "text": text},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                ref_type="import",
                ref_id=str(import_id),
            )
            session.add(job)
            await session.flush()
            job_id = int(job.id)
            await session.commit()

        dispatcher = build_default_dispatcher(app.state.engine)
        ran = await poll_and_execute_jobs(app.state.engine, dispatcher, worker_id="test-worker", max_jobs=5)

        async with Session() as session:
            row = await session.get(JobRow, job_id)
            assert row is not None
            imp2 = await session.get(Import, import_id)
            assert imp2 is not None
            return (str(row.status), int(imp2.success or 0), int(ran))

    status, success, ran = asyncio.run(_seed_and_run())
    assert ran >= 1
    assert status == "completed"
    assert success == 1

