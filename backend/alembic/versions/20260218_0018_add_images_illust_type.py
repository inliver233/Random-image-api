from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260218_0018"
down_revision = "20260215_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("images", sa.Column("illust_type", sa.Integer(), nullable=True))
    op.create_index("idx_images_illust_type_random", "images", ["status", "illust_type", "random_key"])


def downgrade() -> None:
    op.drop_index("idx_images_illust_type_random", table_name="images")
    with op.batch_alter_table("images") as batch_op:
        batch_op.drop_column("illust_type")

