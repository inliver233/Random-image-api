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


revision = "20260210_0010"
down_revision = "20260210_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.Text(),
            nullable=False,
            server_default=utc_iso_now_server_default(_dialect_name()),
        ),
        sa.Column("updated_by", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("runtime_settings")

