"""Compartimentering: required_group op datasets

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("datasets")}
    if "required_group" not in cols:
        op.add_column("datasets",
                      sa.Column("required_group", sa.String(128),
                                nullable=True))
    # Bestaande datasets blijven zichtbaar voor iedereen (NULL); een
    # beheerder zet de compartimenten daarna bewust.


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("datasets")}
    if "required_group" in cols:
        op.drop_column("datasets", "required_group")
