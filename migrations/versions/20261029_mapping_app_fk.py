"""unified_application_capability_mapping follows application_components by a declared foreign key.

A review found ``unified_application_capability_mapping`` still referencing
``application_components`` after an organisation delete: the model declares
``application_component_id = ForeignKey("application_components.id")``, so a database created
fresh by ``flask init-db`` always carries the constraint, but a database whose
``unified_application_capability_mapping`` table predates that declaration never gained it —
``reconcile-schema`` only adds columns, never constraints. Without the constraint in
``pg_constraint``, ``OrganizationDeletionService`` (which reads the schema's own foreign keys,
not a hand-kept list) cannot see that a mapping row points at an application component, so
deleting the organisation leaves the mapping row behind, now pointing at a row that no longer
exists.

Orphan rows (a mapping whose ``application_component_id`` matches no
``application_components.id``) cannot coexist with the constraint; a database carrying one from
before this fix is cleaned up first, since a dangling reference to an application that no longer
exists answers no question a reader could rely on. Every orphan row is first copied, whole, into
``unified_application_capability_mapping_removed`` (with the time it was archived), so the removal
can be reversed by copying the rows back.

Revision ID: 20261029_mapping_app_fk
Revises: 20261029_org_deletion_guards
Create Date: 2026-10-29
"""
from __future__ import annotations

from alembic import op

revision = "20261029_mapping_app_fk"
down_revision = "20261029_org_deletion_guards"
branch_labels = None
depends_on = None

_ARCHIVE = "unified_application_capability_mapping_removed"
_CONSTRAINT = "unified_application_capability_ma_application_component_id_fkey"


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    existing = connection.exec_driver_sql(
        "SELECT 1 FROM pg_constraint WHERE conname = %s", (_CONSTRAINT,)
    ).scalar()
    if existing:
        return
    orphans = (
        "FROM unified_application_capability_mapping AS mapping "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM application_components AS app "
        "  WHERE app.id = mapping.application_component_id"
        ")"
    )
    if connection.exec_driver_sql(f"SELECT 1 {orphans} LIMIT 1").first():
        connection.exec_driver_sql(
            f"CREATE TABLE IF NOT EXISTS {_ARCHIVE} "
            f"(LIKE unified_application_capability_mapping, archived_at TIMESTAMP NOT NULL DEFAULT now())"
        )
        archived = connection.exec_driver_sql(
            f"INSERT INTO {_ARCHIVE} SELECT mapping.* {orphans}"
        ).rowcount
        removed = connection.exec_driver_sql(
            "DELETE FROM unified_application_capability_mapping AS mapping WHERE NOT EXISTS ("
            "  SELECT 1 FROM application_components AS app "
            "  WHERE app.id = mapping.application_component_id)"
        ).rowcount
        print(
            f"unified_application_capability_mapping: {removed} orphan row(s) removed before the "
            f"foreign key, {archived} copied first into {_ARCHIVE}"
        )
    connection.exec_driver_sql(
        f"ALTER TABLE unified_application_capability_mapping "
        f"ADD CONSTRAINT {_CONSTRAINT} "
        f"FOREIGN KEY (application_component_id) REFERENCES application_components(id)"
    )


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    connection.exec_driver_sql(
        f"ALTER TABLE unified_application_capability_mapping "
        f"DROP CONSTRAINT IF EXISTS {_CONSTRAINT}"
    )
