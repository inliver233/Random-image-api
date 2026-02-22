from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class Import(Base):
    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(sa.Integer(), primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
    )
    created_by: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    source: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

    total: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    accepted: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    success: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    failed: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))

    detail_json: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

