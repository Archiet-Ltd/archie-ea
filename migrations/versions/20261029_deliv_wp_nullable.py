"""Drop the legacy NOT NULL on deliverables.work_package_id.

app/models/implementation_migration.py's Deliverable.work_package_id has been
nullable=True since R1-B04 PR 2: unified_work_package_id is the key every
reader uses, and the legacy key is only set when a work_packages row exists
for that work package. No revision in this chain ever relaxed the live
column -- the only place that happened was the one-shot
merge-work-package-stores data step
(app/commands/consolidate_work_packages.py's _ensure_deliverable_legacy_key_nullable),
which every already-deployed database has had run against it but which a
database built only from this chain never sees. So a deliverable added to
any work package with no legacy work_packages row behind it -- including
every deliverable seed_demo_company creates -- fails to insert on a
chain-built database with a NOT NULL violation. Expand step; the down step
restores NOT NULL and refuses, changing nothing, while any row holds NULL.

Revision ID: 20261029_deliv_wp_nullable
Revises: 20261029_llm_spend_ledger
Create Date: 2026-10-29
"""
from alembic import op

from app.commands.schema_migrations import relax_not_null, tighten_not_null

revision = "20261029_deliv_wp_nullable"
down_revision = "20261029_persona_default_unset"
branch_labels = None
depends_on = None

TABLE, COLUMN = "deliverables", "work_package_id"


def upgrade():
    relax_not_null(op.get_bind(), TABLE, COLUMN)


def downgrade():
    tighten_not_null(op.get_bind(), TABLE, COLUMN)
