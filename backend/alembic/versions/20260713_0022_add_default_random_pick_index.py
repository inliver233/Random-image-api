from __future__ import annotations

from alembic import op


revision = "20260713_0022"
down_revision = "20260713_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_images_status_x_random",
        "images",
        ["status", "x_restrict", "random_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_images_status_x_random", table_name="images")
