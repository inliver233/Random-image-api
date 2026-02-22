from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260210_0004"
down_revision = "20260210_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_tags",
        sa.Column(
            "image_id",
            sa.Integer(),
            sa.ForeignKey("images.id", name="fk_image_tags_image", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", name="fk_image_tags_tag", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("image_id", "tag_id"),
    )
    op.create_index("idx_image_tags_tag_image", "image_tags", ["tag_id", "image_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_image_tags_tag_image", table_name="image_tags")
    op.drop_table("image_tags")

