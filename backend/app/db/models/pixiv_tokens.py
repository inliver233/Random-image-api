from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class PixivToken(Base):
    __tablename__ = "pixiv_tokens"
    __table_args__ = (
        sa.CheckConstraint("enabled IN (0,1)", name="ck_pixiv_tokens_enabled"),
        sa.Index("idx_pixiv_tokens_enabled", "enabled"),
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

    label: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    enabled: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))

    refresh_token_enc: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    refresh_token_masked: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    weight: Mapped[float] = mapped_column(sa.Float(), nullable=False, server_default=sa.text("1.0"))

    error_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    backoff_until: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_ok_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_fail_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_error_msg: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

