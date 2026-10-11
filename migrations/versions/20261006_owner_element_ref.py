"""Let an ownership record point at any element, not only an application (R1-B03 PR 2).

Adds element_type/element_id to application_owners so the one ownership
record (R1-B03 PR 1) can own a capability -- or, later, any other element --
without a second owner table or a new owner column on the element's own
model (both forbidden by the brief). Nullable, additive: existing rows are
untouched, application_id stays as the application-specific path.

A second unique index mirrors the existing (application_id, user_id,
ownership_type) one for the element path: Postgres ignores NULLs in a
unique index, so this does not collide with, or get collided with by, the
existing application-only rows.

Revision ID: 20261006_owner_element_ref
Revises: 20261005_bf_capability_fk
Create Date: 2026-10-06
"""
from alembic import op
from sqlalchemy import text

revision = "20261006_owner_element_ref"
down_revision = "20261005_bf_capability_fk"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text(
        "ALTER TABLE application_owners ADD COLUMN IF NOT EXISTS element_type VARCHAR(30)"
    ))
    bind.execute(text(
        "ALTER TABLE application_owners ADD COLUMN IF NOT EXISTS element_id INTEGER"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_application_owners_element "
        "ON application_owners (element_type, element_id)"
    ))
    bind.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_application_owner_element_type "
        "ON application_owners (element_type, element_id, user_id, ownership_type)"
    ))


def downgrade():
    bind = op.get_bind()
    bind.execute(text("DROP INDEX IF EXISTS uq_application_owner_element_type"))
    bind.execute(text("DROP INDEX IF EXISTS ix_application_owners_element"))
    bind.execute(text("ALTER TABLE application_owners DROP COLUMN IF EXISTS element_id"))
    bind.execute(text("ALTER TABLE application_owners DROP COLUMN IF EXISTS element_type"))
