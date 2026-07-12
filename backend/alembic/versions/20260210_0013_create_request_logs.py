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


revision = "20260210_0013"
down_revision = "20260210_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=utc_iso_now_server_default(_dialect_name()),
        ),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("sample_rate", sa.Float(), nullable=True),
    )

    op.create_index("idx_request_logs_created_at", "request_logs", ["created_at"], unique=False)
    op.create_index("idx_request_logs_route", "request_logs", ["route"], unique=False)
    op.create_index("idx_request_logs_status", "request_logs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_request_logs_status", table_name="request_logs")
    op.drop_index("idx_request_logs_route", table_name="request_logs")
    op.drop_index("idx_request_logs_created_at", table_name="request_logs")
    op.drop_table("request_logs")

