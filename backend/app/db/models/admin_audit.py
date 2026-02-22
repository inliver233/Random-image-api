from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class AdminAudit(Base):
    __tablename__ = "admin_audit"
    __table_args__ = (
        sa.Index("idx_admin_audit_created_at", "created_at"),
        sa.Index("idx_admin_audit_action", "action"),
        sa.Index("idx_admin_audit_resource", "resource"),
        sa.Index("idx_admin_audit_record_id", "record_id"),
    )

    id: Mapped[int] = mapped_column(sa.Integer(), primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
    )
    actor: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    action: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    resource: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    record_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    request_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    ip: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    detail_json: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

