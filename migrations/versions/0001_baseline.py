"""Baseline: maak alle tabellen aan zoals gedefinieerd in core/storage.py.

Idempotent (checkfirst): op een bestaande database uit de pre-Alembic-tijd
worden alleen ontbrekende tabellen aangemaakt; bestaande blijven onaangeroerd.
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from core import storage
    storage._metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Baseline wordt niet teruggedraaid (zou alle data verwijderen).
    raise NotImplementedError("baseline is niet omkeerbaar")
