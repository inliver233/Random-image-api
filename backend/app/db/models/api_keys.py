from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        sa.UniqueConstraint("name", name="uq_api_keys_name"),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_api_keys_enabled"),
        sa.Index("idx_api_keys_enabled", "enabled"),
        sa.Index("idx_api_keys_created_at", "created_at"),
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

    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

    key_hash: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    hint: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("''"))

    enabled: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))
    last_used_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

