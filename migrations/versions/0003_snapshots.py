"""Analyse-momentopnames + ingest-runs

Revision ID: 0003_snapshots
Revises: 0002_timestamp_datetime
"""
from alembic import op
from sqlalchemy import inspect

from core.storage import ingest_runs_t, snapshots_t

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    for table in (snapshots_t, ingest_runs_t):
        if table.name not in existing:
            table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in (snapshots_t, ingest_runs_t):
        table.drop(bind, checkfirst=True)
