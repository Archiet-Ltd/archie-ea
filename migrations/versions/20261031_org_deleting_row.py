"""The append-only guards need proof of a real organisation deletion, not just a marker.

Setting the transaction-local ``archie.deleting_organisation`` marker used to be enough to delete
an organisation's append-only ARB and evidence history row by row, from any session. The shared
guard helper (``archie_org_deletion_admits``) now also requires a row in
``organization_deletions_in_progress`` naming that organisation, which only the deletion service
writes, first, in the transaction that deletes the organisation, and removes before it commits.

Adds that table (empty) and replaces the helper with the version that checks it. Nothing is
removed and no trigger is touched.

Revision ID: 20261031_org_deleting_row
Revises: 20261030_arb_cond_evidence_fk
Create Date: 2026-10-31
"""
from __future__ import annotations

from alembic import op

revision = "20261031_org_deleting_row"
down_revision = "20261030_arb_cond_evidence_fk"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    from app.models.transformation_db_guards import _ORG_DELETION_ADMITS_SQL

    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS public.organization_deletions_in_progress ("
        "target_organization_id INTEGER PRIMARY KEY, "
        "started_at TIMESTAMP NOT NULL DEFAULT now())"
    )
    connection.exec_driver_sql(_ORG_DELETION_ADMITS_SQL)


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    connection.exec_driver_sql(
        "CREATE OR REPLACE FUNCTION public.archie_org_deletion_admits(old_org integer) "
        "RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$ "
        "DECLARE marker text; BEGIN "
        "marker := current_setting('archie.deleting_organisation', TRUE); "
        "IF marker IS NULL OR marker = '' THEN RETURN FALSE; END IF; "
        "IF old_org IS NULL OR old_org <> marker::integer THEN RETURN FALSE; END IF; "
        "RETURN TRUE; END; $$"
    )
    connection.exec_driver_sql("DROP TABLE IF EXISTS public.organization_deletions_in_progress")
