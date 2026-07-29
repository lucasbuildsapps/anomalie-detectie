"""observations.timestamp: ISO-tekst → echte DateTime-kolom.

Oudere databases (pre-juli 2026) sloegen timestamps op als ISO-8601-strings
(soms met 'T'-separator en '+00:00'-offset). Deze migratie normaliseert de
waarden zodat SQLAlchemy's DateTime-verwerking ze eenduidig leest, en zet op
Postgres het kolomtype om zodat range-queries en DB-side aggregatie werken.

- SQLite: kolomtypes zijn adviserend; SQLAlchemy leest via het metadata-type
  (DateTime). We herschrijven alleen de tekstwaarden naar het canonieke
  'YYYY-MM-DD HH:MM:SS'-formaat. GEEN alter_column: SQLAlchemy's batch-alter
  kopieert met CAST, en SQLite's CAST naar DATETIME (NUMERIC-affiniteit)
  verminkt datumstrings tot het jaartal.
- Postgres: ALTER TYPE met USING-cast (data was al UTC; de offset valt weg
  bij AT TIME ZONE 'UTC').
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        op.execute(sa.text(
            "UPDATE observations SET timestamp = REPLACE(timestamp, 'T', ' ')"
        ))
        op.execute(sa.text(
            "UPDATE observations SET timestamp = "
            "SUBSTR(timestamp, 1, LENGTH(timestamp) - 6) "
            "WHERE timestamp LIKE '%+00:00'"
        ))
    elif dialect == "postgresql":
        op.execute(sa.text(
            "ALTER TABLE observations "
            "ALTER COLUMN timestamp TYPE timestamp "
            "USING timestamp::timestamptz AT TIME ZONE 'UTC'"
        ))

    # Index voor de laad-query (per dataset, gesorteerd op tijd).
    op.create_index(
        "ix_obs_dataset_ts", "observations", ["dataset_id", "timestamp"],
        if_not_exists=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    op.drop_index("ix_obs_dataset_ts", table_name="observations")
    if dialect == "postgresql":
        op.execute(sa.text(
            "ALTER TABLE observations "
            "ALTER COLUMN timestamp TYPE varchar(64) "
            "USING to_char(timestamp, 'YYYY-MM-DD\"T\"HH24:MI:SS')"
        ))
    # SQLite: waarden blijven leesbaar; niets terug te draaien.
