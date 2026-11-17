"""Force row-level security on every table that has it enabled.

ENABLE ROW LEVEL SECURITY binds every role except the table owner (and
superusers / BYPASSRLS roles). FORCE ROW LEVEL SECURITY binds the owner too,
so the schema-owning role is held to the same tenant policies as the runtime
role: defence in depth for the case where the owner's credentials are used by
mistake. No policy and no audit trigger is touched.

Which tables: found from pg_class at run time (relrowsecurity true,
relforcerowsecurity false, ordinary or partitioned tables in public), so a
table fenced by any earlier revision is covered without listing it here.

Downgrade: the tables this revision changed are recorded in
``rls_force_previous`` and only those are put back to NO FORCE, so a table that
was already forced before this revision stays forced.

The migration runner (schema-upgrade, role archie_deploy) is NOBYPASSRLS and
owns the tables, so once they are forced it is bound by the policies. The
runner sets archie.platform_scope for its session (migrations/env.py), which is
the platform scope the policies already admit.

Revision ID: 20261104_force_rls
Revises: 20261103_requirement_source
Create Date: 2026-11-04
"""
from alembic import op
from sqlalchemy import text

revision = "20261104_force_rls"
down_revision = "20261103_requirement_source"
branch_labels = None
depends_on = None

_LEDGER = "rls_force_previous"

_FIND = """
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p')
  AND c.relrowsecurity
  AND NOT c.relforcerowsecurity
ORDER BY c.relname
"""


def _q(bind, name):
    """Quote an identifier, doubling any embedded quote."""
    return bind.dialect.identifier_preparer.quote_identifier(name)


def upgrade():
    bind = op.get_bind()
    bind.execute(text(f"CREATE TABLE IF NOT EXISTS public.{_LEDGER} (table_name text PRIMARY KEY)"))
    tables = [r[0] for r in bind.execute(text(_FIND))]
    for table in tables:
        bind.execute(text(f"INSERT INTO public.{_LEDGER} (table_name) VALUES (:t) ON CONFLICT DO NOTHING"), {"t": table})
        bind.execute(text(f"ALTER TABLE public.{_q(bind, table)} FORCE ROW LEVEL SECURITY"))  # nosec B608 - name read from pg_class, quoted by the dialect
    print(f"row-level security forced on {len(tables)} tables")


def downgrade():
    bind = op.get_bind()
    present = bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{_LEDGER}"}).scalar()
    if not present:
        return
    for (table,) in list(bind.execute(text(f"SELECT table_name FROM public.{_LEDGER}"))):
        if bind.execute(
            text("SELECT to_regclass(format('public.%I', CAST(:t AS text))) IS NOT NULL"), {"t": table}
        ).scalar():
            bind.execute(text(f"ALTER TABLE public.{_q(bind, table)} NO FORCE ROW LEVEL SECURITY"))  # nosec B608 - name from the ledger, quoted by the dialect
    bind.execute(text(f"DROP TABLE public.{_LEDGER}"))
