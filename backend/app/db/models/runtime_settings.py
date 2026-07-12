from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base
from app.db.utc_text_now import UtcNow


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    value_json: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    updated_at: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=UtcNow(),
    )
    updated_by: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)

