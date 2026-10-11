"""
Versioned schema changes: the Alembic baseline, the deploy-time upgrade, and
the expand-and-contract primitives revisions are written with.

Three commands shape the schema on every deploy, in this order
(``scripts/database/deploy-schema.sh``):

1. ``flask init-db`` creates missing tables from the models (``create_all``).
2. ``flask schema-upgrade`` (this module) applies pending Alembic revisions.
   This is where every change ``reconcile-schema`` cannot make lives: relaxing
   or tightening NOT NULL, widening or narrowing a column, adding a constraint
   after a backfill.
3. ``flask reconcile-schema`` adds nullable columns the models gained and
   reports what it had to add. It stays in the boot sequence as the drift
   detector: on a database the first two steps already brought up to date it
   adds nothing.

The baseline revision is anchored to the model metadata rather than to a
frozen DDL dump. On every deployed database ``init-db`` runs first, so the
baseline's ``create_all(checkfirst=True)`` finds every table present and
changes nothing; on an empty database it builds the full schema. Revisions
after the baseline are ordinary Alembic revisions written with the helpers
below, each idempotent (it inspects the live column before altering it) and
each with a down step that refuses rather than lose data.

The pattern every revision after the baseline follows:

- **Expand** - add a nullable column (``reconcile-schema`` does this from the
  model), relax a NOT NULL (``relax_not_null``) or widen a column
  (``widen_varchar``). Old and new code both work against the expanded schema.
- **Backfill** - a ``flask`` command in ``app/commands/``, idempotent, run one
  organisation at a time.
- **Contract** - in a later revision, once the backfill has been measured:
  ``tighten_not_null`` or ``narrow_varchar``. Both check the data first and
  raise ``ContractBlocked`` instead of discarding a value.
"""
from __future__ import annotations

import os
import time

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import column as sa_column
from sqlalchemy import func, select, text
from sqlalchemy import table as sa_table

from app import db

#: The revision every database is recorded at once its schema matches the
#: models. Revisions before it are archived under
#: ``migrations/versions/_archive_pre_baseline/`` and are not part of the chain.
BASELINE_REVISION = "20260926_baseline"

#: Arbitrary, stable key for the advisory lock that serialises schema upgrades.
_UPGRADE_LOCK_KEY = 7_302_026_926

#: Bound on how long schema-upgrade waits for another deploy's lock before
#: giving up. Unbounded would hang a deploy forever behind a stuck or crashed
#: holder; this fails loudly instead, so the deploy stops and can be retried.
_UPGRADE_LOCK_TIMEOUT_S = 60
_UPGRADE_LOCK_POLL_S = 0.5


class SchemaUpgradeLocked(RuntimeError):
    """Another process held the schema-upgrade advisory lock past the timeout."""


class ContractBlocked(RuntimeError):
    """A contract step (or a down step) would discard or reject existing data."""


class MissingColumn(RuntimeError):
    """A revision addressed a column the database does not have."""


# --------------------------------------------------------------- baseline


def dedupe_metadata_indexes(metadata=None) -> int:
    """Drop duplicate same-named indexes from each table in ``metadata``.

    A few tables are mapped by two model classes (``extend_existing``), which
    can leave the same index defined twice on one table, so a from-scratch
    ``create_all`` emits a duplicate ``CREATE INDEX`` and fails on an empty
    database. Returns how many duplicates were removed.
    """
    metadata = metadata if metadata is not None else db.metadata
    removed = 0
    for table in metadata.tables.values():
        seen = set()
        for index in list(table.indexes):
            if index.name in seen:
                table.indexes.discard(index)
                removed += 1
            else:
                seen.add(index.name)
    return removed


#: Schema objects every deployed database has that no model declares. They
#: were added by hand-written DDL in ``flask init-db`` before revisions
#: existed; the baseline must create them too or it would not match a
#: deployed schema. Measured, not guessed: tests/test_schema_migrations.py
#: compares a database built by the baseline with one built by ``init-db``
#: column for column and index for index.
UNDECLARED_COLUMNS = (
    ("principles", "enforcement_status", "VARCHAR(20) NOT NULL DEFAULT 'advisory'"),
    ("principles", "adm_phase", "VARCHAR(5)"),
)
UNDECLARED_INDEXES = (
    ("rationalization_audit_entries", "idx_rat_audit_created", "created_at"),
)


def ensure_undeclared_schema(bind) -> None:
    """Add the ``UNDECLARED_COLUMNS`` and ``UNDECLARED_INDEXES`` a database lacks.

    Idempotent (``IF NOT EXISTS``).
    """
    for table, column, ddl in UNDECLARED_COLUMNS:
        bind.execute(text(
            f"ALTER TABLE {_quote(bind, table)} "
            f"ADD COLUMN IF NOT EXISTS {_quote(bind, column)} {ddl}"
        ))
    for table, index, column in UNDECLARED_INDEXES:
        bind.execute(text(
            f"CREATE INDEX IF NOT EXISTS {_quote(bind, index)} "
            f"ON {_quote(bind, table)} ({_quote(bind, column)})"
        ))


def ensure_baseline_schema(bind) -> None:
    """Create every table and column ``flask init-db`` creates, on ``bind``.

    A no-op on any database ``init-db`` has already run against.
    """
    dedupe_metadata_indexes(db.metadata)
    db.metadata.create_all(bind=bind, checkfirst=True)
    ensure_undeclared_schema(bind)


# ------------------------------------------------------ expand / contract


def _quote(bind, name: str) -> str:
    return bind.dialect.identifier_preparer.quote(name)


def _column_ref(table: str, column: str):
    """A quoted ``table.column`` expression for counting rows by value."""
    return sa_table(table, sa_column(column)).c[column]


def column_state(bind, table: str, column: str) -> dict | None:
    """Return the live ``data_type``, ``max_length`` and ``nullable`` of a column.

    ``None`` when the table or column does not exist in the current schema.
    """
    row = bind.execute(
        text(
            "SELECT data_type, character_maximum_length, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).first()
    if row is None:
        return None
    return {
        "data_type": row[0],
        "max_length": row[1],
        "nullable": row[2] == "YES",
    }


def _require(bind, table: str, column: str) -> dict:
    state = column_state(bind, table, column)
    if state is None:
        raise MissingColumn(f"{table}.{column} does not exist in the current schema")
    return state


def relax_not_null(bind, table: str, column: str) -> bool:
    """Expand: allow NULL in ``table.column``. Returns True when it altered."""
    if _require(bind, table, column)["nullable"]:
        return False
    bind.execute(text(
        f"ALTER TABLE {_quote(bind, table)} "
        f"ALTER COLUMN {_quote(bind, column)} DROP NOT NULL"
    ))
    return True


def tighten_not_null(bind, table: str, column: str) -> bool:
    """Contract: forbid NULL in ``table.column``, refusing if any row holds NULL.

    Returns True when it altered. Raises ``ContractBlocked`` naming the row
    count when a backfill has not reached every row; nothing is changed.
    """
    if not _require(bind, table, column)["nullable"]:
        return False
    qt, qc = _quote(bind, table), _quote(bind, column)
    nulls = bind.execute(
        select(func.count()).where(_column_ref(table, column).is_(None))
    ).scalar()
    if nulls:
        raise ContractBlocked(
            f"{table}.{column}: {nulls} row(s) hold NULL, so NOT NULL cannot be "
            "restored without discarding them. Backfill those rows first, or "
            "forward-fix with a new revision instead of downgrading."
        )
    bind.execute(text(f"ALTER TABLE {qt} ALTER COLUMN {qc} SET NOT NULL"))
    return True


def _require_varchar(bind, table: str, column: str) -> dict:
    state = _require(bind, table, column)
    if state["data_type"] != "character varying":
        raise MissingColumn(
            f"{table}.{column} is {state['data_type']}, not character varying"
        )
    return state


def widen_varchar(bind, table: str, column: str, length: int) -> bool:
    """Expand: make ``table.column`` at least ``VARCHAR(length)``.

    A no-op when the column is already that wide or unbounded. On PostgreSQL
    widening a VARCHAR rewrites neither the table nor its indexes.
    """
    current = _require_varchar(bind, table, column)["max_length"]
    if current is None or current >= length:
        return False
    bind.execute(text(
        f"ALTER TABLE {_quote(bind, table)} "
        f"ALTER COLUMN {_quote(bind, column)} TYPE VARCHAR({int(length)})"
    ))
    return True


def narrow_varchar(bind, table: str, column: str, length: int) -> bool:
    """Contract: make ``table.column`` ``VARCHAR(length)``, refusing to truncate.

    Raises ``ContractBlocked`` when any stored value is longer than ``length``;
    nothing is changed.
    """
    current = _require_varchar(bind, table, column)["max_length"]
    if current is not None and current <= length:
        return False
    qt, qc = _quote(bind, table), _quote(bind, column)
    too_long = bind.execute(
        select(func.count()).where(func.char_length(_column_ref(table, column)) > int(length))
    ).scalar()
    if too_long:
        raise ContractBlocked(
            f"{table}.{column}: {too_long} value(s) are longer than {length} "
            "characters, so the column cannot be narrowed without truncating "
            "them. Forward-fix with a new revision instead of downgrading."
        )
    bind.execute(text(
        f"ALTER TABLE {qt} ALTER COLUMN {qc} TYPE VARCHAR({int(length)})"
    ))
    return True


# ------------------------------------------------------- deploy command


def acquire_upgrade_lock(
    lock_conn, timeout_s: float = _UPGRADE_LOCK_TIMEOUT_S, poll_s: float = _UPGRADE_LOCK_POLL_S
) -> None:
    """Block until the schema-upgrade advisory lock is held, or ``timeout_s`` elapses.

    Polls ``pg_try_advisory_lock`` (non-blocking) instead of the blocking
    ``pg_advisory_lock``, so a stuck or crashed holder cannot hang a deploy
    forever: raises ``SchemaUpgradeLocked`` instead, changing nothing.
    """
    deadline = time.monotonic() + timeout_s
    while not lock_conn.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": _UPGRADE_LOCK_KEY}
    ).scalar():
        if time.monotonic() >= deadline:
            raise SchemaUpgradeLocked(
                f"schema-upgrade: another process still held the upgrade lock after "
                f"{timeout_s}s; nothing was changed. Retry once it finishes or crashes."
            )
        time.sleep(poll_s)


def _migrations_directory() -> str:
    migrate = current_app.extensions.get("migrate")
    directory = getattr(migrate, "directory", None) or "migrations"
    if not os.path.isabs(directory):
        directory = os.path.join(os.path.dirname(current_app.root_path), directory)
    return directory


def _alembic_config():
    from alembic.config import Config

    directory = _migrations_directory()
    config = Config(os.path.join(directory, "alembic.ini"))
    config.set_main_option("script_location", directory)
    return config


def recorded_revisions(bind) -> list[str]:
    """Revision ids in ``alembic_version``; empty when the table is absent."""
    exists = bind.execute(text("SELECT to_regclass('alembic_version')")).scalar()
    if exists is None:
        return []
    return [r[0] for r in bind.execute(text("SELECT version_num FROM alembic_version"))]


def known_revisions(config=None) -> set[str]:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(config or _alembic_config())
    return {rev.revision for rev in script.walk_revisions()}


@click.command("schema-upgrade")
@click.option("--to", "target", default="head", show_default=True,
              help="Revision to upgrade to.")
@with_appcontext
def schema_upgrade(target):
    """Apply pending schema revisions (safe on an existing database).

    A database recorded at a revision from before the baseline is re-stamped
    at the baseline first: those revisions are archived and cannot be
    resolved. The old ids are printed so the stamp can be put back.
    """
    from alembic import command

    config = _alembic_config()
    known = known_revisions(config)

    with db.engine.connect() as lock_conn:
        acquire_upgrade_lock(lock_conn)
        try:
            with db.engine.connect() as conn:
                before = recorded_revisions(conn)
            unknown = [r for r in before if r not in known]
            if unknown:
                click.echo(
                    "schema-upgrade: database recorded at pre-baseline revision(s) "
                    f"{', '.join(unknown)}; stamping {BASELINE_REVISION} (record only, "
                    "no DDL). Previous alembic_version value(s): "
                    + ", ".join(unknown)
                )
                command.stamp(config, BASELINE_REVISION, purge=True)
            command.upgrade(config, target)
            with db.engine.connect() as conn:
                after = recorded_revisions(conn)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _UPGRADE_LOCK_KEY})
            lock_conn.commit()

    click.echo(
        f"schema-upgrade: at {', '.join(after) or 'no revision'} "
        f"(was {', '.join(before) or 'unversioned'})."
    )


def init_app(app):
    """Register the schema-upgrade CLI command."""
    app.cli.add_command(schema_upgrade)
