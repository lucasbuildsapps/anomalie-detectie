"""Watchboard-indicatoren

Revision ID: 0006
Revises: 0005
"""
from alembic import op
from sqlalchemy import inspect

from core.storage import indicators_t

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if indicators_t.name not in set(inspect(bind).get_table_names()):
        indicators_t.create(bind, checkfirst=True)


def downgrade() -> None:
    indicators_t.drop(op.get_bind(), checkfirst=True)
