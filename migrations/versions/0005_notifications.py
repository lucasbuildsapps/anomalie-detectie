"""Verstuurde meldingen (ontdubbeling van waarschuwingen)

Revision ID: 0005
Revises: 0004
"""
from alembic import op
from sqlalchemy import inspect

from core.storage import notifications_t

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if notifications_t.name not in set(inspect(bind).get_table_names()):
        notifications_t.create(bind, checkfirst=True)


def downgrade() -> None:
    notifications_t.drop(op.get_bind(), checkfirst=True)
