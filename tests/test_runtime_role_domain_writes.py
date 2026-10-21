"""Web writes must succeed as the real, restricted application database role.

Every other test connects as the database owner or a superuser, which skips
both table privileges and row-level security. On 18 Oct 2026 that let a
privilege gap ship unseen: every ArchiMate element write queues a row in
``transformation_outbox_events`` through a plain ORM INSERT, the runtime role
held SELECT only on that table, and so adding an application (and every other
write that creates or changes an element) answered HTTP 500 for every user on
production while the whole suite was green.

This module builds a scratch database the way a deploy does: roles made by
``configure_database_roles``, the schema deployed as the schema-owner role
(``init-db``, ``schema-upgrade``, ``reconcile-schema``), the grants applied
again, then drives the app as the runtime role in a separate process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from psycopg2 import sql as pg_sql

from scripts.database.configure_roles import configure_database_roles

ROOT = Path(__file__).resolve().parents[1]
COMMAND_CAPABILITY_SECRET = "63" * 32


def _maintenance_url() -> str:
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    return urlunsplit(parsed._replace(path="/postgres"))


def _role_url(database: str, role: str, password: str) -> str:
    parsed = urlsplit(_maintenance_url())
    netloc = f"{role}:{password}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit(parsed._replace(netloc=netloc, path=f"/{database}"))


def _run(args: list[str], database_url: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "TEST_DATABASE_URL": database_url,
        "FLASK_CONFIG": "testing",
        "SECRET_KEY": "runtime-role-test",
        "TRANSFORMATION_COMMAND_CAPABILITY_SECRET": COMMAND_CAPABILITY_SECRET,
        "PYTHON_DOTENV_DISABLED": "1",
    }
    return subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=900
    )


@pytest.fixture(scope="module")
def deployed_roles_database():
    suffix = uuid.uuid4().hex[:10]
    name = f"rt_writes_{suffix}"
    deploy_role, runtime_role = f"rt_deploy_{suffix}", f"rt_runtime_{suffix}"
    deploy_password = f"deploy-{uuid.uuid4().hex}"
    runtime_password = f"runtime-{uuid.uuid4().hex}"

    admin = psycopg2.connect(_maintenance_url())
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(pg_sql.SQL("CREATE DATABASE {}").format(pg_sql.Identifier(name)))

    def configure() -> None:
        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(name,),
            deploy_password=deploy_password,
            runtime_password=runtime_password,
            deploy_role=deploy_role,
            runtime_role=runtime_role,
        )

    try:
        configure()
        deploy_url = _role_url(name, deploy_role, deploy_password)
        for command in ("init-db", "schema-upgrade", "reconcile-schema"):
            done = _run([sys.executable, "-m", "flask", "--app", "manage", command], deploy_url)
            assert done.returncode == 0, f"{command} failed:\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
        seed = _run(
            [
                sys.executable,
                "-c",
                "from app import create_app\n"
                "from app.models import Role\n"
                "app = create_app('testing')\n"
                "ctx = app.app_context(); ctx.push()\n"
                "Role.insert_roles()\n",
            ],
            deploy_url,
        )
        assert seed.returncode == 0, seed.stderr[-2000:]
        configure()  # grants for the tables the deploy just created
        yield {
            "name": name,
            "runtime_role": runtime_role,
            "runtime_url": _role_url(name, runtime_role, runtime_password),
        }
    finally:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s",
                (name,),
            )
            cursor.execute(
                pg_sql.SQL("DROP DATABASE IF EXISTS {}").format(pg_sql.Identifier(name))
            )
            for role in (runtime_role, deploy_role):
                cursor.execute(
                    pg_sql.SQL("DROP ROLE IF EXISTS {}").format(pg_sql.Identifier(role))
                )
        admin.close()


def test_runtime_role_can_queue_entity_events_but_not_rewrite_them(deployed_roles_database):
    """INSERT is granted; nothing that would let it alter or erase the log is."""
    db = deployed_roles_database
    conn = psycopg2.connect(_maintenance_url(), dbname=db["name"])
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT "
                + ", ".join(
                    f"has_table_privilege(%s, 'transformation_outbox_events', '{p}')"
                    for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
                ),
                (db["runtime_role"],) * 5,
            )
            assert cursor.fetchone() == (True, True, False, False, False)
    finally:
        conn.close()


def test_new_user_first_writes_succeed_as_the_runtime_role(deployed_roles_database):
    done = _run(
        [sys.executable, "-m", "tests._runtime_role_driver"],
        deployed_roles_database["runtime_url"],
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"driver produced no result:\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
    results = json.loads(lines[-1][len("RESULT "):])

    for name in ("application_import_manual", "application_create", "risk_create"):
        status, detail = results[name]
        assert status in (200, 201), f"{name} answered {status}: {detail}"
    assert json.loads(results["application_import_manual"][1])["created"] == 1
    # Both applications and the risk each produced an element in the
    # organisation, readable back through the runtime role with RLS on.
    assert int(results["elements_visible"][1]) >= 3
