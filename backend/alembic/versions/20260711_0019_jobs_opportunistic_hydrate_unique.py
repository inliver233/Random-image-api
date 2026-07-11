from __future__ import annotations

from alembic import op

revision = "20260711_0019"
down_revision = "20260218_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite partial unique index: only one active opportunistic hydrate per illust.
    op.execute(
        """
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_active_opportunistic_hydrate
ON jobs(type, ref_type, ref_id)
WHERE type = 'hydrate_metadata'
  AND ref_type = 'opportunistic_hydrate'
  AND status IN ('pending', 'running');
""".strip()
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_jobs_active_opportunistic_hydrate;")
