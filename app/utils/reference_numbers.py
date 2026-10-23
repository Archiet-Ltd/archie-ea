"""Allocation of human-readable reference numbers that are UNIQUE table-wide.

Why this exists
---------------
Several models carry a business reference in a column declared
``unique=True`` -- ``architecture_decisions.decision_id`` (``AD-001``),
``architecture_review_boards.board_number`` (``ARB-2026-001``) -- and each one
had its own ``next_*()`` classmethod that computed the next value with an ORM
query::

    last = cls.query.order_by(cls.id.desc()).first()

Both models are ``TenantMixin``, so ``do_orm_execute`` silently narrows that
query to ``WHERE organization_id = g.current_org_id``. The generator therefore
answers "the next reference **for this tenant**" while the unique index is
enforced across **every** tenant. A second organisation creating its first ADR
computed ``AD-001``, which another organisation already held, and the INSERT
died on the unique index.

That surfaced in production on 01 Sep 2026 as two separate BLOCKER defects with
one cause: ``POST /architecture/decisions/new`` returned a bare 500 and
persisted nothing, and ``POST /arb/sessions/create`` returned 500 so no ARB
session could ever be scheduled.

The first fix (superseded by this one)
---------------------------------------
A first fix kept the ORM-query shape but read the existing values with raw SQL
(``not`` subject to ``do_orm_execute``) and took the maximum suffix across the
whole table. That closed the ORM-filtering hole, but it did not close the
underlying problem: the application connects to production as ``archie_runtime``,
which is ``NOBYPASSRLS`` -- row-level security genuinely applies to that
connection, with or without the ORM listener. A raw ``SELECT ... FROM
architecture_decisions`` run through that role sees only the session
organisation's rows just as surely as the ORM query did, because the database
itself is filtering them, not SQLAlchemy. A second organisation's first ADR
still computed ``AD-001`` and still collided -- the same defect, moved one
layer down, caught before it reached production.

The fix
-------
Stop reading the target table at all. ``next_reference`` allocates from
``reference_number_counters`` (see ``app/models/reference_number_counter.py``),
one row per ``(table, column, prefix)`` family, with a single atomic
``INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING`` statement: Postgres
serialises concurrent callers on that row's unique index, so two callers in the
same family can never be handed the same number, whichever organisation they
run under and however many of them run at once.

This table carries no ``organization_id`` and is not row-level-secured --
deliberately: the families counted here (``AD-``, ``ARB-<year>-``, and friends)
are already required to be unique *across every organisation* by the target
column's own unique index, so a tenant-scoped counter would be exactly the bug
this module exists to remove, not a safer version of it. See that model's
docstring.

A literal ``CREATE SEQUENCE`` per family was considered and rejected: the ARB
families embed the current year in the prefix (``ARB-2026-001``, resetting to
``...-001`` every January) and a plain sequence has no key, so either the
reset behaviour would quietly break, or a deploy-time step would need to
provision a new sequence every year. The counter table is keyed by the whole
prefix string, so a new year (or any new family) is handled by the first
``INSERT ... ON CONFLICT`` that names it -- no schema change required, ever.

``next_reference`` still takes ``table``/``column`` (not just ``prefix``) even
though it no longer reads the named table, so that two different columns which
happen to share a prefix text (``architecture_review_boards.board_number`` and
``arb_review_items.review_number`` both use ``ARB-<year>-``) keep independent
counters, the same way the old table-scan was always scoped per table.
"""

from __future__ import annotations

from app import db

__all__ = ["next_reference"]


def next_reference(table: str, column: str, prefix: str, width: int = 3) -> str:
    """Return the next unused ``<prefix><n>`` reference for ``table.column``.

    Args:
        table: physical table name the reference is ultimately stored on (used
            only to key the counter; this function never reads that table).
        column: the UNIQUE reference column (used only to key the counter).
        prefix: everything before the numeric suffix, including the separator
            (e.g. ``"AD-"`` or ``"ARB-2026-"``).
        width: zero-padding of the numeric suffix.

    Allocation is one atomic UPSERT against ``reference_number_counters``; see
    the module docstring for why that replaces the previous table-wide scan.
    """
    next_value = db.session.execute(
        db.text(
            "INSERT INTO reference_number_counters "
            "(table_name, column_name, prefix, last_value) "
            "VALUES (:table_name, :column_name, :prefix, 1) "
            "ON CONFLICT (table_name, column_name, prefix) DO UPDATE SET "
            "last_value = reference_number_counters.last_value + 1, "
            "updated_at = clock_timestamp() "
            "RETURNING last_value"
        ),
        {"table_name": table, "column_name": column, "prefix": prefix},
    ).scalar_one()
    return f"{prefix}{next_value:0{width}d}"
