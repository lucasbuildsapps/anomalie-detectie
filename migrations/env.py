"""Alembic-omgeving: gebruikt dezelfde URL-resolutie als de app zelf."""
from __future__ import annotations

import sys
from pathlib import Path

from alembic import context

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import storage  # noqa: E402

target_metadata = storage._metadata


def run_migrations_offline() -> None:
    context.configure(
        url=storage._database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = storage._engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
