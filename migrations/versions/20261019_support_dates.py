"""End-of-support dates on the ArchiMate element.

Adds four nullable columns to archimate_elements -- support_end_date,
extended_support_end_date, support_source and support_verified_on -- so a
technology element carries its own support record instead of nothing at all
(the 55 demo technologies have no support-date record behind them today).

The same migration copies, one time, any Device or SystemSoftware row's
existing eos_date into its element's support_end_date: those two typed tables
already carry eos_date and nothing reads or writes it, so the date moves onto
the element where the end-of-support query reads it. Rows that already carry
a value are never overwritten, and every step checks before it acts, so a
second run is a no-op.

Revision ID: 20261019_support_dates
Revises: 20261018_tree_models_rls
Create Date: 2026-10-19
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261019_support_dates"
down_revision = "20261018_tree_models_rls"
branch_labels = None
depends_on = None

COLUMNS = {
    "support_end_date": sa.Date(),
    "extended_support_end_date": sa.Date(),
    "support_source": sa.String(128),
    "support_verified_on": sa.Date(),
}

# Typed tables that already carry eos_date, linked to their ArchiMate element.
SOURCES = ("technology_devices", "technology_system_software")


def _present(bind, table):
    return bool(bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())


def _has_column(bind, table, column):
    return bool(
        bind.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c)"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def upgrade():
    bind = op.get_bind()
    if not _present(bind, "archimate_elements"):
        return
    for column, type_ in COLUMNS.items():
        if not _has_column(bind, "archimate_elements", column):
            op.add_column("archimate_elements", sa.Column(column, type_, nullable=True))

    # One-time copy: a Device or SystemSoftware row's eos_date becomes its
    # element's support_end_date. Rows that already carry a value keep it.
    for source in SOURCES:
        if not (
            _present(bind, source)
            and _has_column(bind, source, "eos_date")
            and _has_column(bind, source, "archimate_element_id")
        ):
            continue
        bind.execute(
            text(
                f"UPDATE archimate_elements AS e SET support_end_date = s.eos_date "  # nosec B608 - literal names
                f"FROM {source} AS s WHERE s.archimate_element_id = e.id "
                "AND e.support_end_date IS NULL AND s.eos_date IS NOT NULL"
            )
        )


def downgrade():
    bind = op.get_bind()
    if not _present(bind, "archimate_elements"):
        return
    for column in COLUMNS:
        if _has_column(bind, "archimate_elements", column):
            op.drop_column("archimate_elements", column)
