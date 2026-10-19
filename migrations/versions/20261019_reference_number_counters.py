"""Atomic per-family counter table for app.utils.reference_numbers.next_reference.

Expand + seed. Creates ``reference_number_counters`` (one row per
``(table_name, column_name, prefix)`` family) and seeds it from whatever
reference numbers already exist for the five known families (architecture
decisions, ARB sessions, ARB review items, change requests, change notices),
so a deploy that already has rows in these tables keeps allocating past the
highest one that exists rather than restarting at 1 and colliding with it.

Idempotent: the table is created only if absent, and seeding uses
``GREATEST`` against any value already there, so a second run (or a database
``init-db`` already reached) is a no-op.

No row-level security on this table: the families it counts are already
required to be unique across every organisation by the target column's own
unique index (see app/utils/reference_numbers.py), so this table carries no
organization_id and is deliberately outside both ``TENANT_TABLES`` and
``HYBRID_TABLES`` in the row-level-security migrations.

Revision ID: 20261019_reference_number_counters
Revises: 20261018_typed_tables_rls
Create Date: 2026-10-19
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261019_reference_number_counters"
down_revision = "20261018_tree_models_rls"
branch_labels = None
depends_on = None


# (table, column, pattern). ``pattern`` captures (prefix, numeric suffix) so a
# family that embeds the year (ARB-2026-, ACR-2026-, ACN-2026-) seeds one
# counter row per distinct year actually found, not just the current one.
_FAMILIES = (
    ("architecture_decisions", "decision_id", re.compile(r"^(AD-)(\d+)$")),
    ("architecture_review_boards", "board_number", re.compile(r"^(ARB-\d{4}-)(\d+)$")),
    ("arb_review_items", "review_number", re.compile(r"^(ARB-\d{4}-)(\d+)$")),
    ("architecture_change_requests", "acr_reference", re.compile(r"^(ACR-\d{4}-)(\d+)$")),
    ("architecture_change_notices", "acn_reference", re.compile(r"^(ACN-\d{4}-)(\d+)$")),
)


def _seed_counters(bind) -> None:
    inspector = inspect(bind)
    for table, column, pattern in _FAMILIES:
        if not inspector.has_table(table):
            continue
        rows = bind.execute(
            text(f'SELECT "{column}" AS ref FROM "{table}"')  # nosec B608 - table/column are literals above
        ).fetchall()
        highest_by_prefix: dict[str, int] = {}
        for row in rows:
            ref = row.ref
            if not ref:
                continue
            match = pattern.match(ref)
            if not match:
                continue
            found_prefix, suffix = match.group(1), int(match.group(2))
            highest_by_prefix[found_prefix] = max(
                highest_by_prefix.get(found_prefix, 0), suffix
            )
        for found_prefix, highest in highest_by_prefix.items():
            bind.execute(
                text(
                    "INSERT INTO reference_number_counters "
                    "(table_name, column_name, prefix, last_value) "
                    "VALUES (:table_name, :column_name, :prefix, :last_value) "
                    "ON CONFLICT (table_name, column_name, prefix) DO UPDATE SET "
                    "last_value = GREATEST("
                    "reference_number_counters.last_value, EXCLUDED.last_value)"
                ),
                {
                    "table_name": table,
                    "column_name": column,
                    "prefix": found_prefix,
                    "last_value": highest,
                },
            )


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("reference_number_counters"):
        op.create_table(
            "reference_number_counters",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("table_name", sa.String(128), nullable=False),
            sa.Column("column_name", sa.String(128), nullable=False),
            sa.Column("prefix", sa.String(64), nullable=False),
            sa.Column("last_value", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=True,
                server_default=sa.text("clock_timestamp()"),
            ),
            sa.UniqueConstraint(
                "table_name", "column_name", "prefix",
                name="uq_reference_number_counters_family",
            ),
        )

    _seed_counters(bind)


def downgrade():
    op.execute("DROP TABLE IF EXISTS reference_number_counters")
