from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260210_0006"
down_revision = "20260210_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proxy_endpoints",
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
        sa.Column("scheme", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("password_enc", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("last_latency_ms", sa.Float(), nullable=True),
        sa.Column("last_ok_at", sa.Text(), nullable=True),
        sa.Column("last_fail_at", sa.Text(), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("blacklisted_until", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("scheme", "host", "port", "username", name="uq_proxy_identity"),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_proxy_enabled"),
    )

    op.create_index("idx_proxy_endpoints_enabled", "proxy_endpoints", ["enabled"], unique=False)
    op.create_index("idx_proxy_endpoints_source", "proxy_endpoints", ["source"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_proxy_endpoints_source", table_name="proxy_endpoints")
    op.drop_index("idx_proxy_endpoints_enabled", table_name="proxy_endpoints")
    op.drop_table("proxy_endpoints")

