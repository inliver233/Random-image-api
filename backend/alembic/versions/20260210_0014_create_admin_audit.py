from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260210_0014"
down_revision = "20260210_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
        ),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("record_id", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=True),
    )

    op.create_index("idx_admin_audit_created_at", "admin_audit", ["created_at"], unique=False)
    op.create_index("idx_admin_audit_action", "admin_audit", ["action"], unique=False)
    op.create_index("idx_admin_audit_resource", "admin_audit", ["resource"], unique=False)
    op.create_index("idx_admin_audit_record_id", "admin_audit", ["record_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_admin_audit_record_id", table_name="admin_audit")
    op.drop_index("idx_admin_audit_resource", table_name="admin_audit")
    op.drop_index("idx_admin_audit_action", table_name="admin_audit")
    op.drop_index("idx_admin_audit_created_at", table_name="admin_audit")
    op.drop_table("admin_audit")

