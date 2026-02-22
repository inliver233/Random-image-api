from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260215_0017"
down_revision = "20260211_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("images", sa.Column("bookmark_count", sa.Integer(), nullable=True))
    op.add_column("images", sa.Column("view_count", sa.Integer(), nullable=True))
    op.add_column("images", sa.Column("comment_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("images") as batch_op:
        batch_op.drop_column("comment_count")
        batch_op.drop_column("view_count")
        batch_op.drop_column("bookmark_count")

