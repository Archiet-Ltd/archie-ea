"""arb_condition_evidence_records.condition_id is deferred, breaking the append-only cycle.

arb_canonical_conditions and arb_condition_evidence_records are mutually append-only and
reference each other (a condition's submitted/fulfilment evidence id; an evidence row's own
condition id), so the organisation-deletion planner has no row order that satisfies both
foreign keys at once, and neither table's guard admits the UPDATE a column-null cycle-break
would need -- only a DELETE, under the deletion marker, for a row that is this organisation's
own. Before this revision, deleting an organisation with real condition/evidence rows could
only be torn down in tests by disabling every trigger in the session
(``session_replication_role``), which this schema must never need.

Making the ``condition_id`` foreign key DEFERRABLE INITIALLY DEFERRED removes its ordering
requirement from the deletion planner outright, the same way
``fk_arb_submission_snapshot_review_item`` already does for the
arb_submission_evidence_snapshots/arb_review_items/arb_review_cycles ring: Postgres checks a
deferred constraint only at commit, and by commit every row on both sides of this cycle is
gone, so there is nothing left to check. This revision applies the exact same DDL
``ensure_arb_condition_evidence_guards`` (``app/models/arb_condition_evidence.py``) already
installs for a fresh or reconciled schema, so the two paths cannot drift apart.

Revision ID: 20261030_arb_cond_evidence_fk
Revises: 20261029_mapping_app_fk
Create Date: 2026-10-30
"""
from __future__ import annotations

from alembic import op

revision = "20261030_arb_cond_evidence_fk"
down_revision = "20261029_mapping_app_fk"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    from app.models.arb_condition_evidence import _ensure_condition_evidence_condition_fk

    _ensure_condition_evidence_condition_fk(connection)


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    tables_ready = connection.exec_driver_sql(
        "SELECT to_regclass(current_schema() || '.arb_condition_evidence_records') "
        "IS NOT NULL AND to_regclass(current_schema() || '.arb_canonical_conditions') IS NOT NULL"
    ).scalar()
    if not tables_ready:
        return
    connection.exec_driver_sql(
        "ALTER TABLE arb_condition_evidence_records "
        "DROP CONSTRAINT IF EXISTS fk_arb_condition_evidence_condition"
    )
    connection.exec_driver_sql(
        "ALTER TABLE arb_condition_evidence_records "
        "ADD CONSTRAINT arb_condition_evidence_records_condition_id_fkey "
        "FOREIGN KEY (condition_id) REFERENCES arb_canonical_conditions(id)"
    )
