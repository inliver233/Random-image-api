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
            server_default=utc_iso_now_server_default(_dialect_name()),
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

