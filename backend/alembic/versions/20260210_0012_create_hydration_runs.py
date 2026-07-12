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


revision = "20260210_0012"
down_revision = "20260210_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hydration_runs",
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
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("criteria_json", sa.Text(), nullable=True),
        sa.Column("cursor_json", sa.Text(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("processed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("success", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','paused','canceled','completed','failed')",
            name="ck_hr_status",
        ),
    )

    op.create_index(
        "idx_hr_status_updated",
        "hydration_runs",
        ["status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_hr_status_updated", table_name="hydration_runs")
    op.drop_table("hydration_runs")

