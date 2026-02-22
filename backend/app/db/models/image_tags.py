from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class ImageTag(Base):
    __tablename__ = "image_tags"
    __table_args__ = (sa.Index("idx_image_tags_tag_image", "tag_id", "image_id"),)

    image_id: Mapped[int] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("images.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )

