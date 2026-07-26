from __future__ import annotations

from alembic import op

revision = "20260711_0019"
down_revision = "20260218_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deterministic preflight: databases from before this index can hold
    # duplicate active opportunistic hydrates; creating a unique index over
    # them would abort the migration (and the API startup chain). Keep the
    # oldest active job per (type, ref_type, ref_id), cancel the rest.
    op.execute(
        """
UPDATE jobs
SET status = 'canceled',
    last_error = 'migration 0019: duplicate active opportunistic hydrate canceled'
WHERE type = 'hydrate_metadata'
  AND ref_type = 'opportunistic_hydrate'
  AND status IN ('pending', 'running')
  AND id NOT IN (
    SELECT MIN(id)
    FROM jobs
    WHERE type = 'hydrate_metadata'
      AND ref_type = 'opportunistic_hydrate'
      AND status IN ('pending', 'running')
    GROUP BY type, ref_type, ref_id
  );
""".strip()
    )
    # Partial unique index: only one active opportunistic hydrate per illust
    # (identical predicate on SQLite and PostgreSQL).
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
