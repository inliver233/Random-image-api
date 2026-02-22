from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260210_0002"
down_revision = "20260210_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("illust_id", sa.Integer(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("ext", sa.Text(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("proxy_path", sa.Text(), nullable=False),
        sa.Column("random_key", sa.Float(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("aspect_ratio", sa.Float(), nullable=True),
        sa.Column("orientation", sa.Integer(), nullable=True),
        sa.Column("x_restrict", sa.Integer(), nullable=True),
        sa.Column("ai_type", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_name", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("created_at_pixiv", sa.Text(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_fail_at", sa.Text(), nullable=True),
        sa.Column("last_ok_at", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_error_msg", sa.Text(), nullable=True),
        sa.Column(
            "created_import_id",
            sa.Integer(),
            sa.ForeignKey("imports.id", name="fk_images_import", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "added_at",
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
        sa.CheckConstraint("status IN (1,2,3,4)", name="ck_images_status"),
        sa.CheckConstraint("random_key >= 0.0 AND random_key < 1.0", name="ck_images_random_key"),
        sa.UniqueConstraint("illust_id", "page_index", name="uq_images_illust_page"),
    )

    op.create_index(
        "idx_images_filter",
        "images",
        ["status", "x_restrict", "orientation", "width", "height", "random_key"],
        unique=False,
    )
    op.create_index(
        "idx_images_user_random",
        "images",
        ["status", "user_id", "random_key"],
        unique=False,
    )
    op.create_index("idx_images_created_at_pixiv", "images", ["created_at_pixiv"], unique=False)
    op.create_index("idx_images_created_import_id", "images", ["created_import_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_images_created_import_id", table_name="images")
    op.drop_index("idx_images_created_at_pixiv", table_name="images")
    op.drop_index("idx_images_user_random", table_name="images")
    op.drop_index("idx_images_filter", table_name="images")
    op.drop_table("images")

