"""Give six hierarchy tables an organisation.

tasks, portfolio_initiatives, system_boundaries, compliance_requirements,
vendor_capability_hierarchy and vendor_process_hierarchy carried no
organisation column, so the generic tree API served and changed one
organisation's rows for every other. They now carry ``organization_id``
(``TenantMixin``).

required_capabilities is not one of them, even though it is one of the
generic tree API's registered trees and carried the same gap: it already has
a derivation and a fence, in ``20261018_vendor_analysis_rls`` (that
revision's own docstring calls it "the only derivation of these five tables'
organisation"), and that revision deliberately leaves the column nullable for
rows it cannot derive. A second, competing derivation here -- on a table that
revision does not expect anyone else to touch -- is exactly the duplication
``docs/reuse-register.yml``-style checks exist to catch, not a second,
independent feature.

Backfill fails closed. A row takes the organisation of the record it hangs
off (its project, vendor stack template or ArchiMate element), then of its
parent row in the same hierarchy, then, last, of the user who created it --
parent-derivation before the creating-user fallback, the same order
``app/commands/backfill_layer_tenancy.py`` uses elsewhere in this codebase.
A row with none of these has no derivable organisation: none is guessed, it
stays NULL, matches no organisation's filter, and the column is only
tightened to NOT NULL on a table where no NULL remains. The number of
unattributed rows is printed.

Idempotent: every step checks before it acts, so a second run is a no-op.

Revision ID: 20261018_tree_models_org_id
Revises: 20261018_typed_tables_rls
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261018_tree_models_org_id"
down_revision = "20261018_typed_tables_rls"
branch_labels = None
depends_on = None

# table -> (parent column in the same table, ((source table, join column), ...),
#           creating-user column or None)
# Each source table is assumed to carry organization_id; a missing one is
# skipped. The creating-user fallback, when present, always runs last --
# after every other source and after the parent-chain walk below -- never
# before the row's own parent, matching backfill_layer_tenancy.py's
# COALESCE ordering (parent/linked-entity derivation first, creator last).
TABLES = {
    "tasks": ("parent_task_id", (("projects", "project_id"), ("requirements", "requirement_id")), None),
    "portfolio_initiatives": (
        "parent_initiative_id",
        (("archimate_elements", "archimate_element_id"),),
        "created_by_id",
    ),
    "system_boundaries": (
        "parent_boundary_id",
        (("archimate_elements", "archimate_element_id"),),
        "created_by_id",
    ),
    "compliance_requirements": (
        "parent_requirement_id",
        (
            ("archimate_elements", "archimate_element_id"),
            ("business_capability", "applies_to_capability_id"),
        ),
        "created_by_id",
    ),
    "vendor_capability_hierarchy": ("parent_id", (("vendor_stack_templates", "template_id"),), None),
    "vendor_process_hierarchy": ("parent_id", (("vendor_stack_templates", "template_id"),), None),
}


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


def upgrade():
    bind = op.get_bind()
    for table, (parent_col, sources, creator_col) in TABLES.items():
        if not _present(bind, table):
            continue
        if not _has_column(bind, table, "organization_id"):
            op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))

        # 1. The record this row hangs off (its project, vendor stack
        # template or ArchiMate element).
        for source, column in sources:
            if not (
                _present(bind, source)
                and _has_column(bind, source, "organization_id")
                and _has_column(bind, table, column)
            ):
                continue
            bind.execute(
                text(
                    f"UPDATE {table} AS t SET organization_id = s.organization_id "  # nosec B608 - literal names
                    f"FROM {source} AS s WHERE t.{column} = s.id "
                    "AND t.organization_id IS NULL AND s.organization_id IS NOT NULL"
                )
            )

        # 2. A child with nothing else to go on takes its parent's
        # organisation. Repeat until stable so a chain of any depth
        # resolves; each pass only fills NULLs.
        if _has_column(bind, table, parent_col):
            for _ in range(32):
                filled = bind.execute(
                    text(
                        f"UPDATE {table} AS c SET organization_id = p.organization_id "  # nosec B608 - literal names
                        f"FROM {table} AS p WHERE c.{parent_col} = p.id "
                        "AND c.organization_id IS NULL AND p.organization_id IS NOT NULL"
                    )
                ).rowcount
                if not filled:
                    break

        # 3. Last resort: the user who created it. Only reached once
        # neither the row's own linked entity nor its parent resolved it.
        if creator_col and _has_column(bind, table, creator_col) and _has_column(bind, "users", "organization_id"):
            bind.execute(
                text(
                    f"UPDATE {table} AS t SET organization_id = u.organization_id "  # nosec B608 - literal names
                    f"FROM users AS u WHERE t.{creator_col} = u.id "
                    "AND t.organization_id IS NULL AND u.organization_id IS NOT NULL"
                )
            )

        remaining = bind.execute(text(f"SELECT COUNT(*) FROM {table} WHERE organization_id IS NULL")).scalar()  # nosec B608 - literal names
        if remaining:
            print(f"tree model org fence: {remaining} row(s) in {table} have no derivable organisation; left NULL")
        else:
            op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=False)
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"], if_not_exists=True)
        # Detect by column, not by name: a foreign key already on organization_id
        # under any other name (e.g. one created by another revision sharing this
        # table) must not get a second one beside it.
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


def downgrade():
    bind = op.get_bind()
    for table in TABLES:
        if not _present(bind, table) or not _has_column(bind, table, "organization_id"):
            continue
        fk = f"fk_{table}_organization_id"
        if bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
            op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_index(f"ix_{table}_organization_id", table_name=table, if_exists=True)
        op.drop_column(table, "organization_id")
