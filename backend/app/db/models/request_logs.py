from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class RequestLog(Base):
    __tablename__ = "request_logs"
    __table_args__ = (
        sa.Index("idx_request_logs_created_at", "created_at"),
        sa.Index("idx_request_logs_route", "route"),
        sa.Index("idx_request_logs_status", "status"),
    )

    id: Mapped[int] = mapped_column(sa.Integer(), primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
    )
    request_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    method: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    route: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    status: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    duration_ms: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    ip: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    sample_rate: Mapped[float | None] = mapped_column(sa.Float(), nullable=True)

