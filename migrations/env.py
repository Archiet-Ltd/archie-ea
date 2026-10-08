"""Alembic environment for Flask-Migrate.

Runs every revision in its own transaction (``transaction_per_migration``), so
a failing revision rolls back alone and the revisions before it stay recorded.
PostgreSQL DDL is transactional, so a failed revision leaves no partial change.

Only ``migrations/versions/*.py`` is on the chain. The pre-baseline history in
``migrations/versions/_archive_pre_baseline/`` is kept for reference and is not
loaded (Alembic does not recurse into subdirectories by default).
"""
import logging

from alembic import context
from flask import current_app
from sqlalchemy import text

# No fileConfig() here: schema-upgrade runs inside the already-booted Flask
# app (this module executes under @with_appcontext), whose own logging is
# already configured by app/services/core/logging_config.py (dictConfig).
# Alembic's default fileConfig() boilerplate would reconfigure the root
# logger from alembic.ini on top of that, fighting the one logging setup
# this app already has.
config = context.config
logger = logging.getLogger("alembic.env")


def _db():
    return current_app.extensions["migrate"].db


def _metadata():
    return _db().metadata


def run_migrations_offline():
    """Emit SQL to stdout instead of executing it (``flask db upgrade --sql``)."""
    url = str(_db().engine.url.render_as_string(hide_password=False)).replace("%", "%%")
    context.configure(
        url=url,
        target_metadata=_metadata(),
        literal_binds=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


#: Bounds how long a DDL statement inside a revision (ALTER TABLE, etc.) waits
#: on a conflicting lock held by some unrelated session (a long-running query,
#: another connection) -- the acquire_upgrade_lock advisory lock only
#: serialises schema-upgrade invocations against each other, it says nothing
#: about a lock an unrelated session holds on the table being altered.
_DDL_LOCK_TIMEOUT = "30s"


def run_migrations_online():
    connectable = _db().engine
    with connectable.connect() as connection:
        # Plain (non-LOCAL) SET is itself transactional in PostgreSQL, so it
        # must be committed to survive as a session-level GUC into the
        # transactions Alembic opens afterwards on this same connection.
        # Two things that look like fixes are not:
        #   - No explicit commit here: SQLAlchemy 2.0 opens an implicit
        #     transaction on the SET, Alembic's own begin_transaction()
        #     (transaction_per_migration=True) then finds one already open
        #     and applies nothing -- alembic_version stays empty, no error.
        #   - execution_options(isolation_level="AUTOCOMMIT") instead of a
        #     commit: mutates the underlying DBAPI connection's autocommit
        #     flag for the rest of its life (the same physical connection),
        #     so Alembic's later SAVEPOINT for transaction_per_migration then
        #     fails with "SAVEPOINT can only be used in transaction blocks".
        connection.execute(text(f"SET lock_timeout = '{_DDL_LOCK_TIMEOUT}'"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=_metadata(),
            transaction_per_migration=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
