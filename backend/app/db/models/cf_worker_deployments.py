from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base
from app.db.utc_text_now import UtcNow


class CfWorkerDeployment(Base):
    __tablename__ = "cf_worker_deployments"
    __table_args__ = (
        sa.UniqueConstraint("account_id", "worker_name", name="uq_cf_worker_account_name"),
        sa.CheckConstraint("kind IN ('api','image')", name="ck_cf_worker_deployment_kind"),
        sa.CheckConstraint(
            "state IN ('intent','deployed','verified','complete','delete_intent','deleted','failed')",
            name="ck_cf_worker_deployment_state",
        ),
        sa.Index("idx_cf_worker_deployment_state", "state"),
    )

    id: Mapped[int] = mapped_column(sa.Integer(), primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    worker_name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    state: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("'intent'"))
    operation_version: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default=sa.text("1")
    )
    base_url: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_at: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=UtcNow())
    updated_at: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=UtcNow())
