from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db.utc_text_now import utc_iso_now_server_default


def _dialect_name() -> str:
    try:
        name = (getattr(getattr(op.get_bind(), "dialect", None), "name", None) or "").strip().lower()
    except Exception:
        name = ""
    return name or "sqlite"


revision = "20260210_0008"
down_revision = "20260210_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proxy_pool_endpoints",
        sa.Column(
            "pool_id",
            sa.Integer(),
            sa.ForeignKey("proxy_pools.id", name="fk_ppe_pool", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "endpoint_id",
            sa.Integer(),
            sa.ForeignKey("proxy_endpoints.id", name="fk_ppe_ep", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("weight", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=utc_iso_now_server_default(_dialect_name()),
        ),
        sa.Column(
            "updated_at",
            sa.Text(),
            nullable=False,
            server_default=utc_iso_now_server_default(_dialect_name()),
        ),
        sa.PrimaryKeyConstraint("pool_id", "endpoint_id"),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_ppe_enabled"),
    )

    op.create_index(
        "idx_ppe_pool_enabled",
        "proxy_pool_endpoints",
        ["pool_id", "enabled"],
        unique=False,
    )
    op.create_index(
        "idx_ppe_endpoint_pool",
        "proxy_pool_endpoints",
        ["endpoint_id", "pool_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_ppe_endpoint_pool", table_name="proxy_pool_endpoints")
    op.drop_index("idx_ppe_pool_enabled", table_name="proxy_pool_endpoints")
    op.drop_table("proxy_pool_endpoints")

