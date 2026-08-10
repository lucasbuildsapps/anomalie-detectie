"""entity_events: persistence for entity-engine output.

Why a new table rather than the existing `events`
-------------------------------------------------
`events` is an analyst annotation: a date and a label. It carries no entity, no
provenance and no arrival time, so it cannot hold a derived observation that
has to be replayed point-in-time or traced back to the method that produced it.

Why this table exists at all
----------------------------
Non-negotiable #7 says entity regions and count regions share one data model
and one indicator machinery. Until now the machinery was genuinely shared but
the storage was not: `sentinel/entity/` produced `Event` objects that lived in
memory and were never written anywhere, so no entity indicator could run in the
production path. This is the missing half.

The arrival-time column means the same thing here as on `observations`, with
one wrinkle worth stating: for a *derived* event, `ingested_at` is when the
detector ran, not when the behaviour occurred. A loiter that happened on the
3rd but was only computed on the 9th was not knowable on the 5th, and a replay
has to reflect that or it credits the system with foresight it did not have.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_TABLE = "entity_events"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in inspect(bind).get_table_names():
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dataset_id", sa.Integer(),
                  sa.ForeignKey("datasets.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("region_key", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_time", sa.DateTime(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("ingest_estimated", sa.Boolean()),
        sa.Column("entity_key", sa.String(128)),
        sa.Column("entity_kind", sa.String(32)),
        sa.Column("area_key", sa.String(64)),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("magnitude", sa.Float()),
        sa.Column("unit", sa.String(32)),
        sa.Column("producer", sa.String(32)),
        sa.Column("method", sa.String(128)),
        sa.Column("source_keys", sa.Text()),
        sa.Column("attrs", sa.Text()),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("dataset_id", "row_hash",
                            name="uq_evt_dataset_hash"),
    )
    op.create_index("ix_evt_region_time", _TABLE,
                    ["dataset_id", "region_key", "event_time"])
    op.create_index("ix_evt_region_ingested", _TABLE,
                    ["dataset_id", "region_key", "ingested_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in inspect(bind).get_table_names():
        return
    idx_names = {i["name"] for i in inspect(bind).get_indexes(_TABLE)}
    for name in ("ix_evt_region_time", "ix_evt_region_ingested"):
        if name in idx_names:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
