"""The production deploy under forced row-level security (iso06b).

A scratch database is cloned from the migrated test schema, the real role set is
built on it with ``scripts/database/configure_roles.py`` (``archie_deploy`` owns
every table, ``archie_runtime`` has the database-acl grants, both NOSUPERUSER
NOBYPASSRLS) and the deploy data steps run as ``archie_deploy``.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool, QueuePool

REPO = Path(__file__).resolve().parents[1]
DEPLOY_PW = "deploy-pw-iso06b"
RUNTIME_PW = "runtime-pw-iso06b"
FORCE_MIGRATION = REPO / "migrations" / "versions" / "20261104_force_rls.py"


def _url_for(base: str, database: str, user: str | None = None, password: str | None = None) -> str:
    parts = urlsplit(base)
    netloc = parts.netloc
    if user:
        host = parts.netloc.rsplit("@", 1)[-1]
        netloc = f"{user}:{password}@{host}"
    return urlunsplit((parts.scheme.split("+")[0], netloc, f"/{database}", "", ""))


@pytest.fixture(scope="module")
def scratch():
    from scripts.database.configure_roles import configure_database_roles

    base = os.environ["TEST_DATABASE_URL"]
    source = urlsplit(base).path.lstrip("/")
    name = f"archie_test_iso06b_{uuid.uuid4().hex[:8]}"
    admin_url = _url_for(base, "postgres")
    admin = psycopg2.connect(admin_url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        dump = subprocess.run(
            ["pg_dump", "--no-owner", "--no-privileges", "--schema-only", _url_for(base, source)],
            check=True, capture_output=True, text=True,
        ).stdout
        data = subprocess.run(
            ["pg_dump", "--no-owner", "--data-only", "-t", "alembic_version", "-t", "rls_force_previous",
             _url_for(base, source)],
            check=True, capture_output=True, text=True,
        ).stdout
        target = _url_for(base, name)
        for script in (dump, data):
            subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=0", target], input=script,
                           check=True, capture_output=True, text=True)
        configure_database_roles(
            admin_url=admin_url, database_names=[name],
            deploy_password=DEPLOY_PW, runtime_password=RUNTIME_PW,
        )
        yield {
            "name": name,
            "super": target,
            "deploy": _url_for(base, name, "archie_deploy", DEPLOY_PW),
            "runtime": _url_for(base, name, "archie_runtime", RUNTIME_PW),
        }
    finally:
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _flask(scratch, *args, unscoped=False):
    env = dict(os.environ, DATABASE_URL=scratch["deploy"], SECRET_KEY="x", FLASK_CONFIG="production")
    # Production configuration refuses to start without the capability secret; a clean
    # shell (CI) has none, so the subprocess gets its own throwaway 64-hex test value.
    env["TRANSFORMATION_COMMAND_CAPABILITY_SECRET"] = "74" * 32
    env.pop("TEST_DATABASE_URL", None)
    if unscoped:
        code = textwrap.dedent(
            """
            import sys
            from click.testing import CliRunner
            from manage import app
            command = app.cli.commands["merge-work-package-stores"]
            command.callback = command.callback.__wrapped__
            result = CliRunner().invoke(command, sys.argv[1:])
            print(result.output)
            sys.exit(result.exit_code)
            """
        )
        cmd = [sys.executable, "-c", code, *args]
    else:
        cmd = [sys.executable, "-m", "flask", "--app", "manage", *args]
    return subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=900)


def _seed_two_organisations(scratch):
    engine = create_engine(scratch["super"], poolclass=NullPool)
    with engine.begin() as c:
        c.execute(text("DELETE FROM unified_work_packages"))
        c.execute(text("DELETE FROM work_packages"))
        c.execute(text("DELETE FROM organizations WHERE id IN (9101, 9102)"))
        c.execute(text("INSERT INTO organizations (id, name, slug) VALUES (9101, 'iso06b A', 'iso06b-a'), (9102, 'iso06b B', 'iso06b-b')"))
        c.execute(text(
            "INSERT INTO work_packages (name, context, organization_id, created_at, updated_at) VALUES "
            "('iso06b wp A', 'x', 9101, now(), now()), ('iso06b wp B', 'x', 9102, now(), now())"
        ))
    engine.dispose()


def _scalar(url, sql, **params):
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as c:
            return c.execute(text(sql), params).scalar()
    finally:
        engine.dispose()


def test_deploy_backfills_merge_every_organisation_under_forced_rls(scratch):
    """Criterion 1: as archie_deploy, on forced tables, an uncopied work package in
    each of two organisations is merged and --verify passes; without the platform
    scope --verify refuses instead of passing on an empty read."""
    _seed_two_organisations(scratch)
    assert _scalar(scratch["super"], "SELECT count(*) FROM work_packages WHERE retired_into_id IS NULL") == 2
    # the deploy role itself sees nothing without a scope: the premise of the defect
    assert _scalar(scratch["deploy"], "SELECT count(*) FROM work_packages") == 0

    for args in (["backfill-work-package-org"], ["merge-work-package-stores"],
                 ["backfill-work-package-org"], ["merge-work-package-stores", "--verify"]):
        done = _flask(scratch, *args)
        assert done.returncode == 0, (args, done.stdout[-2000:], done.stderr[-2000:])

    merged = _scalar(
        scratch["super"],
        "SELECT count(*) FROM unified_work_packages WHERE source_table = 'work_packages' "
        "AND organization_id IN (9101, 9102)",
    )
    assert merged == 2
    assert _scalar(scratch["super"], "SELECT count(*) FROM work_packages WHERE retired_into_id IS NULL") == 0

    # an uncopied row present again, no scope: --verify must refuse, not pass
    engine = create_engine(scratch["super"], poolclass=NullPool)
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO work_packages (name, context, organization_id, created_at, updated_at) "
            "VALUES ('iso06b late', 'x', 9101, now(), now())"
        ))
    engine.dispose()
    refused = _flask(scratch, "--verify", unscoped=True)
    assert refused.returncode != 0, refused.stdout
    assert "platform scope" in (refused.stdout + refused.stderr)
    scoped = _flask(scratch, "merge-work-package-stores", "--verify")
    assert scoped.returncode != 0, "verify must fail while an uncopied row exists"
    assert "1 unmerged" in scoped.stdout


def test_every_deploy_data_command_in_the_script_runs_in_a_platform_scope(app):
    """Each flask command deploy-schema.sh runs after the structural steps carries
    the scope; none is left to the shell script."""
    import re

    from app.commands.deploy_scope import DEPLOY_DATA_COMMANDS, SCOPED_MARK

    script = (REPO / "scripts" / "database" / "deploy-schema.sh").read_text()
    named = set(re.findall(r"flask --app manage ([a-z][a-z-]+)", script))
    structural = {"init-db", "schema-upgrade", "reconcile-schema", "schema-status"}
    assert named - structural == set(DEPLOY_DATA_COMMANDS)
    # manage.app is the app the deploy script runs (``flask --app manage``): it
    # has had register_all_cli_commands applied at import. Never call setup
    # methods on the shared session app, which may already have served a request.
    import manage

    for name in sorted(DEPLOY_DATA_COMMANDS):
        command = manage.app.cli.commands.get(name)
        assert command is not None, name
        assert getattr(command.callback, SCOPED_MARK, False), name


def test_runtime_role_cannot_touch_the_force_ledger(scratch):
    """Criterion 2a: archie_runtime has no access to rls_force_previous."""
    assert _scalar(scratch["super"], "SELECT count(*) FROM rls_force_previous") > 0
    engine = create_engine(scratch["runtime"], poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        for statement in (
            "SELECT * FROM rls_force_previous",
            "INSERT INTO rls_force_previous (table_name) VALUES ('x')",
            "UPDATE rls_force_previous SET table_name = 'y'",
            "DELETE FROM rls_force_previous",
        ):
            with engine.connect() as c, pytest.raises(DBAPIError) as failure:
                c.execute(text(statement))
            assert "permission denied" in str(failure.value), statement
    finally:
        engine.dispose()


def _operations(connection):
    spec = importlib.util.spec_from_file_location("force_rls_under_test", FORCE_MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = MigrationContext.configure(connection)
    return module, Operations(context)


def test_a_table_name_containing_a_quote_round_trips_upgrade_and_downgrade(scratch):
    """Criterion 2b: identifiers are quoted, so a quote in a name neither breaks
    nor escapes the statement in either direction."""
    name = 'iso06b"quoted'
    engine = create_engine(scratch["super"], poolclass=NullPool)
    try:
        with engine.connect() as connection:
            outer = connection.begin()
            try:
                connection.execute(text('CREATE TABLE public."iso06b""quoted" (id int, organization_id int)'))
                connection.execute(text('ALTER TABLE public."iso06b""quoted" ENABLE ROW LEVEL SECURITY'))
                module, ops = _operations(connection)
                from alembic.operations import Operations as Ops

                with Ops.context(MigrationContext.configure(connection)):
                    module.upgrade()
                    forced = connection.execute(
                        text("SELECT relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:q)"),
                        {"q": 'public."iso06b""quoted"'},
                    ).scalar()
                    assert forced is True
                    assert connection.execute(
                        text("SELECT count(*) FROM rls_force_previous WHERE table_name = :n"), {"n": name}
                    ).scalar() == 1
                    module.downgrade()
                    unforced = connection.execute(
                        text("SELECT relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:q)"),
                        {"q": 'public."iso06b""quoted"'},
                    ).scalar()
                    assert unforced is False
            finally:
                outer.rollback()
    finally:
        engine.dispose()


def test_a_non_superuser_owner_reads_no_rows_of_a_real_migrated_tenant_table(scratch):
    """Defect 4: archie_deploy owns the migrated tenant tables, is NOSUPERUSER
    NOBYPASSRLS, and with no organisation set reads zero rows of them."""
    _seed_two_organisations(scratch)
    owner = _scalar(
        scratch["deploy"],
        "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'public.work_packages'::regclass",
    )
    assert owner == "archie_deploy"
    flags = _scalar(
        scratch["deploy"],
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user",
    )
    assert flags is False
    assert _scalar(scratch["super"], "SELECT count(*) FROM work_packages") >= 2
    assert _scalar(scratch["deploy"], "SELECT count(*) FROM work_packages") == 0


def test_the_migration_scope_does_not_reach_the_apps_pooled_connections(scratch):
    """Criterion 3: after an in-process schema-upgrade, connections in the app's
    pool carry no platform scope, and an unscoped read there returns nothing while
    the signed-in organisation's read returns only its own rows."""
    import flask
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import MetaData

    _seed_two_organisations(scratch)
    pool = create_engine(
        scratch["runtime"], poolclass=QueuePool, pool_size=4, max_overflow=0,
        pool_reset_on_return="commit",
    )
    try:
        warm = [pool.connect() for _ in range(4)]
        for c in warm:
            c.close()

        mini = flask.Flask("iso06b")
        mini.extensions["migrate"] = type("M", (), {"db": type("D", (), {"engine": pool, "metadata": MetaData()})()})()
        config = Config()
        config.set_main_option("script_location", str(REPO / "migrations"))
        with mini.app_context():
            command.upgrade(config, "head")

        held = [pool.connect() for _ in range(4)]
        try:
            for c in held:
                assert c.execute(text("SELECT coalesce(current_setting('archie.platform_scope', true), '')")).scalar() != "on"
            unscoped = held[0].execute(text("SELECT count(*) FROM work_packages")).scalar()
            held[0].rollback()
            assert unscoped == 0
            held[1].execute(text("SELECT set_config('archie.organization_id', '9101', true)"))
            rows = held[1].execute(text("SELECT organization_id FROM work_packages")).all()
            held[1].rollback()
            assert rows and {r[0] for r in rows} == {9101}
        finally:
            for c in held:
                c.close()
    finally:
        pool.dispose()


# -- iso06c ---------------------------------------------------------------

def test_each_apps_scoped_deploy_callbacks_run_in_that_apps_context(app, monkeypatch):
    """The deploy commands are module-level objects shared by every app in a
    process; the scope binds to the app running the command, not the first app
    that wrapped it."""
    import click
    from flask import current_app, g

    import app.commands.deploy_scope as deploy_scope
    from app import create_app

    other = create_app("testing")
    assert other is not app
    seen = []

    @click.command("iso06c-probe")
    def probe():
        seen.append((current_app._get_current_object(), getattr(g, "_platform_scope", None)))

    # one module-level command object, registered on both apps, as the real ones are
    for each in (app, other):
        each.cli.add_command(probe)
    monkeypatch.setattr(deploy_scope, "DEPLOY_DATA_COMMANDS", frozenset({"iso06c-probe"}))
    try:
        deploy_scope.scope_deploy_commands(app)
        deploy_scope.scope_deploy_commands(other)
        assert app.test_cli_runner().invoke(probe).exit_code == 0
        assert other.test_cli_runner().invoke(probe).exit_code == 0
    finally:
        app.cli.commands.pop("iso06c-probe", None)
        other.cli.commands.pop("iso06c-probe", None)
    assert [a for a, _ in seen] == [app, other]
    assert all(scope == "deploy backfill: iso06c-probe" for _, scope in seen)


def test_a_listed_but_unregistered_deploy_command_raises_at_registration():
    from flask import Flask

    from app.commands.deploy_scope import scope_deploy_commands

    with pytest.raises(RuntimeError, match="not registered"):
        scope_deploy_commands(Flask("iso06c-empty"))


@pytest.mark.parametrize(
    "name",
    ["backfill-solution-risk-merge", "backfill-audit-trail", "backfill-review-queue-approvals"],
)
def test_backfills_refuse_without_the_platform_scope(app, name):
    command = app.cli.commands[name]
    scoped = command.callback
    command.callback = scoped.__wrapped__  # the command as it runs unwrapped
    try:
        refused = app.test_cli_runner().invoke(command, ["--dry-run"])
    finally:
        command.callback = scoped
    assert refused.exit_code == 1, refused.output
    assert "platform scope" in refused.output
    allowed = app.test_cli_runner().invoke(command, ["--dry-run"])
    assert allowed.exit_code == 0, allowed.output


def test_migration_engine_receives_the_apps_connect_args(app, monkeypatch):
    import sqlalchemy

    probe = {"application_name": "iso06c_probe"}
    options = dict(app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {})
    options["connect_args"] = {**(options.get("connect_args") or {}), **probe}
    monkeypatch.setitem(app.config, "SQLALCHEMY_ENGINE_OPTIONS", options)
    captured = []
    real = sqlalchemy.create_engine

    def spy(*args, **kwargs):
        captured.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(sqlalchemy, "create_engine", spy)
    result = app.test_cli_runner().invoke(args=["db", "current"])
    assert result.exit_code == 0, result.output
    assert captured and captured[-1].get("connect_args", {}).get("application_name") == "iso06c_probe"
