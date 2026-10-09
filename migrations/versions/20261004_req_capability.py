"""Add requirements.capability_id (R1-B43 PR 2, TB-0188/PB-0190).

No requirement-to-capability association existed before this -- the
guided-design requirements-capture step traces a requirement to the
capability it realises, through unified_capabilities (ADR 0008's
canonical capability store), not a second one.

Revision ID: 20261004_req_capability
Revises: 20261007_public_visitor_events
Create Date: 2026-10-04

Re-chained (not re-dated) onto the real current head: this migration's
original down_revision (20261006_agent_registration) was main's head
only when the file was written. Main has since progressed through
20261005_bf_capability_fk -> 20261006_owner_element_ref ->
20261007_public_visitor_events, and this branch's own merge commits
never re-pointed this file onto that later chain, leaving two heads --
every job that boots the app against a real schema failed near-instantly
(CI run 37814213477). Root-caused and fixed by laptop-b-session2 (board
note 1044); `flask db heads` now reports a single head.
"""
from alembic import op
from sqlalchemy import text

revision = "20261004_req_capability"
down_revision = "20261007_public_visitor_events"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text(
        "ALTER TABLE requirements ADD COLUMN IF NOT EXISTS capability_id INTEGER "
        "REFERENCES unified_capabilities(id) ON DELETE SET NULL"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_requirements_capability_id ON requirements(capability_id)"
    ))
    bind.execute(text(
        "ALTER TABLE requirements ADD COLUMN IF NOT EXISTS solution_id INTEGER "
        "REFERENCES solutions(id) ON DELETE CASCADE"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_requirements_solution_id ON requirements(solution_id)"
    ))


def downgrade():
    bind = op.get_bind()
    bind.execute(text("DROP INDEX IF EXISTS ix_requirements_solution_id"))
    bind.execute(text("ALTER TABLE requirements DROP COLUMN IF EXISTS solution_id"))
    bind.execute(text("DROP INDEX IF EXISTS ix_requirements_capability_id"))
    bind.execute(text("ALTER TABLE requirements DROP COLUMN IF EXISTS capability_id"))
