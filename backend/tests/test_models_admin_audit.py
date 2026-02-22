from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.admin_audit import AdminAudit
from app.db.models.base import Base
from app.db.session import create_sessionmaker


def test_models_admin_audit_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_admin_audit.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            row = AdminAudit(actor="admin", action="login", resource="auth", record_id="1", request_id="req_test")
            session.add(row)
            await session.commit()
            await session.refresh(row)
            assert row.id == 1

        async with Session() as session:
            fetched = (await session.execute(select(AdminAudit))).scalars().first()
            assert fetched is not None
            assert fetched.action == "login"

        await engine.dispose()

    asyncio.run(_run())

