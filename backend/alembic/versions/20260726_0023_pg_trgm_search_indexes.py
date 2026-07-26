from __future__ import annotations

from alembic import op

revision = "20260726_0023"
down_revision = "20260713_0022"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name.startswith("postgres")


def upgrade() -> None:
    # PostgreSQL search fallback is %…% ILIKE (SQLite uses FTS5 instead, M6).
    # Without trigram GIN indexes those predicates are full-table scans over
    # ~13万 tags / 62万 images. pg_trgm ships in contrib on every supported
    # PG16 image; CREATE EXTENSION needs no superuser on standard installs.
    if not _is_postgres():
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tags_name_trgm "
        "ON tags USING gin (name gin_trgm_ops);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tags_translated_name_trgm "
        "ON tags USING gin (translated_name gin_trgm_ops);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_images_user_name_trgm "
        "ON images USING gin (user_name gin_trgm_ops);"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute("DROP INDEX IF EXISTS idx_images_user_name_trgm;")
    op.execute("DROP INDEX IF EXISTS idx_tags_translated_name_trgm;")
    op.execute("DROP INDEX IF EXISTS idx_tags_name_trgm;")
    # The extension is left installed: other objects may depend on it.
