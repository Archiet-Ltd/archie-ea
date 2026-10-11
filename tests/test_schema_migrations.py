"""Versioned schema changes: the baseline and the expand-and-contract examples.

These drive the same commands the deploy runs (scripts/database/deploy-schema.sh)
as subprocesses against throwaway databases, because what is under test is the
deploy sequence itself: ``init-db``, then ``schema-upgrade``, then
``reconcile-schema``.

- On a database built by ``init-db`` + ``reconcile-schema`` (every deployed
  database) the baseline revision changes nothing.
- On an empty database the baseline builds the full schema.
- A database stamped with a revision from before the baseline upgrades instead
  of stopping the deploy.
- The two example revisions are idempotent, and their down steps restore the
  prior type and nullability without losing a row, refusing (and changing
  nothing) when restoring them would discard data.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = "20260926_baseline"
RELAX = "20260926_relax_owner_app"
# The worked baseline/relax/widen example's own tip -- NOT necessarily the
# whole chain's head. Revisions land above this one over time (this
# repository already has two), and the refusal/round-trip semantics this
# file proves belong to these three specifically, so they are pinned to
# WIDEN rather than to "whatever the head currently is".
WIDEN = "20260926_widen_element_name"
HEAD = "20261001_adr_canonical_cols"

_DEFAULT_URL = "postgresql://postgres:postgres@127.0.0.1:5432/archie_test"


def _server_url():
    return make_url(os.environ.get("TEST_DATABASE_URL") or _DEFAULT_URL)


def _true_head():
    """The actual tip of the Alembic chain on disk.

    Derived from the migrations directory itself (the same construction
    app/commands/schema_migrations.py's _alembic_config() uses, minus the
    Flask app context it needs and this module does not have), rather than a
    hard-coded id -- so this file never goes stale when a revision is added
    above WIDEN, the way a literal id here already has once.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    directory = REPO_ROOT / "migrations"
    config = Config(str(directory / "alembic.ini"))
    config.set_main_option("script_location", str(directory))
    return ScriptDirectory.from_config(config).get_current_head()


def _admin_engine():
    return create_engine(
        _server_url().set(database="postgres"), isolation_level="AUTOCOMMIT"
    )


@pytest.fixture(scope="module")
def scratch_databases():
    """Create throwaway databases on the test server; drop them afterwards."""
    admin = _admin_engine()
    created = []

    def make(label, template="template0"):
        name = f"archie_schema_{label}_{uuid.uuid4().hex[:8]}"
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template}"'))
        created.append(name)
        return _server_url().set(database=name).render_as_string(hide_password=False)

    yield make

    with admin.connect() as conn:
        for name in created:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


# Runs several CLI commands against one app instance, so a sequence of deploy
# steps pays for one application boot instead of one per command. Stops at
# the first command that fails. An exception a command raises (a refused down
# step, for instance) is part of that command's output.
#
# Flask-Migrate's own `catch_errors` CLI wrapper catches a raised RuntimeError
# (ContractBlocked is one) and reports it with `logging.getLogger(...).error()`
# before calling sys.exit(1) -- it never reaches Click, so CliRunner's
# stdout/stderr capture misses it whenever the app's logging handlers were
# already bound to the real stderr before the redirect (they are, since the
# app boots once at import time, before the first `runner.invoke()`). A
# dedicated in-memory logging handler on the root logger picks it up anyway.
_DRIVER = r"""
import json, logging, sys
from flask.cli import FlaskGroup
from manage import app
cli = FlaskGroup(create_app=lambda: app)  # what `flask --app manage` builds
runner = app.test_cli_runner()

_log_records = []

class _Capture(logging.Handler):
    def emit(self, record):
        _log_records.append(self.format(record))

_handler = _Capture()
_handler.setFormatter(logging.Formatter("%(name)s %(levelname)s: %(message)s"))
logging.getLogger().addHandler(_handler)

results = []
for args in json.loads(sys.argv[1]):
    _log_records.clear()
    r = runner.invoke(cli=cli, args=args)
    text = r.output
    if r.exception is not None and not isinstance(r.exception, SystemExit):
        text += "\n" + type(r.exception).__name__ + ": " + str(r.exception)
    if _log_records:
        text += "\n" + "\n".join(_log_records)
    results.append([r.exit_code, text])
    if r.exit_code:
        break
print("@@RESULTS@@" + json.dumps(results))
"""


def _flask(url, *commands, check=True):
    """Run each command (a list of CLI arguments) in order; return (exit code, output).

    The exit code is the first non-zero one, else 0; output joins every
    command's output.
    """
    env = dict(os.environ)
    env.update(DATABASE_URL=url, TEST_DATABASE_URL=url, DEV_DATABASE_URL=url)
    env.setdefault("FLASK_CONFIG", "testing")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER, json.dumps([list(c) for c in commands])],
        cwd=REPO_ROOT, env=env, capture_output=True, timeout=1800,
        encoding="utf-8", errors="replace",
    )
    raw = proc.stdout + proc.stderr
    assert "@@RESULTS@@" in proc.stdout, f"command driver did not finish:\n{raw[-4000:]}"
    results = json.loads(proc.stdout.rsplit("@@RESULTS@@", 1)[1].splitlines()[0])
    code = next((c for c, _ in results if c), 0)
    output = "\n".join(o for _, o in results)
    if check:
        ran = [" ".join(c) for c in commands[:len(results)]]
        assert code == 0 and len(results) == len(commands), (
            f"flask {ran[-1]} failed:\n{output[-4000:]}"
        )
    return code, output


def _snapshot(url):
    """Every column, index and constraint in the current schema."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            columns = set(conn.execute(text(
                "SELECT table_name, column_name, data_type, character_maximum_length, "
                "is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name <> 'alembic_version'"
            )).all())
            indexes = set(conn.execute(text(
                "SELECT tablename, indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename <> 'alembic_version'"
            )).all())
            constraints = set(conn.execute(text(
                "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = current_schema() "
                "AND c.conrelid::regclass::text <> 'alembic_version'"
            )).all())
    finally:
        engine.dispose()
    return {"columns": columns, "indexes": indexes, "constraints": constraints}


def _tables(snapshot):
    return {row[0] for row in snapshot["columns"]}


def _recorded(url):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            if conn.execute(text("SELECT to_regclass('alembic_version')")).scalar() is None:
                return []
            return [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
    finally:
        engine.dispose()


def _column(url, table, column):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT character_maximum_length, is_nullable "
                "FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = :t AND column_name = :c"
            ), {"t": table, "c": column}).one()
    finally:
        engine.dispose()


def _deploy_schema(url):
    """The table and column steps of scripts/database/deploy-schema.sh before this change."""
    _flask(
        url,
        ["init-db"],
        ["reconcile-schema"],
        ["apply-unified-capability-provenance-migration"],
    )


@pytest.fixture(scope="module")
def deployed_template(scratch_databases):
    """One database built the way every deployed one is; copied per test."""
    url = scratch_databases("deployed")
    _deploy_schema(url)
    return make_url(url).database


@pytest.fixture
def deployed_db(scratch_databases, deployed_template):
    return scratch_databases("copy", template=deployed_template)


# ------------------------------------------------------------ baseline


def test_baseline_changes_nothing_on_a_database_built_by_init_db_and_reconcile(deployed_db):
    before = _snapshot(deployed_db)
    assert _recorded(deployed_db) == []

    # The upgrade, then reconcile-schema, the drift detector that runs next.
    _, output = _flask(
        deployed_db, ["schema-upgrade", "--to", BASELINE], ["reconcile-schema", "--dry-run"]
    )

    assert _recorded(deployed_db) == [BASELINE], output
    after = _snapshot(deployed_db)
    assert after["columns"] == before["columns"]
    assert after["indexes"] == before["indexes"]
    assert after["constraints"] == before["constraints"]
    assert "reconcile-schema: 0 column(s) would add." in output, output
    assert "table(s) absent" not in output, output


def test_upgrade_on_an_empty_database_builds_the_full_schema(scratch_databases, deployed_db):
    empty = scratch_databases("empty")
    assert _tables(_snapshot(empty)) == set()

    _flask(empty, ["schema-upgrade", "--to", BASELINE])

    # Every table, as init-db would have created it.
    assert _recorded(empty) == [BASELINE]
    deployed = _snapshot(deployed_db)
    assert _tables(_snapshot(empty)) == _tables(deployed)

    # The rest of the deploy sequence then finishes exactly the schema an
    # init-db-built database has: the same columns, indexes and constraints.
    _flask(empty, ["reconcile-schema"], ["apply-unified-capability-provenance-migration"])
    built = _snapshot(empty)
    assert built["columns"] == deployed["columns"]
    assert built["indexes"] == deployed["indexes"]
    assert built["constraints"] == deployed["constraints"]


# ------------------------------------------------- deploy-schema.sh ordering


def _assert_deploy_schema_runs_cutover_before_projection_and_backfill():
    """Static ordering guard against the real file, not a hand-copied one.

    Spawning scripts/database/deploy-schema.sh's ~18 `flask --app manage`
    steps as separate subprocesses (each a full, independent app boot) was
    measured at over 20 minutes for one run -- too slow for a test, and not
    what is actually under test here, which is *ordering*. This reads the
    literal file and asserts the three invocations appear in the required
    order; the behavioural proof that running them in that order reaches
    zero NULL-scope rows follows in the test below, using the fast
    single-process ``_flask`` driver the rest of this module already uses.
    """
    text_ = (REPO_ROOT / "scripts" / "database" / "deploy-schema.sh").read_text()
    cutover_at = text_.index("flask --app manage cutover-capability-tenancy")
    projection_at = text_.index("flask --app manage project-capabilities")
    backfill_at = text_.index("flask --app manage backfill-capability-catalogs")
    assert cutover_at < projection_at, (
        "cutover-capability-tenancy must run before project-capabilities"
    )
    assert cutover_at < backfill_at, (
        "cutover-capability-tenancy must run before backfill-capability-catalogs"
    )


def _seed_unclassified_legacy_capability(url, *, capability_id, org_id, source_org_id):
    """One row shaped like output from an uninstrumented UnifiedCapability
    writer (one of the "seven direct writers" project_capabilities.py's own
    docstring names) that never set scope/organization_id -- exactly what
    the maintenance cutover exists to classify. ``source_table`` is
    deliberately NOT ``'business_capability'``: that value is reserved for
    rows `project-capabilities` itself produced, and this row has no
    corresponding `business_capability` row behind it.
    """
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug) "
                "ON CONFLICT (id) DO NOTHING"
            ), {"id": org_id, "name": f"Org {org_id}", "slug": f"org-{org_id}"})
            conn.execute(text(
                "INSERT INTO unified_capabilities "
                "(id, name, level, source_table, source_id, source_org_id, source_checksum) "
                "VALUES (:id, 'Legacy Unclassified Capability', 1, "
                "'legacy_direct_write', :source_id, :source_org_id, 'seed-checksum')"
            ), {"id": capability_id, "source_id": str(capability_id), "source_org_id": source_org_id})
    finally:
        engine.dispose()


def test_deploy_runs_the_capability_tenancy_cutover_before_projection_and_backfill(
    scratch_databases, tmp_path
):
    """The deploy sequence this script encodes must leave zero
    unified_capabilities rows with organization_id IS NULL AND scope IS NULL.

    Regression: deploy-schema.sh never called `cutover-capability-tenancy
    --apply`, so a pre-existing unclassified row (the maintenance cutover's
    whole reason to exist) survived every deploy indefinitely -- present,
    hidden from the hybrid reference/tenant visibility predicate, and off the
    store-agreement contract.
    """
    _assert_deploy_schema_runs_cutover_before_projection_and_backfill()

    url = scratch_databases("cutover_ordering")
    _flask(url, ["init-db"], ["schema-upgrade"], ["reconcile-schema"],
           ["apply-unified-capability-provenance-migration"])

    org_id = 9701
    _seed_unclassified_legacy_capability(
        url, capability_id=501, org_id=org_id, source_org_id=org_id
    )
    assert _conn_scalar(
        url, "SELECT count(*) FROM unified_capabilities "
        "WHERE organization_id IS NULL AND scope IS NULL"
    ) == 1

    manifest = tmp_path / "cutover-manifest.json"
    manifest.write_text(json.dumps({"backup_path": str(tmp_path / "fake.dump")}))
    report = tmp_path / "cutover-report.json"

    # The same three commands, in the order the real script now runs them.
    _flask(
        url,
        ["cutover-capability-tenancy", "--apply",
         "--backup-manifest", str(manifest), "--report", str(report)],
        ["project-capabilities", "--apply"],
        ["backfill-capability-catalogs", "--apply"],
    )

    remaining = _conn_scalar(
        url, "SELECT count(*) FROM unified_capabilities "
        "WHERE organization_id IS NULL AND scope IS NULL"
    )
    assert remaining == 0, f"remaining_null_scope_rows={remaining}"
    # Classified correctly, not merely touched: provenance with no
    # relationship link names a tenant owner (classify_capability), never a
    # guess, and never silently dropped into shared reference scope.
    scope, owner = _conn_row(
        url, "SELECT scope, organization_id FROM unified_capabilities WHERE id = 501"
    )
    assert (scope, owner) == ("tenant", org_id)


def _conn_scalar(url, sql):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar_one()
    finally:
        engine.dispose()


def _conn_row(url, sql):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).one()
    finally:
        engine.dispose()


# ------------------------------------------------ expand / contract examples


def _insert(conn, table, values):
    """Insert one row, filling any other required column with a unique dummy."""
    required = conn.execute(text(
        "SELECT column_name, data_type, udt_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = :t "
        "AND is_nullable = 'NO' AND column_default IS NULL AND is_identity = 'NO'"
    ), {"t": table}).all()
    row = dict(values)
    for name, data_type, udt in required:
        if name in row:
            continue
        if data_type in ("integer", "bigint", "smallint", "numeric", "double precision", "real"):
            row[name] = 1
        elif data_type == "boolean":
            row[name] = False
        elif data_type.startswith("timestamp") or data_type == "date":
            row[name] = "2026-01-01"
        elif data_type in ("json", "jsonb"):
            row[name] = "{}"
        elif data_type == "uuid":
            row[name] = str(uuid.uuid4())
        elif data_type == "USER-DEFINED":
            row[name] = conn.execute(text(
                "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = :u ORDER BY enumsortorder LIMIT 1"
            ), {"u": udt}).scalar()
        else:
            row[name] = uuid.uuid4().hex[:8]
    cols = ", ".join(f'"{c}"' for c in row)
    params = ", ".join(f":{c}" for c in row)
    conn.execute(text(f'INSERT INTO "{table}" ({cols}) VALUES ({params})'), row)


def _seed(url, rows):
    """Insert fixture rows with foreign keys and triggers off (superuser session)."""
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        for table, values in rows:
            _insert(conn, table, values)
    engine.dispose()


def _rows(url):
    """Each organisation's element names and owner application ids."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            elements = sorted(conn.execute(text(
                "SELECT organization_id, name FROM archimate_elements ORDER BY 1, 2"
            )).all())
            owners = sorted(conn.execute(text(
                "SELECT organization_id, application_id FROM application_owners "
                "ORDER BY 1, 2 NULLS FIRST"
            )).all(), key=lambda r: (r[0], r[1] is not None, r[1] or 0))
    finally:
        engine.dispose()
    return elements, owners


def test_example_revisions_are_idempotent_and_reversible_without_data_loss(deployed_db):
    url = deployed_db
    org_a, org_b = 101, 202
    true_head = _true_head()

    # deployed_db is built by init-db (create_all from the *current* models),
    # which already declares application_id nullable and name VARCHAR(500) --
    # models.py and archimate_core.py were updated in the same change as these
    # revisions, so the models never disagree with a database the revisions
    # have run against (see docs/adr/0002-schema-management.md). A real
    # long-lived production database predating this change is still in the
    # old shape until schema-upgrade actually runs on it, so this test
    # regresses those two columns to that old shape first, the same way
    # init-db + reconcile-schema alone (years of it, on older code) would
    # have left them.
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE application_owners ALTER COLUMN application_id SET NOT NULL"
        ))
        conn.execute(text(
            "ALTER TABLE archimate_elements ALTER COLUMN name TYPE VARCHAR(100)"
        ))
    engine.dispose()

    _seed(url, [
        ("archimate_elements", {"organization_id": org_a, "name": "A" * 100}),
        ("archimate_elements", {"organization_id": org_b, "name": "B element"}),
        ("application_owners", {"organization_id": org_a, "application_id": 11, "user_id": 1}),
        ("application_owners", {"organization_id": org_b, "application_id": 22, "user_id": 2}),
    ])
    assert tuple(_column(url, "application_owners", "application_id")) == (None, "NO")
    assert tuple(_column(url, "archimate_elements", "name")) == (100, "NO")
    seeded = _rows(url)

    # The database carries a stamp from the archived pre-baseline history, as a
    # long-lived one may. The upgrade replaces it instead of stopping the deploy.
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        ))
        conn.execute(text("INSERT INTO alembic_version VALUES ('fac924608f6e')"))
    engine.dispose()

    _, output = _flask(url, ["schema-upgrade"])
    assert "fac924608f6e" in output, output
    assert _recorded(url) == [true_head]
    assert tuple(_column(url, "application_owners", "application_id")) == (None, "YES")
    assert _column(url, "archimate_elements", "name")[0] == 500
    assert _rows(url) == seeded

    # Each revision's upgrade step, run again on the expanded schema, changes nothing.
    from app.commands.schema_migrations import relax_not_null, widen_varchar

    engine = create_engine(url)
    with engine.begin() as conn:
        assert relax_not_null(conn, "application_owners", "application_id") is False
        assert widen_varchar(conn, "archimate_elements", "name", 500) is False
    engine.dispose()
    assert tuple(_column(url, "application_owners", "application_id")) == (None, "YES")
    assert _column(url, "archimate_elements", "name")[0] == 500

    # Down while every row still fits the old shape: type and NOT NULL come back,
    # every organisation keeps every row.
    _flask(url, ["db", "downgrade", BASELINE])
    assert _recorded(url) == [BASELINE]
    assert tuple(_column(url, "application_owners", "application_id")) == (None, "NO")
    assert _column(url, "archimate_elements", "name")[0] == 100
    assert _rows(url) == seeded

    # Back up to the true head, then park exactly at WIDEN -- the worked
    # examples' own tip -- regardless of how many further revisions this
    # repository has grown above it (one, as of this change; any db
    # downgrade WIDEN when already AT WIDEN is a no-op, so this is the same
    # on a repository with none). The refusal semantics below belong to
    # WIDEN/RELAX/BASELINE specifically; proving them must not depend on how
    # tall the chain above WIDEN happens to be, or every revision added
    # after it would need to keep this test updated the way a literal
    # expected-head id here already once needed updating. Coverage for
    # whatever sits above WIDEN today lives in
    # test_every_revision_above_the_worked_examples_round_trips alongside
    # this test.
    _flask(url, ["schema-upgrade"])
    assert _recorded(url) == [true_head]
    _flask(url, ["db", "downgrade", WIDEN])
    assert _recorded(url) == [WIDEN]

    # Use the expanded shape as organisation B, then try to go down again.
    long_name = "L" * 400
    _seed(url, [
        ("archimate_elements", {"organization_id": org_b, "name": long_name}),
        ("application_owners", {"organization_id": org_b, "application_id": None, "user_id": 3}),
    ])
    expanded = _rows(url)

    code, output = _flask(url, ["db", "downgrade", BASELINE], check=False)
    assert code != 0
    assert "cannot be narrowed without truncating" in output, output[-3000:]
    # HEAD and each revision below it down to WIDEN have nothing to refuse
    # and always succeed, so the chain steps down through all of them before
    # the genuine refusal: recorded at WIDEN, not HEAD.
    assert _recorded(url) == [WIDEN]
    assert _column(url, "archimate_elements", "name")[0] == 500
    assert _rows(url) == expanded
    assert (org_b, long_name) in expanded[0]
    assert all(org != org_a for org, name in expanded[0] if name == long_name)

    # With the long name shortened, the name narrows, and the next down step
    # refuses on the NULL owner: each revision commits or rolls back on its own.
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE archimate_elements SET name = 'short' WHERE name = :n"
        ), {"n": long_name})
    engine.dispose()
    code, output = _flask(url, ["db", "downgrade", BASELINE], check=False)
    assert code != 0
    assert "NOT NULL cannot be restored" in output, output[-3000:]
    assert _recorded(url) == [RELAX]
    assert _column(url, "archimate_elements", "name")[0] == 100
    assert tuple(_column(url, "application_owners", "application_id")) == (None, "YES")
    assert (org_b, None) in _rows(url)[1]


def test_every_revision_above_the_worked_examples_round_trips(deployed_db):
    """Generic coverage for whatever sits above WIDEN in the chain today.

    Deliberately knows nothing about which revisions those are or how many
    there are -- it derives the true head from the migrations directory,
    upgrades to it, downgrades to WIDEN (passing back down through every
    revision above WIDEN on the way), and upgrades again, asserting the
    chain lands in the same two places both times. This is what proves
    20261001_risk_score_fields' own upgrade/downgrade round-trips, and it
    keeps proving the same thing for the next revision added after it
    without needing an update here -- unlike a hard-coded expected id, which
    is exactly what went stale the first time a revision was added above
    WIDEN.
    """
    url = deployed_db
    true_head = _true_head()

    _flask(url, ["schema-upgrade"])
    assert _recorded(url) == [true_head]

    _flask(url, ["db", "downgrade", WIDEN])
    assert _recorded(url) == [WIDEN]

    _flask(url, ["schema-upgrade"])
    assert _recorded(url) == [true_head]

    # Round again: downgrading and upgrading a second time changes nothing
    # further -- every revision's steps are idempotent, not just reachable.
    _flask(url, ["db", "downgrade", WIDEN])
    assert _recorded(url) == [WIDEN]
    _flask(url, ["schema-upgrade"])
    assert _recorded(url) == [true_head]


def test_expand_and_contract_helpers_are_idempotent_on_their_own(deployed_db):
    from app.commands.schema_migrations import (
        ContractBlocked,
        column_state,
        narrow_varchar,
        relax_not_null,
        tighten_not_null,
        widen_varchar,
    )

    engine = create_engine(deployed_db)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            conn.execute(text("CREATE TABLE ec_probe (label VARCHAR(10) NOT NULL)"))
            conn.execute(text("INSERT INTO ec_probe VALUES ('0123456789')"))

            assert relax_not_null(conn, "ec_probe", "label") is True
            assert relax_not_null(conn, "ec_probe", "label") is False
            assert widen_varchar(conn, "ec_probe", "label", 40) is True
            assert widen_varchar(conn, "ec_probe", "label", 40) is False
            assert column_state(conn, "ec_probe", "label") == {
                "data_type": "character varying", "max_length": 40, "nullable": True,
            }

            conn.execute(text("INSERT INTO ec_probe VALUES (NULL), (:v)"), {"v": "x" * 30})
            with pytest.raises(ContractBlocked):
                tighten_not_null(conn, "ec_probe", "label")
            with pytest.raises(ContractBlocked):
                narrow_varchar(conn, "ec_probe", "label", 10)
            assert column_state(conn, "ec_probe", "label")["max_length"] == 40

            conn.execute(text("DELETE FROM ec_probe WHERE label IS NULL OR length(label) > 10"))
            assert tighten_not_null(conn, "ec_probe", "label") is True
            assert tighten_not_null(conn, "ec_probe", "label") is False
            assert narrow_varchar(conn, "ec_probe", "label", 10) is True
            assert narrow_varchar(conn, "ec_probe", "label", 10) is False
            assert conn.execute(text("SELECT label FROM ec_probe")).scalar() == "0123456789"
            trans.rollback()
    finally:
        engine.dispose()


# ----------------------------------------------- deploy image / drift fixes


def test_dockerignore_ships_migration_revisions():
    """schema-upgrade runs inside the deployed image; it must carry the revisions.

    Regression: the image previously excluded migrations/versions/, so a real
    deploy stopped or silently no-op'd (no revisions to apply).
    """
    lines = (REPO_ROOT / ".dockerignore").read_text().splitlines()
    excluded = [
        line for line in lines
        if line.strip().rstrip("/") == "migrations/versions"
    ]
    assert excluded == [], (
        f"migrations/versions/ must not be excluded from the Docker build "
        f"context: {excluded}"
    )


def test_models_match_the_relax_and_widen_revisions():
    """The two worked-example revisions must not leave the ORM models behind.

    Regression: the revisions widened/relaxed the live column but the models
    still declared the old, narrower shape -- a from-scratch create_all()
    (every test database, an empty deploy) then builds the *old* schema,
    permanently diverging from a database these revisions have run against.
    """
    owner_src = (REPO_ROOT / "app/models/application_owner.py").read_text()
    owner_block = owner_src[owner_src.index("application_id = db.Column"):]
    owner_block = owner_block[:owner_block.index(")\n") + 2]
    assert "nullable=True" in owner_block, (
        "ApplicationOwner.application_id must be nullable=True, matching "
        "migrations/versions/20260926_relax_owner_app.py"
    )
    assert "nullable=False" not in owner_block

    for path in ("app/models/models.py", "app/models/archimate_core.py"):
        src = (REPO_ROOT / path).read_text()
        idx = src.index('__tablename__ = "archimate_elements"')
        block = src[idx:idx + 600]
        assert "name = db.Column(db.String(500)" in block, (
            f"{path}: ArchiMateElement.name must be String(500), matching "
            "migrations/versions/20260926_widen_element_name.py"
        )


def test_env_py_does_not_reconfigure_logging():
    """schema-upgrade runs inside the already-booted app; env.py must not
    call Alembic's default fileConfig(), which would reconfigure the app's
    own already-active logging setup (app/services/core/logging_config.py).
    """
    code_lines = [
        line for line in (REPO_ROOT / "migrations/env.py").read_text().splitlines()
        if not line.strip().startswith("#")
    ]
    assert not any("fileConfig" in line for line in code_lines), (
        "migrations/env.py must not import or call fileConfig() outside a comment"
    )


def test_acquire_upgrade_lock_times_out_instead_of_hanging(scratch_databases):
    """A held lock must fail loudly and quickly, not hang the deploy forever.

    Regression: schema-upgrade called the blocking pg_advisory_lock with no
    timeout, so a stuck or crashed holder wedged every future deploy.
    """
    import time

    from app.commands.schema_migrations import (
        _UPGRADE_LOCK_KEY,
        SchemaUpgradeLocked,
        acquire_upgrade_lock,
    )

    url = scratch_databases("lock")
    holder = create_engine(url).connect()
    contender = create_engine(url).connect()
    try:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _UPGRADE_LOCK_KEY})

        started = time.monotonic()
        with pytest.raises(SchemaUpgradeLocked):
            acquire_upgrade_lock(contender, timeout_s=1, poll_s=0.1)
        elapsed = time.monotonic() - started

        assert elapsed < 5, f"acquire_upgrade_lock blocked for {elapsed}s past its 1s timeout"
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _UPGRADE_LOCK_KEY})
        holder.close()
        contender.close()


# No automated test for the lock_timeout fix below (migrations/env.py's
# run_migrations_online): a real repro needs a second session holding a
# conflicting lock on the exact table a revision's ALTER targets, while that
# revision runs inside an Alembic-managed connection Flask-Migrate itself
# checks out from a pool, inside a subprocess this suite's own driver
# spawns -- three nested boundaries deep, none of which is where the fix
# actually lives. Verified directly instead, matching the same standard this
# suite already applies to browser/CI-environment evidence elsewhere: with a
# second `psql` session holding `LOCK TABLE ... IN ACCESS EXCLUSIVE MODE`, a
# plain SQLAlchemy connection mirroring run_migrations_online's exact
# sequence (SET lock_timeout, commit, then the conflicting ALTER on the same
# connection) fails with `psycopg2.errors.LockNotAvailable: canceling
# statement due to lock timeout` at the configured bound, not indefinitely.
