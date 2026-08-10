"""positions: raw position reports, and the denominator rarity needs.

Two jobs, and the second is the one that unblocked this
-------------------------------------------------------
The obvious job is somewhere for a live AIS feed to land, so the entity
primitives have real tracks to run over instead of a synthetic fleet.

The load-bearing job is the **observed population**: every entity that emitted
anything, including the silent majority that did nothing interesting. Entity
events only record vessels that *did* something, so a peer baseline fitted from
events alone has participation of 1.0 by construction — rarity, which is the
primary entity signal, can never fire. Until this table exists the entity test
correctly reports insufficient data rather than calling a magnitude-only pass
quiet. This is where the denominator comes from.

Why not PostGIS
---------------
The roadmap said PostGIS plus partitioning. Neither is built here, deliberately:

- Nothing in the codebase performs a real spatial query. `sentinel/entity/geo.py`
  computes haversine distance and cross-track offset without a geometry stack,
  and the population denominator is a `SELECT DISTINCT entity_key`. A hard
  PostGIS dependency would break the SQLite path the whole test suite runs on,
  in exchange for nothing used today.
- Partitioning is a volume answer, and there is no volume yet — there is no
  live feed. Partitioning an empty table is a guess about a load nobody has
  measured.

Where PostGIS will genuinely earn its place: when an indicator asks "within
this corridor" rather than "within this box". Cable corridors are lines with a
buffer, not rectangles, and `bbox` filtering cannot express them.

Arrival times
-------------
Same two-column scheme as `observations`. A position received late is not a
position we had, and a bulk historical import that pretends otherwise makes
every replay optimistic by the reporting lag.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_TABLE = "positions"
_INDEXES = (
    ("ix_pos_entity_ts", ["dataset_id", "entity_key", "timestamp"]),
    ("ix_pos_dataset_ts", ["dataset_id", "timestamp"]),
    ("ix_pos_dataset_ingested", ["dataset_id", "ingested_at"]),
)


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
        sa.Column("entity_key", sa.String(128), nullable=False),
        sa.Column("entity_kind", sa.String(32)),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("ingest_estimated", sa.Boolean()),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("sog", sa.Float()),
        sa.Column("cog", sa.Float()),
        sa.Column("heading", sa.Float()),
        sa.Column("vessel_class", sa.String(64)),
        sa.Column("source_key", sa.String(64)),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("dataset_id", "row_hash",
                            name="uq_pos_dataset_hash"),
    )
    for name, columns in _INDEXES:
        op.create_index(name, _TABLE, columns)


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in inspect(bind).get_table_names():
        return
    existing = {i["name"] for i in inspect(bind).get_indexes(_TABLE)}
    for name, _columns in _INDEXES:
        if name in existing:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
