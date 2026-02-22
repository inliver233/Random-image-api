from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260210_0005"
down_revision = "20260210_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pixiv_tokens",
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
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("refresh_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_masked", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("backoff_until", sa.Text(), nullable=True),
        sa.Column("last_ok_at", sa.Text(), nullable=True),
        sa.Column("last_fail_at", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_error_msg", sa.Text(), nullable=True),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_pixiv_tokens_enabled"),
    )
    op.create_index("idx_pixiv_tokens_enabled", "pixiv_tokens", ["enabled"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_pixiv_tokens_enabled", table_name="pixiv_tokens")
    op.drop_table("pixiv_tokens")

