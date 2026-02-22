from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class HydrationRun(Base):
    __tablename__ = "hydration_runs"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending','running','paused','canceled','completed','failed')",
            name="ck_hr_status",
        ),
        sa.Index("idx_hr_status_updated", "status", "updated_at"),
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
    criteria_json: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    cursor_json: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    total: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    processed: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    success: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    failed: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    started_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

