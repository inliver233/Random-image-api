from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base
from app.db.utc_text_now import UtcNow


class ImportImage(Base):
    """Immutable import-to-image membership used to scope rollback ownership."""

    __tablename__ = "import_images"
    __table_args__ = (
        sa.CheckConstraint(
            "previous_status IS NULL OR previous_status IN (1,2,3,4)",
            name="ck_import_images_previous_status",
        ),
        sa.Index("idx_import_images_image_id", "image_id"),
    )

    import_id: Mapped[int] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("imports.id", ondelete="CASCADE"),
        primary_key=True,
    )
    image_id: Mapped[int] = mapped_column(
        sa.Integer(),
        sa.ForeignKey("images.id", ondelete="CASCADE"),
        primary_key=True,
    )
    was_created: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False)
    previous_status: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    touched_at: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=UtcNow())
