from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class ProxyPool(Base):
    __tablename__ = "proxy_pools"
    __table_args__ = (
        sa.UniqueConstraint("name", name="uq_proxy_pools_name"),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_proxy_pools_enabled"),
        sa.Index("idx_proxy_pools_enabled", "enabled"),
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
    enabled: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))

