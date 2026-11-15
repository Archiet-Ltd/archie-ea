"""The deploy role owns every user-defined type in public after the database access step.

A scratch database is built with its types owned by the admin role, the real role set is
configured on it, and the deploy role then changes a type the way a migration does.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

REPO = Path(__file__).resolve().parents[1]
EDUCATION_MIGRATION = REPO / "migrations" / "versions" / "20261114_plan_education.py"
DEPLOY_PW = "deploy-pw-typeowner"
RUNTIME_PW = "runtime-pw-typeowner"

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS citext;
CREATE TYPE public.subscriptionplan AS ENUM ('free', 'pro');
CREATE DOMAIN public.positive_amount AS integer CHECK (VALUE > 0);
CREATE TYPE public.price_range AS RANGE (subtype = integer);
CREATE TYPE public.money_pair AS (amount integer, currency text);
CREATE TABLE public.subscriptions (id serial PRIMARY KEY, plan public.subscriptionplan, plans public.subscriptionplan[]);
"""


def _url_for(base, database, user=None, password=None):
    parts = urlsplit(base)
    netloc = parts.netloc
    if user:
        netloc = f"{user}:{password}@{parts.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((parts.scheme.split("+")[0], netloc, f"/{database}", "", ""))


@pytest.fixture(scope="module")
def scratch():
    from scripts.database.configure_roles import configure_database_roles

    base = os.environ["TEST_DATABASE_URL"]
    name = f"archie_test_typeowner_{uuid.uuid4().hex[:8]}"
    admin_url = _url_for(base, "postgres")
    admin = psycopg2.connect(admin_url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        target = _url_for(base, name)
        with psycopg2.connect(target) as conn, conn.cursor() as cur:
            cur.execute(SCHEMA)
        configure_database_roles(
            admin_url=admin_url, database_names=[name],
            deploy_password=DEPLOY_PW, runtime_password=RUNTIME_PW,
        )
        yield {
            "name": name,
            "super": target,
            "deploy": _url_for(base, name, "archie_deploy", DEPLOY_PW),
        }
    finally:
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _owners(url):
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT t.typname, pg_get_userbyid(t.typowner) FROM pg_type t "
            "JOIN pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = 'public' "
            "AND t.typname IN ('subscriptionplan','positive_amount','price_range','money_pair')"
        )
        return dict(cur.fetchall())


def test_the_deploy_role_can_add_a_value_to_an_enum_type_it_was_handed(scratch):
    """Every user-defined type is owned by the deploy role, and it can add an enum value."""
    assert set(_owners(scratch["super"]).values()) == {"archie_deploy"}
    conn = psycopg2.connect(scratch["deploy"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TYPE subscriptionplan ADD VALUE IF NOT EXISTS 'education'")
            cur.execute("SELECT enum_range(NULL::subscriptionplan)::text")
            assert "education" in cur.fetchone()[0]
    finally:
        conn.close()


def test_the_education_plan_migration_runs_as_the_deploy_role(scratch):
    spec = importlib.util.spec_from_file_location("plan_education_under_test", EDUCATION_MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine(scratch["deploy"], poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"transactional_ddl": True})
            with Operations.context(context):
                module.upgrade()
            connection.commit()
        with engine.connect() as connection:
            labels = connection.execute(text("SELECT enum_range(NULL::subscriptionplan)::text")).scalar()
        assert "education" in labels
    finally:
        engine.dispose()


def test_verification_fails_on_a_public_type_owned_by_another_role_and_passes_after_transfer(scratch):
    from scripts.database import configure_roles as roles

    def verify(cursor):
        roles._verify_database_acl(
            cursor, database_name=scratch["name"], deploy_role="archie_deploy", runtime_role="archie_runtime",
        )

    with psycopg2.connect(scratch["super"]) as conn, conn.cursor() as cur:
        verify(cur)  # extension and array types left alone: no error
        cur.execute("ALTER TYPE public.money_pair OWNER TO postgres")
        with pytest.raises(RuntimeError, match="money_pair"):
            verify(cur)
        conn.rollback()
        cur.execute("ALTER TYPE public.money_pair OWNER TO postgres")
        roles._transfer_public_objects(cur, deploy_role="archie_deploy", runtime_role="archie_runtime")
        verify(cur)
        conn.rollback()


def test_extension_and_array_types_are_left_with_their_owner(scratch):
    from scripts.database import configure_roles as roles

    with psycopg2.connect(scratch["super"]) as conn, conn.cursor() as cur:
        listed = {name for name, _ in roles._public_user_types(cur)}
        assert listed == {"subscriptionplan", "positive_amount", "price_range", "money_pair"}
        cur.execute("SELECT pg_get_userbyid(typowner) FROM pg_type WHERE typname = 'citext'")
        assert cur.fetchone()[0] == "postgres"


def test_verification_reports_a_type_dropped_during_the_check_as_a_clear_failure(monkeypatch):
    from scripts.database import configure_roles as roles

    class Cursor:
        def execute(self, *_args, **_kwargs):
            pass

        def fetchone(self):
            return None

    monkeypatch.setattr(roles, "_public_user_types", lambda _cursor: (("vanished", "e"),))
    with pytest.raises(RuntimeError, match="vanished"):
        roles._verify_type_owners(Cursor(), database_name="x", deploy_role="archie_deploy")


def test_an_extension_owned_type_keeps_its_owner_after_the_transfer(scratch):
    from scripts.database import configure_roles as roles

    with psycopg2.connect(scratch["super"]) as conn, conn.cursor() as cur:
        roles._transfer_public_objects(cur, deploy_role="archie_deploy", runtime_role="archie_runtime")
        cur.execute("SELECT pg_get_userbyid(typowner) FROM pg_type WHERE typname = 'citext'")
        assert cur.fetchone()[0] == "postgres"
        conn.rollback()


def test_the_deploy_script_runs_database_acl_before_and_after_schema_deploy():
    lines = [
        line
        for line in (REPO / "scripts" / "deploy_verified.sh").read_text().splitlines()
        if line.startswith("docker compose run --rm --no-deps")
    ]
    order = [next(n for n in ("database-bootstrap", "schema-deploy", "database-acl") if n in line) for line in lines]
    assert order == ["database-bootstrap", "database-acl", "schema-deploy", "database-acl"]
