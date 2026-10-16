"""The four ADM board-portfolio tables exist, match the model, and are fenced.

``adm_board_portfolios``, ``adm_board_portfolio_links``, ``adm_rida_logs`` and
``adm_cross_board_dependencies`` were declared by ``app/models/adm_portfolio.py``
but never imported, so ``create_all()`` skipped them and a user delete (or a
solution delete that cleans ``adm_rida_logs``) hit "relation does not exist" in
any test shard that had not happened to import the module.

Covered:
- the models are in ``db.metadata`` after a plain ``create_app()``;
- ``init-db`` + ``schema-upgrade`` on an empty database land at the head with all
  four tables, and the three tenant tables fenced;
- the tables revision builds exactly the shape ``create_all()`` builds, and the
  fencing revision restores the policies on a database where the tables were
  added after the earlier fencing revision had already run;
- the admin bulk user delete and the solution-delete RIDA cleanup work.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[1]
TABLES_REVISION = "20261015_adm_portfolio_tables"
RLS_REVISION = "20261015_adm_portfolio_rls"
PARENT_REVISION = "20261013_answer_records_rls"
ADM_TABLES = (
    "adm_board_portfolios",
    "adm_board_portfolio_links",
    "adm_rida_logs",
    "adm_cross_board_dependencies",
)
FENCED_TABLES = ("adm_board_portfolios", "adm_cross_board_dependencies", "adm_rida_logs")
POLICIES = {"tenant_select", "tenant_insert", "tenant_update", "tenant_delete"}


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TABLES_MIGRATION = _load(REPO / "migrations" / "versions" / f"{TABLES_REVISION}.py", "adm_tables_revision")
RLS_MIGRATION = _load(REPO / "migrations" / "versions" / f"{RLS_REVISION}.py", "adm_rls_revision")
# Reuse the deploy-sequence helpers of the schema migration tests rather than
# writing a second copy of the subprocess driver and the snapshot queries.
SCHEMA = _load(REPO / "tests" / "test_schema_migrations.py", "schema_migration_helpers")


# ------------------------------------------------------------------ metadata


def test_models_are_registered_by_create_app_alone():
    """A fresh process that only builds the app sees all four tables."""
    code = (
        "import json\n"
        "from app import create_app, db\n"
        "app = create_app('testing')\n"
        "with app.app_context():\n"
        f"    print('@@' + json.dumps([t in db.metadata.tables for t in {list(ADM_TABLES)!r}]))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert "@@" in proc.stdout, proc.stdout[-2000:] + proc.stderr[-2000:]
    present = json.loads(proc.stdout.rsplit("@@", 1)[1].splitlines()[0])
    assert present == [True] * len(ADM_TABLES)


# ------------------------------------------------------------ schema, scratch


def _policies(engine, table):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = :t"),
            {"t": table},
        ).all()
        enabled = conn.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:t)"),
            {"t": f"public.{table}"},
        ).scalar()
    return {r[0] for r in rows}, bool(enabled)


def _present(engine):
    with engine.connect() as conn:
        return [
            conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{t}"}).scalar()
            for t in ADM_TABLES
        ]


def _run(engine, module, direction="upgrade"):
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(module, direction)()


def _shape(url):
    """Columns (with position), indexes and constraints of the four tables only."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            names = {"names": list(ADM_TABLES)}
            columns = conn.execute(text(
                "SELECT table_name, ordinal_position, column_name, data_type, "
                "character_maximum_length, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = ANY(:names) ORDER BY 1, 2"), names).all()
            indexes = conn.execute(text(
                "SELECT tablename, indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = ANY(:names) ORDER BY 1, 2"), names).all()
            constraints = conn.execute(text(
                "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c WHERE c.conrelid = ANY(ARRAY(SELECT to_regclass('public.' || n) "
                "FROM unnest(CAST(:names AS text[])) AS n)) ORDER BY 1, 2"), names).all()
    finally:
        engine.dispose()
    return {"columns": columns, "indexes": indexes, "constraints": constraints}


@pytest.fixture(scope="module")
def deployed(scratch_databases_adm):
    """One empty database taken through the deploy order: init-db, schema-upgrade."""
    url = scratch_databases_adm("deployed")
    _, output = SCHEMA._flask(url, ["init-db"], ["schema-upgrade"], ["reconcile-schema", "--dry-run"])
    return url, output


@pytest.fixture(scope="module")
def scratch_databases_adm():
    admin = SCHEMA._admin_engine()
    created = []

    def make(label):
        name = f"archie_adm_{label}_{uuid.uuid4().hex[:8]}"
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return SCHEMA._server_url().set(database=name).render_as_string(hide_password=False)

    yield make

    with admin.connect() as conn:
        for name in created:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _script_directory():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    directory = REPO / "migrations"
    config = Config(str(directory / "alembic.ini"))
    config.set_main_option("script_location", str(directory))
    return ScriptDirectory.from_config(config)


def _repo_head():
    heads = _script_directory().get_heads()
    assert len(heads) == 1, heads
    return heads[0]


def _ancestors(revision):
    return {rev.revision for rev in _script_directory().iterate_revisions(revision, "base")}


def test_init_db_then_schema_upgrade_builds_and_fences_the_tables(deployed):
    url, output = deployed
    engine = create_engine(url)
    try:
        assert _present(engine) == [True] * len(ADM_TABLES)
        # The recorded revision is the repository's single head (a later revision
        # may sit above the fence), and the fence is part of its history.
        assert SCHEMA._recorded(url) == [_repo_head()], output
        assert RLS_REVISION in _ancestors(_repo_head())
        for table in FENCED_TABLES:
            names, enabled = _policies(engine, table)
            assert enabled and names == POLICIES, table
        # The links table has no organisation column and stays unfenced.
        assert _policies(engine, "adm_board_portfolio_links") == (set(), False)
    finally:
        engine.dispose()
    # reconcile-schema reports other, unrelated drift on a fresh database (enum
    # values, constraints, triggers added by later steps); none of it may name
    # these four tables, and none of the four may be reported absent.
    reconcile = output[output.index("reconcile-schema:"):]
    assert "table(s) absent" not in reconcile, reconcile
    for table in ADM_TABLES:
        assert table not in reconcile, reconcile


def test_revision_builds_the_shape_create_all_builds_and_refences(deployed):
    """Production-shaped path and schema parity.

    A deployed database passed the earlier fencing revision while the four tables
    were absent. Drop the tables and fences from the init-db database, run the two
    new revisions, and require the same columns, indexes and constraints as the
    ``create_all()`` build plus the policies.
    """
    url, _ = deployed
    from_create_all = _shape(url)
    assert from_create_all["columns"], "init-db built none of the four tables"

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for table in reversed(ADM_TABLES):
                conn.execute(text(f"DROP TABLE IF EXISTS public.{table} CASCADE"))  # nosec B608 - literal names
        assert _present(engine) == [False] * len(ADM_TABLES)

        _run(engine, TABLES_MIGRATION)
        _run(engine, RLS_MIGRATION)
        assert _present(engine) == [True] * len(ADM_TABLES)
        for table in FENCED_TABLES:
            names, enabled = _policies(engine, table)
            assert enabled and names == POLICIES, table
        assert _policies(engine, "adm_board_portfolio_links") == (set(), False)

        from_revision = _shape(url)
        for key in ("columns", "indexes", "constraints"):
            assert [tuple(r) for r in from_revision[key]] == [tuple(r) for r in from_create_all[key]], key

        # Idempotent: a second run changes nothing.
        _run(engine, TABLES_MIGRATION)
        _run(engine, RLS_MIGRATION)
        assert _shape(url)["columns"] == from_revision["columns"]

        # Down steps: unfence, then remove the tables.
        _run(engine, RLS_MIGRATION, "downgrade")
        for table in FENCED_TABLES:
            assert _policies(engine, table) == (set(), False), table
        _run(engine, TABLES_MIGRATION, "downgrade")
        assert _present(engine) == [False] * len(ADM_TABLES)
    finally:
        engine.dispose()


def test_revision_chain_is_linear_and_contains_the_fencing_revision():
    script = _script_directory()
    assert len(script.get_heads()) == 1
    assert RLS_REVISION in _ancestors(_repo_head())
    assert script.get_revision(RLS_REVISION).down_revision == TABLES_REVISION
    assert script.get_revision(TABLES_REVISION).down_revision == PARENT_REVISION


# ------------------------------------------------------------ live code paths


def _make_org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"AdmPf {label} {suffix}", slug=f"admpf-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _make_user(db_session, org, *, is_org_admin=False):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"admpf-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        is_org_admin=is_org_admin,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _rida(db_session, org, **fields):
    from app.models.adm_portfolio import ADMRIDALog

    row = ADMRIDALog(
        organization_id=org.id,
        rida_code=f"RIDA-T-{uuid.uuid4().hex[:10]}",
        rida_type="risk",
        title="Seeded risk",
        **fields,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_owned_rows(db_session, org, user):
    from app.models.adm_portfolio import ADMBoardPortfolio

    portfolio = ADMBoardPortfolio(
        organization_id=org.id,
        portfolio_code=f"PF-T-{uuid.uuid4().hex[:10]}",
        name="Seeded portfolio",
        portfolio_owner_id=user.id,
        created_by_id=user.id,
    )
    db_session.add(portfolio)
    db_session.flush()
    rida = _rida(db_session, org, owner_id=user.id, created_by_id=user.id)
    db_session.commit()
    return portfolio.id, rida.id


def test_deleting_a_user_keeps_the_portfolio_and_rida_rows(app, db_session):
    """The ORM delete the admin user service performs loads the four mapped
    back-references (owned_portfolios, owned_rida_items, ...); each needs its table."""
    from app.models.user import User
    from app.modules.admin.services.admin_user_service import AdminUserService

    org = _make_org(db_session, "orm")
    user = _make_user(db_session, org)
    portfolio_id, rida_id = _seed_owned_rows(db_session, org, user)
    user_id = user.id

    success, message = AdminUserService.delete_user(user)
    assert success, message
    db_session.expire_all()
    assert User.query.get(user_id) is None
    portfolio = db_session.execute(
        text("SELECT portfolio_owner_id, created_by_id FROM adm_board_portfolios WHERE id = :i"),
        {"i": portfolio_id},
    ).one()
    assert tuple(portfolio) == (None, None)
    rida = db_session.execute(
        text("SELECT owner_id, created_by_id FROM adm_rida_logs WHERE id = :i"), {"i": rida_id}
    ).one()
    assert tuple(rida) == (None, None)


def test_bulk_delete_endpoint_removes_a_member_without_portfolio_rows(app, db_session, login_as, client):
    """DELETE /admin/api/users/bulk is served by the v2 admin blueprint."""
    from app.models.user import User

    org = _make_org(db_session, "bulk")
    actor = _make_user(db_session, org, is_org_admin=True)
    target = _make_user(db_session, org)
    db_session.commit()
    target_id = target.id

    login_as(client, actor)
    response = client.delete("/admin/api/users/bulk", json={"ids": [target_id]})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json() == {"deleted": 1}
    db_session.expire_all()
    assert User.query.get(target_id) is None


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Outside this change: the live v2 bulk-delete route issues a plain "
        "User delete without the SET NULL pass the older admin route carries, so a "
        "member who owns an adm_board_portfolios or adm_rida_logs row hits "
        "adm_rida_logs_owner_id_fkey (a foreign key violation, not a missing relation)."
    ),
)
def test_bulk_delete_endpoint_with_owned_portfolio_rows(app, db_session, login_as, client):
    org = _make_org(db_session, "bulkfk")
    actor = _make_user(db_session, org, is_org_admin=True)
    target = _make_user(db_session, org)
    _seed_owned_rows(db_session, org, target)
    target_id = target.id

    login_as(client, actor)
    response = client.delete("/admin/api/users/bulk", json={"ids": [target_id]})
    assert response.status_code == 200
    assert response.get_json() == {"deleted": 1}


def test_solution_delete_cleanup_removes_the_review_item_rida_rows(app, db_session):
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.routes.solution_design_routes import _cascade_delete_solution

    org = _make_org(db_session, "soldel")
    submitter = _make_user(db_session, org)
    solution = Solution(name=f"AdmPf solution {uuid.uuid4().hex[:8]}", organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    item = ARBReviewItem(
        organization_id=org.id,
        review_number=f"REV-T-{uuid.uuid4().hex[:10].upper()}",
        title="Seeded review",
        review_type="solution_design",
        solution_id=solution.id,
        submitter_id=submitter.id,
    )
    db_session.add(item)
    db_session.flush()
    linked = _rida(db_session, org, arb_review_item_id=item.id)
    unrelated = _rida(db_session, org)
    db_session.commit()
    linked_id, unrelated_id, sid = linked.id, unrelated.id, solution.id

    _cascade_delete_solution(sid)
    db_session.flush()

    remaining = {
        r[0] for r in db_session.execute(
            text("SELECT id FROM adm_rida_logs WHERE id IN (:a, :b)"), {"a": linked_id, "b": unrelated_id}
        )
    }
    assert remaining == {unrelated_id}
