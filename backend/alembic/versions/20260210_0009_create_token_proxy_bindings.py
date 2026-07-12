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


revision = "20260210_0009"
down_revision = "20260210_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_proxy_bindings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
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
        sa.Column(
            "token_id",
            sa.Integer(),
            sa.ForeignKey("pixiv_tokens.id", name="fk_tpb_token", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pool_id",
            sa.Integer(),
            sa.ForeignKey("proxy_pools.id", name="fk_tpb_pool", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "primary_proxy_id",
            sa.Integer(),
            sa.ForeignKey("proxy_endpoints.id", name="fk_tpb_primary", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "override_proxy_id",
            sa.Integer(),
            sa.ForeignKey("proxy_endpoints.id", name="fk_tpb_override", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("override_expires_at", sa.Text(), nullable=True),
        sa.UniqueConstraint("token_id", "pool_id", name="uq_token_pool"),
    )

    op.create_index("idx_tpb_pool", "token_proxy_bindings", ["pool_id"], unique=False)
    op.create_index("idx_tpb_primary", "token_proxy_bindings", ["primary_proxy_id"], unique=False)
    op.create_index("idx_tpb_override", "token_proxy_bindings", ["override_proxy_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_tpb_override", table_name="token_proxy_bindings")
    op.drop_index("idx_tpb_primary", table_name="token_proxy_bindings")
    op.drop_index("idx_tpb_pool", table_name="token_proxy_bindings")
    op.drop_table("token_proxy_bindings")

