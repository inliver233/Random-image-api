from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class TokenProxyBinding(Base):
    __tablename__ = "token_proxy_bindings"
    __table_args__ = (
        sa.UniqueConstraint("token_id", "pool_id", name="uq_token_pool"),
        sa.Index("idx_tpb_pool", "pool_id"),
        sa.Index("idx_tpb_primary", "primary_proxy_id"),
        sa.Index("idx_tpb_override", "override_proxy_id"),
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

    token_id: Mapped[int] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("pixiv_tokens.id", ondelete="CASCADE"),
        nullable=False,
    )
    pool_id: Mapped[int] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("proxy_pools.id", ondelete="CASCADE"),
        nullable=False,
    )
    primary_proxy_id: Mapped[int] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("proxy_endpoints.id", ondelete="RESTRICT"),
        nullable=False,
    )
    override_proxy_id: Mapped[int | None] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("proxy_endpoints.id", ondelete="SET NULL"),
        nullable=True,
    )
    override_expires_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

