"""Programmatic Alembic environment for a caller-supplied SQLite connection."""

from __future__ import annotations

from alembic import context


connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("A validated caller-supplied connection is required")

context.configure(
    connection=connection,
    target_metadata=None,
    render_as_batch=True,
    compare_type=False,
    transaction_per_migration=True,
)

with context.begin_transaction():
    context.run_migrations()

