"""Add access_review_cycles and access_review_items (quarterly recertification).

R1-B88: an administrator opens a cycle; every member's grant becomes one item
with the decision taken on it. Both tables carry the organisation (TenantMixin).
At most one open cycle per organisation, enforced by a partial unique index.

Every statement is idempotent: a fresh database may already have these tables
from create_all() (flask init-db) before this revision runs.

Revision ID: 20261008_access_review
Revises: 20261007_public_visitor_events
Create Date: 2026-10-08
"""
from alembic import op

revision = "20261008_access_review"
down_revision = "20261007_public_visitor_events"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS access_review_cycles (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            opened_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            opened_at TIMESTAMP NOT NULL,
            due_at TIMESTAMP NOT NULL,
            unused_days INTEGER NOT NULL,
            status VARCHAR(10) NOT NULL,
            closed_at TIMESTAMP,
            closed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            summary_json JSON
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_access_review_cycles_organization_id "
        "ON access_review_cycles (organization_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_access_review_cycles_status "
        "ON access_review_cycles (status)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_access_review_one_open "
        "ON access_review_cycles (organization_id) WHERE status = 'open'"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS access_review_items (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            cycle_id INTEGER NOT NULL REFERENCES access_review_cycles(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            org_role VARCHAR(50) NOT NULL,
            persona VARCHAR(80),
            last_activity_at TIMESTAMP,
            last_sign_in_at TIMESTAMP,
            unused BOOLEAN NOT NULL,
            decision VARCHAR(10) NOT NULL,
            decided_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            decided_at TIMESTAMP,
            note TEXT,
            CONSTRAINT uq_access_review_item_user UNIQUE (cycle_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_access_review_items_organization_id "
        "ON access_review_items (organization_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_access_review_items_cycle_id "
        "ON access_review_items (cycle_id)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_access_review_items_cycle_id")
    op.execute("DROP INDEX IF EXISTS ix_access_review_items_organization_id")
    op.execute("DROP TABLE IF EXISTS access_review_items")
    op.execute("DROP INDEX IF EXISTS uq_access_review_one_open")
    op.execute("DROP INDEX IF EXISTS ix_access_review_cycles_status")
    op.execute("DROP INDEX IF EXISTS ix_access_review_cycles_organization_id")
    op.execute("DROP TABLE IF EXISTS access_review_cycles")
