from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    value_json: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    updated_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
    )
    updated_by: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

