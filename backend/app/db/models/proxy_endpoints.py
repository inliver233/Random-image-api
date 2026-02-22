from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class ProxyEndpoint(Base):
    __tablename__ = "proxy_endpoints"
    __table_args__ = (
        sa.UniqueConstraint("scheme", "host", "port", "username", name="uq_proxy_identity"),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_proxy_enabled"),
        sa.Index("idx_proxy_endpoints_enabled", "enabled"),
        sa.Index("idx_proxy_endpoints_source", "source"),
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

    scheme: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    host: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    port: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    username: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("''"))
    password_enc: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("''"))

    enabled: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))
    source: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("'manual'"))
    source_ref: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

    last_latency_ms: Mapped[float | None] = mapped_column(sa.Float(), nullable=True)
    last_ok_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_fail_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    success_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    failure_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    blacklisted_until: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

