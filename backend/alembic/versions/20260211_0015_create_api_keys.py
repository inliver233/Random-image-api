from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260211_0015"
down_revision = "20260210_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("hint", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_used_at", sa.Text(), nullable=True),
        sa.UniqueConstraint("name", name="uq_api_keys_name"),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_api_keys_enabled"),
    )

    op.create_index("idx_api_keys_enabled", "api_keys", ["enabled"], unique=False)
    op.create_index("idx_api_keys_created_at", "api_keys", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_api_keys_created_at", table_name="api_keys")
    op.drop_index("idx_api_keys_enabled", table_name="api_keys")
    op.drop_table("api_keys")
