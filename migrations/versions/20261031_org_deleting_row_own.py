"""The deletion-in-progress row must have been written by the transaction that deletes.

The guard helper ``archie_org_deletion_admits`` admitted a delete of append-only history when a
deletion-in-progress row for the organisation existed. It now also requires that this transaction
wrote that row (a column filled with the writing transaction's id), so a row left by another session does not
open history to a session that only sets the marker.

Adds one column to the (empty) table and replaces the function; nothing is removed.

Revision ID: 20261031_org_deleting_row_own
Revises: 20261031_org_deleting_row
Create Date: 2026-10-31
"""
from __future__ import annotations

from alembic import op

revision = "20261031_org_deleting_row_own"
down_revision = "20261031_org_deleting_row"
branch_labels = None
depends_on = None

_PREVIOUS_SQL = r"""
CREATE OR REPLACE FUNCTION public.archie_org_deletion_admits(old_org integer)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    marker text;
BEGIN
    marker := current_setting('archie.deleting_organisation', TRUE);
    IF marker IS NULL OR marker = '' THEN
        RETURN FALSE;
    END IF;
    IF old_org IS NULL OR old_org <> marker::integer THEN
        RETURN FALSE;
    END IF;
    IF to_regclass('public.organization_deletions_in_progress') IS NULL THEN
        RETURN FALSE;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.organization_deletions_in_progress
        WHERE target_organization_id = old_org
    );
END;
$$
"""


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    from app.models.transformation_db_guards import _ORG_DELETION_ADMITS_SQL

    connection.exec_driver_sql(
        "ALTER TABLE public.organization_deletions_in_progress "
        "ADD COLUMN IF NOT EXISTS written_by_txid BIGINT NOT NULL DEFAULT txid_current()"
    )
    connection.exec_driver_sql(_ORG_DELETION_ADMITS_SQL)


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    connection.exec_driver_sql(_PREVIOUS_SQL)
    connection.exec_driver_sql(
        "ALTER TABLE public.organization_deletions_in_progress DROP COLUMN IF EXISTS written_by_txid"
    )
