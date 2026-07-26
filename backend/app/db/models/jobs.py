from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base
from app.db.utc_text_now import UtcNow


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending','running','paused','canceled','completed','failed','dlq')",
            name="ck_jobs_status",
        ),
        sa.Index("idx_jobs_status_priority", "status", "priority", "id"),
        sa.Index("idx_jobs_run_after", "run_after"),
        sa.Index("idx_jobs_ref", "ref_type", "ref_id"),
        # Prevent concurrent opportunistic hydrate enqueue races for the same illust.
        # The predicate must exist for BOTH dialects: without postgresql_where the
        # compiled PG metadata silently became an unconditional global unique index.
        sa.Index(
            "uq_jobs_active_opportunistic_hydrate",
            "type",
            "ref_type",
            "ref_id",
            unique=True,
            sqlite_where=sa.text(
                "type = 'hydrate_metadata' AND ref_type = 'opportunistic_hydrate' AND status IN ('pending','running')"
            ),
            postgresql_where=sa.text(
                "type = 'hydrate_metadata' AND ref_type = 'opportunistic_hydrate' AND status IN ('pending','running')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(sa.Integer(), primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=UtcNow(),
    )
    updated_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=UtcNow(),
    )

    type: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    priority: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    run_after: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

    attempt: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    max_attempts: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("3"))

    payload_json: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    last_error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

    locked_by: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    locked_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

    ref_type: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

