"""Make the capability support table the organisation-fenced projection of support relationships.

Adds ``organization_id`` (filled from the application, then NOT NULL) and
``archimate_relationship_id`` (a foreign key that cascades, unique where set) to
``unified_application_capability_mapping``, indexes both lookups, and puts the table
under the same row-level security as the other organisation tables.

The downgrade drops the policies, the indexes and the two columns.

Revision ID: 20261114_capsupport_projection
Revises: 20261114_subscription_offer
Create Date: 2026-11-14
"""
from alembic import op
from sqlalchemy import text

revision = "20261114_capsupport_projection"
down_revision = "20261114_blank_none_desc"
branch_labels = None
depends_on = None

_ORG = "NULLIF(current_setting('archie.organization_id', true), '')::integer"
_PLATFORM = "COALESCE(current_setting('archie.platform_scope', true), '') = 'on'"
_TENANT_RULE = f"organization_id = {_ORG} OR {_PLATFORM}"

TENANT_POLICIES = ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete")
_TENANT_DEFINITIONS = (
    ("SELECT", _TENANT_RULE, None),
    ("INSERT", None, _TENANT_RULE),
    ("UPDATE", _TENANT_RULE, _TENANT_RULE),
    ("DELETE", _TENANT_RULE, None),
)

TENANT_TABLES = ("unified_application_capability_mapping",)
HYBRID_TABLES = ()

_TABLE = "unified_application_capability_mapping"
_INDEXES = (
    ("ix_uacm_org_capability", "organization_id, unified_capability_id"),
    ("ix_uacm_org_application", "organization_id, application_component_id"),
)
_UNIQUE = "uq_uacm_relationship"


def upgrade():
    bind = op.get_bind()
    present = bind.execute(text("SELECT to_regclass('public." + _TABLE + "') IS NOT NULL")).scalar()
    if not present:
        return
    bind.execute(text(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS organization_id INTEGER"))
    bind.execute(
        text(
            f"UPDATE {_TABLE} AS m SET organization_id = a.organization_id "  # nosec B608 - literal table name
            "FROM application_components AS a "
            "WHERE a.id = m.application_component_id AND m.organization_id IS NULL"
        )
    )
    # An application with no organisation of its own is placed by its capability. A row that neither
    # names is the only kind removed, and its count and ids are printed first.
    bind.execute(
        text(
            f"UPDATE {_TABLE} AS m SET organization_id = c.organization_id "  # nosec B608 - literal table name
            "FROM unified_capabilities AS c "
            "WHERE c.id = m.unified_capability_id AND m.organization_id IS NULL AND c.organization_id IS NOT NULL"
        )
    )
    unplaced = [
        r[0]
        for r in bind.execute(
            text(f"SELECT id FROM {_TABLE} WHERE organization_id IS NULL ORDER BY id")  # nosec B608 - literal table name
        )
    ]
    print(f"capability support: {len(unplaced)} row(s) with no application or capability organisation removed: {unplaced}")
    if unplaced:
        bind.execute(text(f"DELETE FROM {_TABLE} WHERE organization_id IS NULL"))  # nosec B608 - literal table name
    bind.execute(text(f"ALTER TABLE {_TABLE} ALTER COLUMN organization_id SET NOT NULL"))
    bind.execute(
        text(
            f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS archimate_relationship_id INTEGER "
            "REFERENCES archimate_relationships(id) ON DELETE CASCADE"
        )
    )
    bind.execute(
        text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_UNIQUE} ON {_TABLE} (archimate_relationship_id) "
            "WHERE archimate_relationship_id IS NOT NULL"
        )
    )
    for name, columns in _INDEXES:
        bind.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {_TABLE} ({columns})"))
    if bind.dialect.name == "postgresql":
        for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{_TABLE}"))  # nosec B608 - literal table name
            clause = ""
            if using is not None:
                clause += f" USING ({using})"
            if check is not None:
                clause += f" WITH CHECK ({check})"
            bind.execute(
                text(f"CREATE POLICY {name} ON public.{_TABLE} FOR {command}{clause}")  # nosec B608 - literal table name
            )
        bind.execute(text(f"ALTER TABLE public.{_TABLE} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table name
        bind.execute(text(f"ALTER TABLE public.{_TABLE} FORCE ROW LEVEL SECURITY"))  # nosec B608 - literal table name


def downgrade():
    bind = op.get_bind()
    present = bind.execute(text("SELECT to_regclass('public." + _TABLE + "') IS NOT NULL")).scalar()
    if not present:
        return
    if bind.dialect.name == "postgresql":
        for name in TENANT_POLICIES:
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{_TABLE}"))  # nosec B608 - literal table name
        bind.execute(text(f"ALTER TABLE public.{_TABLE} NO FORCE ROW LEVEL SECURITY"))  # nosec B608 - literal table name
        bind.execute(text(f"ALTER TABLE public.{_TABLE} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table name
    for name, _ in _INDEXES:
        bind.execute(text(f"DROP INDEX IF EXISTS {name}"))
    bind.execute(text(f"DROP INDEX IF EXISTS {_UNIQUE}"))
    bind.execute(text(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS archimate_relationship_id"))
    bind.execute(text(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS organization_id"))
