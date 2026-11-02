"""Give consolidation candidates, opportunities and savings an organisation.

consolidation_candidates, consolidation_opportunities and savings_realizations
were global: every organisation read every other's duplicate-application
candidates, consolidation plans and realised savings. Each now carries
``organization_id`` (``TenantMixin``), NOT NULL, with a foreign key to
organizations, an index, and row-level security as the other tenant tables have it.

Backfill, in this order:
  * a candidate takes the organisation of its primary application, or of its
    duplicate application when the primary has none;
  * an opportunity takes the organisation of its target application, or of the
    first of its source applications that has one;
  * a savings row takes the organisation of its opportunity.
A row whose linked applications belong to more than one organisation is given the
first organisation above and counted as ambiguous. A row with no organisation
derivable at all is deleted (savings go with their opportunity) and counted.
Every count is printed.

Idempotent: every step checks before it acts, so a second run is a no-op.

Revision ID: 20261102_consolidation_org
Revises: 20261030_rate_limits
Create Date: 2026-11-02
"""
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261102_consolidation_org"
down_revision = "20261030_rate_limits"
branch_labels = None
depends_on = None

TABLES = ("consolidation_candidates", "consolidation_opportunities", "savings_realizations")

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

# Only the tables this revision exists to fence.
TENANT_TABLES = TABLES
HYBRID_TABLES = ()


def _present(bind, table):
    return bool(bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())


def _has_column(bind, table, column):
    return bool(
        bind.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c)"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _scalar(bind, sql):
    return bind.execute(text(sql)).scalar()  # nosec B608 - literal statements in this module


def _backfill_candidates(bind):
    bind.execute(
        text(
            "UPDATE consolidation_candidates c SET organization_id = COALESCE(p.organization_id, d.organization_id) "
            "FROM application_components p, application_components d "
            "WHERE c.organization_id IS NULL AND p.id = c.primary_application_id "
            "AND d.id = c.duplicate_application_id "
            "AND COALESCE(p.organization_id, d.organization_id) IS NOT NULL"
        )
    )


def _backfill_opportunities(bind):
    rows = bind.execute(
        text(
            "SELECT o.id, o.source_applications, t.organization_id FROM consolidation_opportunities o "
            "LEFT JOIN application_components t ON t.id = o.target_application_id "
            "WHERE o.organization_id IS NULL"
        )
    ).all()
    ambiguous = 0
    for opp_id, sources, target_org in rows:
        try:
            ids = [int(i) for i in (json.loads(sources) if sources else [])]
        except (ValueError, TypeError):
            ids = []
        orgs = []
        if ids:
            found = bind.execute(
                text("SELECT id, organization_id FROM application_components WHERE id = ANY(:ids)"),
                {"ids": ids},
            ).all()
            by_id = {i: o for i, o in found}
            orgs = [by_id[i] for i in ids if by_id.get(i) is not None]
        candidates = ([target_org] if target_org is not None else []) + orgs
        if not candidates:
            continue
        if len(set(candidates)) > 1:
            ambiguous += 1
        bind.execute(
            text("UPDATE consolidation_opportunities SET organization_id = :o WHERE id = :i"),
            {"o": candidates[0], "i": opp_id},
        )
    return ambiguous


def _ambiguous_candidates(bind):
    return _scalar(
        bind,
        "SELECT COUNT(*) FROM consolidation_candidates c "
        "JOIN application_components p ON p.id = c.primary_application_id "
        "JOIN application_components d ON d.id = c.duplicate_application_id "
        "WHERE p.organization_id IS NOT NULL AND d.organization_id IS NOT NULL "
        "AND p.organization_id <> d.organization_id",
    )


def _add_column(bind, table):
    if not _has_column(bind, table, "organization_id"):
        op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))


def _fence(bind, table):
    # Table name is a literal module constant; nothing here is user input.
    owned = bind.execute(
        text("SELECT pg_has_role(current_user, c.relowner, 'USAGE') FROM pg_class c WHERE c.oid = to_regclass(:t)"),
        {"t": f"public.{table}"},
    ).scalar()
    if not owned:
        print(f"WARNING row-level security skipped: {table} is not owned by the migration role")
        return
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal table names
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}"))  # nosec B608 - literal table names
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table names
    print(f"row-level security: fenced {table}")


def upgrade():
    bind = op.get_bind()
    present = [t for t in TABLES if _present(bind, t)]
    for table in present:
        _add_column(bind, table)

    if "consolidation_candidates" in present and _present(bind, "application_components"):
        ambiguous = _ambiguous_candidates(bind) if _has_column(bind, "application_components", "organization_id") else 0
        _backfill_candidates(bind)
        print(f"consolidation org fence: {ambiguous} candidate(s) link applications of more than one organisation; given the primary application's")
    if "consolidation_opportunities" in present and _present(bind, "application_components"):
        ambiguous = _backfill_opportunities(bind)
        print(f"consolidation org fence: {ambiguous} opportunity(ies) link applications of more than one organisation; given the target's")
    if "savings_realizations" in present and "consolidation_opportunities" in present:
        bind.execute(
            text(
                "UPDATE savings_realizations s SET organization_id = o.organization_id "
                "FROM consolidation_opportunities o WHERE s.opportunity_id = o.id "
                "AND s.organization_id IS NULL AND o.organization_id IS NOT NULL"
            )
        )

    # Rows nothing links to an organisation cannot be shown to anyone safely.
    if "savings_realizations" in present:
        gone = _scalar(bind, "SELECT COUNT(*) FROM savings_realizations WHERE organization_id IS NULL")
        bind.execute(text("DELETE FROM savings_realizations WHERE organization_id IS NULL"))
        print(f"consolidation org fence: {gone} unattributable savings row(s) deleted")
    if "consolidation_opportunities" in present:
        gone = _scalar(bind, "SELECT COUNT(*) FROM consolidation_opportunities WHERE organization_id IS NULL")
        bind.execute(text("DELETE FROM consolidation_opportunities WHERE organization_id IS NULL"))
        print(f"consolidation org fence: {gone} unattributable opportunity(ies) deleted")
    if "consolidation_candidates" in present:
        gone = _scalar(bind, "SELECT COUNT(*) FROM consolidation_candidates WHERE organization_id IS NULL")
        bind.execute(text("DELETE FROM consolidation_candidates WHERE organization_id IS NULL"))
        print(f"consolidation org fence: {gone} unattributable candidate(s) deleted")

    for table in present:
        op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=False)
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"], if_not_exists=True)
        fk = f"fk_{table}_organization_id"
        if not bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
            op.create_foreign_key(fk, table, "organizations", ["organization_id"], ["id"], ondelete="CASCADE")
        _fence(bind, table)


def downgrade():
    bind = op.get_bind()
    for table in TABLES:
        if not _present(bind, table) or not _has_column(bind, table, "organization_id"):
            continue
        bind.execute(text(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table names
        for name in TENANT_POLICIES:
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal table names
        fk = f"fk_{table}_organization_id"
        if bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
            op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_index(f"ix_{table}_organization_id", table_name=table, if_exists=True)
        op.drop_column(table, "organization_id")
