from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class Image(Base):
    __tablename__ = "images"
    __table_args__ = (
        sa.UniqueConstraint("illust_id", "page_index", name="uq_images_illust_page"),
        sa.CheckConstraint("status IN (1,2,3,4)", name="ck_images_status"),
        sa.CheckConstraint("random_key >= 0.0 AND random_key < 1.0", name="ck_images_random_key"),
        sa.Index(
            "idx_images_filter",
            "status",
            "x_restrict",
            "orientation",
            "width",
            "height",
            "random_key",
        ),
        sa.Index("idx_images_illust_type_random", "status", "illust_type", "random_key"),
        sa.Index("idx_images_user_random", "status", "user_id", "random_key"),
        sa.Index("idx_images_created_at_pixiv", "created_at_pixiv"),
        sa.Index("idx_images_created_import_id", "created_import_id"),
    )

    id: Mapped[int] = mapped_column(sa.Integer(), primary_key=True, autoincrement=True)

    illust_id: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    page_index: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    ext: Mapped[str] = mapped_column(sa.Text(), nullable=False)

    original_url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    proxy_path: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    random_key: Mapped[float] = mapped_column(sa.Float(), nullable=False)

    width: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    height: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    aspect_ratio: Mapped[float | None] = mapped_column(sa.Float(), nullable=True)
    orientation: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    x_restrict: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    ai_type: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    illust_type: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    user_id: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    user_name: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    title: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_at_pixiv: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

    bookmark_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    view_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    comment_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)

    status: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))
    fail_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    last_fail_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_ok_at: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_error_msg: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

    created_import_id: Mapped[int | None] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("imports.id", ondelete="SET NULL"),
        nullable=True,
    )

    added_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
    )
    updated_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
    )
