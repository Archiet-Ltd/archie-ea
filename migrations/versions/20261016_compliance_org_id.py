"""Give compliance_policies and compliance_violations an organisation.

Both tables were global: a policy written by an administrator of one
organisation was listed to every signed-in user of every other. They now carry
``organization_id`` (``TenantMixin``) and a policy name is unique within an
organisation instead of across the platform.

Backfill fails closed. A violation takes the organisation of its policy. A
policy has no derivable organisation (the table never recorded one), so none is
guessed: policy rows stay NULL, are invisible to every organisation, and the
column is only tightened to NOT NULL on a table where no NULL remains. The
number of unattributed rows is printed.

Idempotent: every step checks before it acts, so a second run is a no-op.

Revision ID: 20261016_compliance_org_id
Revises: 20261015_adm_portfolio_rls
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261016_compliance_org_id"
down_revision = "20261015_adm_portfolio_rls"
branch_labels = None
depends_on = None

TABLES = ("compliance_policies", "compliance_violations")
NAME_UNIQUE = "uq_compliance_policies_org_name"


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


def _drop_single_column_name_unique(bind):
    """Drop the old platform-wide unique on compliance_policies.name, however it was created."""
    constraints = bind.execute(
        text(
            "SELECT c.conname FROM pg_constraint c WHERE c.conrelid = to_regclass('public.compliance_policies') "
            "AND c.contype = 'u' AND (SELECT array_agg(a.attname::text) FROM unnest(c.conkey) k "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k) = ARRAY['name']"
        )
    ).all()
    for (name,) in constraints:
        bind.execute(text(f'ALTER TABLE public.compliance_policies DROP CONSTRAINT "{name}"'))  # nosec B608 - name read from the catalogue
    indexes = bind.execute(
        text(
            "SELECT i.relname FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
            "WHERE x.indrelid = to_regclass('public.compliance_policies') AND x.indisunique "
            "AND NOT x.indisprimary AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = x.indexrelid) "
            "AND (SELECT array_agg(a.attname::text) FROM unnest(x.indkey::int2[]) k "
            "JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k) = ARRAY['name']"
        )
    ).all()
    for (name,) in indexes:
        bind.execute(text(f'DROP INDEX IF EXISTS public."{name}"'))  # nosec B608 - name read from the catalogue


def upgrade():
    bind = op.get_bind()
    for table in TABLES:
        if not _present(bind, table):
            continue
        if not _has_column(bind, table, "organization_id"):
            op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))

    if _present(bind, "compliance_violations") and _present(bind, "compliance_policies"):
        bind.execute(
            text(
                "UPDATE compliance_violations AS v SET organization_id = p.organization_id "
                "FROM compliance_policies AS p WHERE v.policy_id = p.id "
                "AND v.organization_id IS NULL AND p.organization_id IS NOT NULL"
            )
        )

    for table in TABLES:
        if not _present(bind, table):
            continue
        remaining = bind.execute(text(f"SELECT COUNT(*) FROM {table} WHERE organization_id IS NULL")).scalar()  # nosec B608 - literal names
        if remaining:
            print(f"compliance org fence: {remaining} row(s) in {table} have no derivable organisation; left NULL")
        else:
            op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=False)
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"], if_not_exists=True)
        fk = f"fk_{table}_organization_id"
        if not bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
            op.create_foreign_key(fk, table, "organizations", ["organization_id"], ["id"], ondelete="CASCADE")

    if _present(bind, "compliance_policies"):
        _drop_single_column_name_unique(bind)
        if not bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": NAME_UNIQUE}).scalar():
            op.create_unique_constraint(NAME_UNIQUE, "compliance_policies", ["organization_id", "name"])


def downgrade():
    bind = op.get_bind()
    if _present(bind, "compliance_policies"):
        if bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": NAME_UNIQUE}).scalar():
            op.drop_constraint(NAME_UNIQUE, "compliance_policies", type_="unique")
    for table in TABLES:
        if not _present(bind, table) or not _has_column(bind, table, "organization_id"):
            continue
        fk = f"fk_{table}_organization_id"
        if bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
            op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_index(f"ix_{table}_organization_id", table_name=table, if_exists=True)
        op.drop_column(table, "organization_id")
    # The platform-wide unique on name is not restored: two organisations may
    # now hold the same name, so re-adding it could fail on real data.
