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


revision = "20260713_0020"
down_revision = "20260711_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("imports", sa.Column("rollback_mode", sa.Text(), nullable=True))
    op.add_column("imports", sa.Column("rolled_back_at", sa.Text(), nullable=True))
    op.create_table(
        "import_images",
        sa.Column(
            "import_id",
            sa.Integer(),
            sa.ForeignKey("imports.id", name="fk_import_images_import", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "image_id",
            sa.Integer(),
            sa.ForeignKey("images.id", name="fk_import_images_image", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("was_created", sa.Boolean(), nullable=False),
        sa.Column("previous_status", sa.Integer(), nullable=True),
        sa.Column(
            "touched_at",
            sa.Text(),
            nullable=False,
            server_default=utc_iso_now_server_default(_dialect_name()),
        ),
        sa.CheckConstraint(
            "previous_status IS NULL OR previous_status IN (1,2,3,4)",
            name="ck_import_images_previous_status",
        ),
        sa.PrimaryKeyConstraint("import_id", "image_id"),
    )
    op.create_index("idx_import_images_image_id", "import_images", ["image_id"], unique=False)
    # Historical created_import_id values may already have been overwritten by
    # the duplicate-import bug. Preserve them only as unverified memberships;
    # never authorize historical rollback without a separate audited repair.
    op.execute(
        """
INSERT INTO import_images (import_id, image_id, was_created, previous_status)
SELECT i.created_import_id, i.id, FALSE, NULL
FROM images AS i
WHERE i.created_import_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1
    FROM import_images AS ii
    WHERE ii.import_id = i.created_import_id AND ii.image_id = i.id
  )
""".strip()
    )


def downgrade() -> None:
    op.drop_index("idx_import_images_image_id", table_name="import_images")
    op.drop_table("import_images")
    op.drop_column("imports", "rolled_back_at")
    op.drop_column("imports", "rollback_mode")
