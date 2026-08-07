"""observations: add ingested_at + ingest_estimated (point-in-time correctness).

Why this column exists
----------------------
v1 records *when something happened* (`timestamp`) but not *when we learned
it* (`ingested_at`). Without the second, a replay cannot reconstruct what the
tool could have said on a given day: late-arriving reporting is silently
folded into history, so every backtest is optimistic by an unknown margin.

`ingest_estimated` is the honesty flag. Rows that existed before this
migration get `ingested_at = timestamp`, which is a guess — we genuinely do
not know when those rows arrived. Marking them lets `AsOfView` report that a
replay rests on estimated arrival times rather than quietly presenting it as
a faithful reconstruction.

Backend notes
-------------
- Both columns are added nullable. Making them NOT NULL would require a table
  rebuild on SQLite for no real benefit: the insert path always sets them, and
  the read path treats NULL as "estimated, fall back to timestamp".
- Dedupe is unaffected: `row_hash` is computed from the source field values
  only, so re-importing a row keeps its original `ingested_at` via
  ON CONFLICT DO NOTHING. That is the correct semantics — the first time we
  saw it is when we learned it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_TABLE = "observations"


def _columns(bind) -> set[str]:
    return {c["name"] for c in inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)

    if "ingested_at" not in existing:
        op.add_column(_TABLE, sa.Column("ingested_at", sa.DateTime(),
                                        nullable=True))
    if "ingest_estimated" not in existing:
        op.add_column(_TABLE, sa.Column("ingest_estimated", sa.Boolean(),
                                        nullable=True))

    # Backfill: we do not know when historical rows arrived, so we assume they
    # were known when they happened and flag that assumption.
    op.execute(sa.text(
        f"UPDATE {_TABLE} "
        f"SET ingested_at = timestamp, ingest_estimated = 1 "  # noqa: S608
        f"WHERE ingested_at IS NULL"
    ))

    # Point-in-time queries filter on both time columns; without this index
    # every AsOfView read is a full scan of the dataset.
    idx_names = {i["name"] for i in inspect(bind).get_indexes(_TABLE)}
    if "ix_obs_dataset_ingested" not in idx_names:
        op.create_index("ix_obs_dataset_ingested", _TABLE,
                        ["dataset_id", "ingested_at"])


def downgrade() -> None:
    bind = op.get_bind()
    idx_names = {i["name"] for i in inspect(bind).get_indexes(_TABLE)}
    if "ix_obs_dataset_ingested" in idx_names:
        op.drop_index("ix_obs_dataset_ingested", table_name=_TABLE)

    existing = _columns(bind)
    if "ingest_estimated" in existing:
        op.drop_column(_TABLE, "ingest_estimated")
    if "ingested_at" in existing:
        op.drop_column(_TABLE, "ingested_at")
