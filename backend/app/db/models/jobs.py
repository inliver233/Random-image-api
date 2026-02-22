from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


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
    )

    id: Mapped[int] = mapped_column(sa.Integer(), primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
    )
    updated_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
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

