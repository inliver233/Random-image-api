from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260210_0011"
down_revision = "20260210_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
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
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("run_after", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.Text(), nullable=True),
        sa.Column("ref_type", sa.Text(), nullable=True),
        sa.Column("ref_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','paused','canceled','completed','failed','dlq')",
            name="ck_jobs_status",
        ),
    )

    op.create_index(
        "idx_jobs_status_priority",
        "jobs",
        ["status", "priority", "id"],
        unique=False,
    )
    op.create_index("idx_jobs_run_after", "jobs", ["run_after"], unique=False)
    op.create_index("idx_jobs_ref", "jobs", ["ref_type", "ref_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_jobs_ref", table_name="jobs")
    op.drop_index("idx_jobs_run_after", table_name="jobs")
    op.drop_index("idx_jobs_status_priority", table_name="jobs")
    op.drop_table("jobs")

