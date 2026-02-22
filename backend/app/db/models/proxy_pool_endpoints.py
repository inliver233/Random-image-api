from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class ProxyPoolEndpoint(Base):
    __tablename__ = "proxy_pool_endpoints"
    __table_args__ = (
        sa.Index("idx_ppe_pool_enabled", "pool_id", "enabled"),
        sa.Index("idx_ppe_endpoint_pool", "endpoint_id", "pool_id"),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_ppe_enabled"),
    )

    pool_id: Mapped[int] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("proxy_pools.id", ondelete="CASCADE"),
        primary_key=True,
    )
    endpoint_id: Mapped[int] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("proxy_endpoints.id", ondelete="CASCADE"),
        primary_key=True,
    )

    enabled: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))
    weight: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))

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

