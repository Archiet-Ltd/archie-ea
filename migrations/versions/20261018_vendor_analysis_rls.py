"""Row-level security on the vendor options analysis tables.

A new fencing revision for the five child tables that gained the tenant mixin
with the vendor options analysis fix: ``vendor_options``,
``analysis_recommendations``, ``analysis_scenarios``, ``required_capabilities``
and ``analysis_audit_logs``.
``tests/test_row_level_security.py::test_every_tenant_and_hybrid_model_is_listed_or_excluded``
unions every revision carrying a ``TENANT_TABLES``/``HYBRID_TABLES`` constant,
so this file alone brings the tables under the guard; the base revision's
helper functions and policy text are copied here rather than imported.

The column is added here, ahead of the policy, because on a real deploy
``schema-upgrade`` runs before ``reconcile-schema``: a revision that only checked
for an existing column would find none, apply no policy, and still be recorded as
applied. Each table gets a nullable ``organization_id``, an index and a foreign
key to ``organizations``, then a backfill, then the policies.

The backfill has one rule, parent first: a row belongs to the organisation of its
own analysis (``analysis_id`` -> ``options_analysis.organization_id``), which is
stamped from the caller's organisation when the analysis is created. Only when
that value is NULL does it fall back to the organisation owning the analysis's
capability (``options_analysis.capability_id`` -> ``business_capability.organization_id``).
The capability is not authoritative: ``capability_id`` is taken from the request
with no ownership check, so it can name another organisation's capability. The
rule only fills: a row that already carries an organisation is never changed.
A row whose chain does not resolve is left NULL, the count is printed and the
deploy carries on; it matches no organisation's policy and is visible to the
platform scope alone until it is attributed. This is the only derivation of
these five tables' organisation; ``backfill-layer-tenancy`` no longer has one.

``downgrade()`` reverses the policies and row-level security only. It
deliberately leaves the ``organization_id`` column, its index and its foreign
key in place: they are inert without the policies, and dropping the column
would discard the attribution this revision computed.

Idempotent: every step checks before it acts, so a second run is a no-op.

Revision ID: 20261018_vendor_analysis_rls
Revises: 20261016_compliance_org_rls
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261018_vendor_analysis_rls"
down_revision = "20261016_compliance_org_rls"
branch_labels = None
depends_on = None


# Identical text to 20261008_row_level_security's own constants -- copied, not
# imported, so this revision stays runnable on its own if the base file ever
# changes.
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

# Only the tables this revision exists to fence. The guard unions this with
# every other revision's TENANT_TABLES/HYBRID_TABLES -- do not add tables
# already listed in 20261008_row_level_security here.
TENANT_TABLES = (
    "vendor_options",
    "analysis_recommendations",
    "analysis_scenarios",
    "required_capabilities",
    "analysis_audit_logs",
)
HYBRID_TABLES = ()


def _present(bind, table: str) -> bool:
    return bool(
        bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar()
    )


def _has_column(bind, table: str, column: str) -> bool:
    return bool(
        bind.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c)"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _add_column(bind, table: str) -> None:
    """Nullable organization_id, index and foreign key; each step checks first."""
    if not _has_column(bind, table, "organization_id"):
        op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))
    op.create_index(f"ix_{table}_organization_id", table, ["organization_id"], if_not_exists=True)
    # Detect by column, not by name: a foreign key already on organization_id
    # under any other name must not get a second one beside it.
    has_fk = bind.execute(
        text(
            "SELECT 1 FROM pg_constraint con "
            "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey) "
            "WHERE con.contype = 'f' AND con.conrelid = to_regclass(:t) "
            "AND att.attname = 'organization_id'"
        ),
        {"t": f"public.{table}"},
    ).scalar()
    if not has_fk:
        op.create_foreign_key(
            f"fk_{table}_organization_id", table, "organizations", ["organization_id"], ["id"], ondelete="CASCADE"
        )


def _can_derive(bind) -> bool:
    return (
        _present(bind, "options_analysis")
        and _present(bind, "business_capability")
        and _has_column(bind, "options_analysis", "capability_id")
        and _has_column(bind, "business_capability", "organization_id")
    )


def _backfill(bind, table: str) -> None:
    """Fill a row from its analysis's own organisation, else its capability's; never overwrite."""
    if not _has_column(bind, table, "analysis_id"):
        return
    parent = "a.organization_id" if _has_column(bind, "options_analysis", "organization_id") else "NULL"
    # Table name is a literal module constant; nothing here is user input.
    bind.execute(
        text(
            f"UPDATE {table} AS c SET organization_id = COALESCE({parent}, b.organization_id) "  # nosec B608 - table names from a literal constant
            "FROM options_analysis AS a LEFT JOIN business_capability AS b ON b.id = a.capability_id "
            f"WHERE c.analysis_id = a.id AND c.organization_id IS NULL "
            f"AND COALESCE({parent}, b.organization_id) IS NOT NULL"
        )
    )


def _skip_reason(bind, table: str):
    if not _present(bind, table):
        return "absent"
    if not _has_column(bind, table, "organization_id"):
        print(f"WARNING row-level security skipped: {table} has no organization_id column")
        return "no organization_id column"
    may_alter = bind.execute(
        text(
            "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
            "FROM pg_class c WHERE c.oid = to_regclass(:t)"
        ),
        {"t": f"public.{table}"},
    ).scalar()
    if not may_alter:
        print(f"WARNING row-level security skipped: {table} is not owned by the migration role")
        return "not owned by the migration role"
    return None


def _fence(bind, table: str) -> None:
    # Table name is a literal module constant; nothing here is user input.
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - table names from a literal constant
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(
            text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}")  # nosec B608 - table names from a literal constant
        )
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - table names from a literal constant


def upgrade():
    bind = op.get_bind()
    derivable = _can_derive(bind)
    for table in TENANT_TABLES:
        if not _present(bind, table):
            continue
        _add_column(bind, table)
        if derivable:
            _backfill(bind, table)
        remaining = bind.execute(text(f"SELECT COUNT(*) FROM {table} WHERE organization_id IS NULL")).scalar()  # nosec B608 - literal names
        if remaining:
            print(
                f"vendor analysis org fence: {remaining} row(s) in {table} have no derivable "
                "organisation; left NULL"
            )

    for table in TENANT_TABLES:
        reason = _skip_reason(bind, table)
        if reason is not None:
            print(f"row-level security: skipped {table} ({reason})")
            continue
        _fence(bind, table)
        print(f"row-level security: fenced {table}")


def downgrade():
    bind = op.get_bind()
    for table in TENANT_TABLES:
        if _skip_reason(bind, table) is not None:
            continue
        for name in TENANT_POLICIES:
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - table names from a literal constant
        bind.execute(text(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - table names from a literal constant
