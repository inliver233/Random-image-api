from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base
from app.db.utc_text_now import UtcNow


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (sa.UniqueConstraint("name", name="uq_tags_name"),)

    id: Mapped[int] = mapped_column(sa.Integer(), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    translated_name: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=UtcNow(),
    )
    updated_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=UtcNow(),
    )

