"""Correct the sample company's origin and ownership on rows it already added.

Before this, a relationship written while loading the sample company was stamped
"manual", naming the person who clicked Load the sample company as its drawer
(``app/services/relationship_origin.py``'s before_insert listener runs ahead of
the sample loader's own citation), and every sample application and capability
named that same person as its owner. Both read as invented attribution on data
the sample file itself provided, not anyone's own work (CLAUDE.md "never invent
data"). The code path is fixed; this corrects every sample company already
loaded, from the one record of what each load added
(``import_audit_log.snapshot_data``) — nothing here guesses at a sample load the
code cannot already point to.

Idempotent: a second run finds the same rows already in their corrected state and
changes nothing further (the relationship update does not re-narrow on a label it
has already set). Data-only; no column changes, so a from-scratch database is
unaffected.

Revision ID: 20261030_sample_company_origin
Revises: 20261029_persona_default_unset
Create Date: 2026-10-30
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "20261030_sample_company_origin"
down_revision = "20261029_persona_default_unset"
branch_labels = None
depends_on = None

SOURCE_LABEL = "Sample company"
IMPORT_SOURCE = "oef_model_import"
MODEL_NAME = "Sample company"


def _int_ids(values) -> list[int]:
    out = []
    for value in values or []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def upgrade() -> None:
    bind = op.get_bind()
    logs = bind.execute(
        sa.text(
            "SELECT id, organization_id, snapshot_data FROM import_audit_log "
            "WHERE import_source = :source AND snapshot_data IS NOT NULL "
            "AND snapshot_data->>'model_name' = :model_name"
        ),
        {"source": IMPORT_SOURCE, "model_name": MODEL_NAME},
    ).fetchall()

    for _log_id, org_id, snapshot_data in logs:
        if org_id is None:
            continue
        data = snapshot_data if isinstance(snapshot_data, dict) else json.loads(snapshot_data or "{}")
        element_ids = _int_ids(data.get("created_element_ids"))
        sample_rows = data.get("sample_rows") or []

        if element_ids:
            bind.execute(
                sa.text(
                    "UPDATE archimate_elements SET source_kind = 'connector', source_label = :label, "
                    "source_recorded_at = COALESCE(source_recorded_at, recorded_at, now()) "
                    "WHERE organization_id = :org_id AND id = ANY(:ids)"
                ),
                {"label": SOURCE_LABEL, "org_id": org_id, "ids": element_ids},
            )
            # A relationship this load drew between two of its own elements, not
            # already carrying a real, named origin of its own.
            bind.execute(
                sa.text(
                    "UPDATE archimate_relationships SET source_kind = 'connector', source_label = :label, "
                    "source_recorded_at = COALESCE(source_recorded_at, created_at, now()) "
                    "WHERE organization_id = :org_id "
                    "AND source_id = ANY(:ids) AND target_id = ANY(:ids) "
                    "AND (source_label IS NULL OR source_label = '')"
                ),
                {"label": SOURCE_LABEL, "org_id": org_id, "ids": element_ids},
            )

        owner_ids = _int_ids(r["id"] for r in sample_rows if r.get("table") == "application_owners")
        if owner_ids:
            bind.execute(
                sa.text("DELETE FROM application_owners WHERE id = ANY(:ids) AND organization_id = :org_id"),
                {"ids": owner_ids, "org_id": org_id},
            )

        app_ids = _int_ids(r["id"] for r in sample_rows if r.get("table") == "application_components")
        if app_ids:
            bind.execute(
                sa.text(
                    "UPDATE application_components SET application_owner = NULL, business_owner = NULL "
                    "WHERE id = ANY(:ids) AND organization_id = :org_id"
                ),
                {"ids": app_ids, "org_id": org_id},
            )

        cap_ids = _int_ids(r["id"] for r in sample_rows if r.get("table") == "business_capability")
        if cap_ids:
            bind.execute(
                sa.text(
                    "UPDATE business_capability SET business_owner = NULL "
                    "WHERE id = ANY(:ids) AND organization_id = :org_id"
                ),
                {"ids": cap_ids, "org_id": org_id},
            )


def downgrade() -> None:
    # A correction of fabricated attribution is not reversed: the prior (wrong)
    # owner names and "Drawn by" origins are not recoverable from here, and
    # restoring them would reintroduce the defect this revision fixes.
    pass
