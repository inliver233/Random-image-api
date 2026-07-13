"""Create durable Cloudflare Worker deployment intents.

Revision ID: 20260713_0021
Revises: 20260713_0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.utc_text_now import UtcNow

revision = "20260713_0021"
down_revision = "20260713_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cf_worker_deployments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("worker_name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'intent'")),
        sa.Column("operation_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=UtcNow()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=UtcNow()),
        sa.CheckConstraint("kind IN ('api','image')", name="ck_cf_worker_deployment_kind"),
        sa.CheckConstraint(
            "state IN ('intent','deployed','verified','complete','delete_intent','deleted','failed')",
            name="ck_cf_worker_deployment_state",
        ),
        sa.UniqueConstraint("account_id", "worker_name", name="uq_cf_worker_account_name"),
    )
    op.create_index(
        "idx_cf_worker_deployment_state",
        "cf_worker_deployments",
        ["state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_cf_worker_deployment_state", table_name="cf_worker_deployments")
    op.drop_table("cf_worker_deployments")
