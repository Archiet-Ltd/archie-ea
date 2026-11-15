"""Every table with row-level security enabled also forces it, so the owning
role is bound by the tenant policies (iso06)."""
import uuid

import pytest
from sqlalchemy import create_engine, text


def test_every_rls_table_forces_row_level_security(app, db_session):
    rows = db_session.execute(
        text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') "
            "AND c.relrowsecurity AND NOT c.relforcerowsecurity ORDER BY 1"
        )
    ).fetchall()
    assert rows == [], f"row-level security enabled but not forced: {[r[0] for r in rows]}"
    enabled = db_session.execute(
        text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
             "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND c.relrowsecurity")
    ).scalar()
    assert enabled > 0, "no table has row-level security: the check would pass on nothing"


def test_a_non_superuser_owner_with_no_organisation_reads_no_tenant_rows(app, db_session):
    """Connected as a NOSUPERUSER, NOBYPASSRLS role that OWNS a tenant table,
    with no organisation set, a read returns no rows."""
    url = app.config["SQLALCHEMY_DATABASE_URI"]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    role = f"iso06_owner_{uuid.uuid4().hex[:8]}"
    table = f"iso06_t_{uuid.uuid4().hex[:8]}"
    with admin.connect() as c:
        if not c.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar():
            pytest.skip("needs a role that may create roles")
        c.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'x' NOSUPERUSER NOBYPASSRLS"))
        c.execute(text(f"GRANT CREATE ON SCHEMA public TO {role}"))
        db = c.execute(text("SELECT current_database()")).scalar()
        c.execute(text(f"GRANT CONNECT ON DATABASE {db} TO {role}"))
    try:
        owner_url = admin.url.set(username=role, password="x")
        owner = create_engine(owner_url, isolation_level="AUTOCOMMIT")
        with owner.connect() as o:
            o.execute(text(f"CREATE TABLE public.{table} (id serial PRIMARY KEY, organization_id integer)"))
            o.execute(text(f"INSERT INTO public.{table} (organization_id) VALUES (1), (2)"))
            o.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
            o.execute(text(
                f"CREATE POLICY tenant_select ON public.{table} FOR SELECT USING "
                "(organization_id = NULLIF(current_setting('archie.organization_id', true), '')::integer "
                "OR COALESCE(current_setting('archie.platform_scope', true), '') = 'on')"))
            # enabled only: the owner still sees everything (the gap being closed)
            assert o.execute(text(f"SELECT count(*) FROM public.{table}")).scalar() == 2
            o.execute(text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
            assert o.execute(text(f"SELECT count(*) FROM public.{table}")).scalar() == 0
            o.execute(text("SELECT set_config('archie.organization_id', '1', false)"))
            assert o.execute(text(f"SELECT count(*) FROM public.{table}")).scalar() == 1
        owner.dispose()
    finally:
        with admin.connect() as c:
            c.execute(text(f"DROP TABLE IF EXISTS public.{table}"))
            c.execute(text(f"DROP OWNED BY {role}"))
            c.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


def test_a_real_tenant_table_is_forced_for_its_owner(app, db_session):
    forced = db_session.execute(
        text("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'public.answer_records'::regclass")
    ).scalar()
    assert forced is True
