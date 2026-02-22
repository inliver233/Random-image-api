from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260210_0007"
down_revision = "20260210_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proxy_pools",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
        ),
        sa.Column(
            "updated_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("name", name="uq_proxy_pools_name"),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_proxy_pools_enabled"),
    )
    op.create_index("idx_proxy_pools_enabled", "proxy_pools", ["enabled"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_proxy_pools_enabled", table_name="proxy_pools")
    op.drop_table("proxy_pools")

