from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260210_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "imports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"),
        ),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("accepted", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("success", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("detail_json", sa.Text(), nullable=True),
    )
    op.create_index("idx_imports_created_at", "imports", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_imports_created_at", table_name="imports")
    op.drop_table("imports")

