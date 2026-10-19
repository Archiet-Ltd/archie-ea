"""Give seven hierarchy tables an organisation.

tasks, portfolio_initiatives, system_boundaries, compliance_requirements,
vendor_capability_hierarchy, vendor_process_hierarchy and required_capabilities
carried no organisation column, so the generic tree API served and changed one
organisation's rows for every other. They now carry ``organization_id``
(``TenantMixin``).

Backfill fails closed. A row takes the organisation of the record it hangs off
(its project, vendor stack template, options analysis or ArchiMate element),
then of the user who created it, then of its parent row in the same hierarchy.
A row with none of these has no derivable organisation: none is guessed, it
stays NULL, matches no organisation's filter, and the column is only tightened
to NOT NULL on a table where no NULL remains. The number of unattributed rows
is printed.

Idempotent: every step checks before it acts, so a second run is a no-op.

Revision ID: 20261018_tree_models_org_id
Revises: 20261018_vendor_analysis_rls
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261018_tree_models_org_id"
down_revision = "20261018_vendor_analysis_rls"
branch_labels = None
depends_on = None

# table -> (parent column in the same table, ((source table, join column), ...))
# Each source table is assumed to carry organization_id; a missing one is skipped.
TABLES = {
    "tasks": ("parent_task_id", (("projects", "project_id"), ("requirements", "requirement_id"))),
    "portfolio_initiatives": (
        "parent_initiative_id",
        (("archimate_elements", "archimate_element_id"), ("users", "created_by_id")),
    ),
    "system_boundaries": (
        "parent_boundary_id",
        (("archimate_elements", "archimate_element_id"), ("users", "created_by_id")),
    ),
    "compliance_requirements": (
        "parent_requirement_id",
        (
            ("archimate_elements", "archimate_element_id"),
            ("business_capability", "applies_to_capability_id"),
            ("users", "created_by_id"),
        ),
    ),
    "vendor_capability_hierarchy": ("parent_id", (("vendor_stack_templates", "template_id"),)),
    "vendor_process_hierarchy": ("parent_id", (("vendor_stack_templates", "template_id"),)),
    "required_capabilities": ("parent_capability_id", (("options_analysis", "analysis_id"),)),
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
    for table, (parent_col, sources) in TABLES.items():
        if not _present(bind, table):
            continue
        if not _has_column(bind, table, "organization_id"):
            op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))

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

        # A child with nothing else to go on takes its parent's organisation. Repeat
        # until stable so a chain of any depth resolves; each pass only fills NULLs.
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
        if table == "required_capabilities":
            # organization_id on this table is also owned by
            # 20261018_vendor_analysis_rls, which now runs earlier in this
            # chain and keeps its own column, index and foreign key in place
            # on downgrade for the same reason: dropping them here would
            # discard that revision's attribution too, not just this one's.
            continue
        fk = f"fk_{table}_organization_id"
        if bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
            op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_index(f"ix_{table}_organization_id", table_name=table, if_exists=True)
        op.drop_column(table, "organization_id")
