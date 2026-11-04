"""Shared test fixtures.

Before this file existed, every test module hand-rolled a module-scoped ``app``
fixture and cleaned up its own rows, because the test database is shared and
persistent. That is flaky by construction: a test that fails partway through
leaves rows behind, and the next run sees them.

The ``db_session`` fixture here replaces that pattern with a transaction that is
always rolled back, so no test can leave residue even if it fails mid-way.

Backwards compatibility
-----------------------
The eight pre-existing test modules define their own module-scoped ``app``
fixture. pytest resolves the *closest* definition, so those keep working
untouched — the fixtures below are opt-in for new tests.

Requires PostgreSQL: ``TestingConfig`` rejects SQLite outright. Set
``TEST_DATABASE_URL`` before running.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


# Tests must never inherit a developer's or deployment agent's LLM credentials.
# This runs while pytest imports its root conftest, before it collects test
# modules that can import the Flask configuration.  The primary service supports
# numbered and secondary keys for provider failover, so clear those too.
_LLM_API_KEY_PROVIDERS = (
    "ANTHROPIC",
    "AZURE",
    "CLAUDE",
    "GEMINI",
    "GOOGLE",
    "HUGGINGFACE",
    "NVIDIA",
    "OPENAI",
    "OPENROUTER",
)
_LLM_API_KEY_ENV_VARS = {"LLM_API_KEY", "AZURE_OPENAI_API_KEY"}
for _provider in _LLM_API_KEY_PROVIDERS:
    _LLM_API_KEY_ENV_VARS.add(f"{_provider}_API_KEY")
    _LLM_API_KEY_ENV_VARS.add(f"{_provider}_API_KEY_SECONDARY")
    _LLM_API_KEY_ENV_VARS.update(
        f"{_provider}_API_KEY_{_number}" for _number in range(1, 10)
    )

for _llm_api_key_env_var in _LLM_API_KEY_ENV_VARS:
    # Keep a deliberately empty process value instead of deleting it. python-
    # dotenv's default override=False then treats it as already defined and
    # cannot repopulate a real credential from a parent .env file, including
    # on releases that do not support PYTHON_DOTENV_DISABLED.
    os.environ[_llm_api_key_env_var] = ""
os.environ["PYTHON_DOTENV_DISABLED"] = "1"


def _quote(connection, name):
    return connection.dialect.identifier_preparer.quote(name)


@pytest.fixture(scope="session")
def run_id():
    """A short id unique to this pytest process.

    Roles are cluster-wide and database names are shared by every run on the
    cluster, so two runs (or two seats) with the same xdist worker id must not
    share either.
    """
    return uuid.uuid4().hex[:8]


def _copy_when_source_is_free(connection, worker_q, template_q, template_db_name, wait_seconds=120):
    """Copy the source database, waiting while another session is connected to it
    (a serial run or a shell on the same database), then fail in plain words."""
    import time

    from sqlalchemy.exc import OperationalError

    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            connection.execute(text(f"CREATE DATABASE {worker_q} TEMPLATE {template_q}"))  # nosec B608 - quoted identifiers
            return
        except OperationalError as exc:
            if "being accessed by other users" not in str(exc):
                raise
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Could not copy {template_db_name} for a parallel worker: another session stayed "
                    f"connected to it for {wait_seconds}s. Run without -n, or close that session."
                ) from exc
            time.sleep(2)


@pytest.fixture(scope="session")
def worker_database(request, run_id):
    """Create a per-worker database if running in parallel, otherwise use the default.

    The database is copied from TEST_DATABASE_URL's own database at session start and
    dropped at session end, so every worker starts with the schema a serial run of this
    branch uses (TEST_TEMPLATE_DATABASE names another source when set). Its name is
    TEST_DATABASE_URL's database with the worker id and a run id appended (e.g.
    archie_test_xdist01_gw0_1a2b3c4d).
    """
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")

    # The master worker in a parallel run can reuse the main database.
    if worker_id == "master":
        yield
        return

    db_url = os.environ.get("TEST_DATABASE_URL")
    if not db_url:
        pytest.skip("TEST_DATABASE_URL not set, skipping parallel database setup.")

    base_url = make_url(db_url)
    worker_db_name = f"{base_url.database}_{worker_id}_{run_id}"
    worker_db_url = base_url.set(database=worker_db_name).render_as_string(hide_password=False)

    # A shared seat template can lag the branch by several revisions (a column the models
    # map is missing there), so the copy is taken from the database a serial run uses.
    template_db_name = os.environ.get("TEST_TEMPLATE_DATABASE", base_url.database)

    # Connect to the postgres database (not the template) to create the worker database,
    # and copy one worker at a time: a copy fails with "source database is being accessed
    # by other users" while anything else is connected to its source.
    admin = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            worker_q = _quote(connection, worker_db_name)
            template_q = _quote(connection, template_db_name)
            lock = connection.execute(text("SELECT hashtext(:n)"), {"n": template_db_name}).scalar_one()
            connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": lock})
            try:
                connection.execute(text(f"DROP DATABASE IF EXISTS {worker_q} WITH (FORCE)"))  # nosec B608 - quoted identifiers
                _copy_when_source_is_free(connection, worker_q, template_q, template_db_name)
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock})
    finally:
        admin.dispose()

    original_url = db_url
    os.environ["TEST_DATABASE_URL"] = worker_db_url

    def finalizer():
        os.environ["TEST_DATABASE_URL"] = original_url
        cleanup = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        try:
            with cleanup.connect() as connection:
                connection.execute(
                    text(f"DROP DATABASE IF EXISTS {_quote(connection, worker_db_name)} WITH (FORCE)")  # nosec B608 - quoted identifier
                )
        finally:
            cleanup.dispose()

    request.addfinalizer(finalizer)

    yield


@pytest.fixture(scope="session")
def app(request, worker_database):
    """Boot the application once per test session."""
    os.environ.setdefault("FLASK_CONFIG", "testing")
    from app import create_app

    application = create_app("testing")
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    return application


def seed_implementation_planning_flag():
    """Switch the implementation planning module on (committed). The one place
    the ``architecture_implementation_planning`` flag is seeded for tests;
    idempotent. Needs an application context."""
    from app import db
    from app.models.feature_flags import FeatureFlag, FeatureState

    flag = FeatureFlag.query.filter_by(key="architecture_implementation_planning").first()
    if flag is None:
        flag = FeatureFlag(
            key="architecture_implementation_planning",
            name="Architecture Implementation Planning",
            description="Enable the Implementation Planning module (gap discovery, work packages, plateaus)",
            enabled=True,
            state=FeatureState.STABLE,
        )
        db.session.add(flag)
    else:
        flag.enabled = True
        flag.state = FeatureState.STABLE
    db.session.commit()


@pytest.fixture(scope="session")
def _schema(app):
    """Ensure tables exist once per session.

    ``create_all()`` only creates *missing* tables, so this is safe against a
    shared database that already has a schema.  It does not add missing columns —
    see the schema-drift gate in scripts/verify.py for that.

    It also seeds the standard roles, as a deployed database has them. A user
    created without an explicit role takes the default one, so without this a
    test\'s permissions depended on whether some earlier test in the same run
    had happened to seed them, and a test run on its own, or in a CI shard
    without that earlier test, was refused with 403.
    """
    from app import db
    from app.models.user import Role

    with app.app_context():
        db.create_all()
        # create_all never builds the unique index on unified_capabilities
        # (source_table, source_id); a deployed database gets it from a revision.
        # Without it a new business capability is silently not projected.
        from app.commands.apply_unified_capability_provenance_migration import (
            ensure_provenance_index,
        )

        ensure_provenance_index(db.session.connection())
        db.session.commit()
        Role.insert_roles()
        seed_implementation_planning_flag()
        db.session.remove()
    return True

@pytest.fixture
def db_session(app, _schema):
    """A database session whose work is always rolled back.

    Routes ``db.session`` to an outer transaction per configured engine.
    ``join_transaction_mode="create_savepoint"`` means a
    ``session.commit()`` inside the test resolves to a SAVEPOINT release rather
    than a real COMMIT, so committing code under test behaves normally while the
    outer transaction still discards everything at teardown.

    Direct engine connections are not session operations and remain outside this
    fixture's rollback contract. Engine objects are deliberately left intact.
    """
    from contextlib import ExitStack

    from app import db

    # A dedicated context keeps any caller's existing scoped session untouched.
    with app.app_context(), ExitStack() as resources:
        factory = db.session.session_factory
        original_class = factory.class_
        original_options = dict(factory.kw)
        connections = {}
        for engine in dict.fromkeys(db.engines.values()):
            connection = resources.enter_context(engine.connect())
            transaction = connection.begin()
            resources.callback(transaction.rollback)
            connections[engine] = connection

        class RollbackSession(original_class):
            def get_bind(self, mapper=None, clause=None, bind=None, **kwargs):
                # Flask-SQLAlchemy selects db.engines before Session.bind, even
                # for mapped writes. Preserve that routing, then substitute the
                # owned connection. Subclassing preserves registered listeners.
                resolved = super().get_bind(
                    mapper=mapper, clause=clause, bind=bind, **kwargs
                )
                return connections.get(resolved, resolved)

        try:
            db.session.remove()
            factory.class_ = RollbackSession
            db.session.configure(join_transaction_mode="create_savepoint")
            assert db.session.get_bind() is connections[db.engine]
            assert db.session.get_bind().in_transaction()
            yield db.session
        finally:
            try:
                db.session.remove()
            finally:
                factory.class_ = original_class
                factory.kw.clear()
                factory.kw.update(original_options)


@pytest.fixture
def make_org(db_session):
    """Factory for Organization rows with collision-free names."""

    def _make(label: str = "org"):
        from app.models.organization import Organization

        suffix = uuid.uuid4().hex[:10]
        org = Organization(name=f"Test {label} {suffix}", slug=f"test-{label}-{suffix}")
        db_session.add(org)
        db_session.flush()
        return org

    return _make


@pytest.fixture
def tenant_ctx(app):
    """Enter a request-scoped tenant context, as a logged-in request would.

    The isolation middleware keys off ``g.current_org_id`` and is a deliberate
    no-op without it, so any test asserting isolation must establish it.

        with tenant_ctx(org.id):
            ...  # queries here are scoped to org.id
    """
    import contextlib

    from flask import g

    @contextlib.contextmanager
    def _ctx(org_id):
        # A test request context so `g` behaves as it does in a real request.
        with app.test_request_context("/"):
            g.current_org_id = org_id
            yield

    return _ctx


@pytest.fixture
def enrolled_admin():
    """Every administrator a test creates (a platform admin, or a user holding
    the Administrator role or an organisation-admin flag) is recorded as having
    enrolled for two-factor sign-in, so an administrator the test signs in by writing the session
    passes the real enrolment checks instead of being sent to enrol. The checks
    stay on: ``must_enrol_now`` and ``step_up_needed`` run unchanged and see an
    enrolled user. A module asks for it with
    ``pytest.mark.usefixtures("enrolled_admin")``; a test that is about the
    unenrolled case does not."""
    from datetime import datetime

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    def _enrol(session, flush_context, instances):
        for user in session.new:
            if type(user).__name__ != "User":
                continue
            role = getattr(user, "role", None)
            if not (
                getattr(user, "is_platform_admin", False)
                or getattr(user, "is_org_admin", False)
                or getattr(role, "name", None) == "Administrator"
            ):
                continue
            if not user.mfa_enabled:
                user.mfa_enabled = True
            if user.mfa_enrolled_at is None:
                user.mfa_enrolled_at = datetime.utcnow()

    event.listen(Session, "before_flush", _enrol)
    try:
        yield
    finally:
        event.remove(Session, "before_flush", _enrol)


def _listed(name):
    import os

    here = os.path.dirname(__file__)
    with open(os.path.join(here, name)) as handle:
        return {line.split("#")[0].strip() for line in handle if line.split("#")[0].strip()}


def pytest_collection_modifyitems(config, items):
    """Give the modules in enrolled_admin_modules.txt ``enrolled_admin``."""
    import os

    here = os.path.dirname(__file__)
    enrolled = _listed("enrolled_admin_modules.txt")
    for item in items:
        module = os.path.relpath(str(item.path), here).replace(os.sep, "/")
        if module in enrolled:
            item.fixturenames.insert(0, "enrolled_admin")


@pytest.fixture
def client(app):
    """Repository-owned Flask client; do not depend on pytest-flask being installed."""
    return app.test_client()


@pytest.fixture
def login_as(app):
    """Log a client in as ``user``, defeating flask_login's ``g`` cache.

    Three separate agents independently rediscovered the same trap in one
    session, and 51 test modules hand-roll their own copy of this helper, so
    it belongs here rather than in a comment.

    The trap: ``db_session`` holds ONE app context open for the whole test, so
    ``g`` survives between test-client requests. flask_login caches the
    resolved user on ``g._login_user``, and our tenant middleware caches
    ``g.current_org_id``. Writing to the session cookie therefore does not
    change who the next request executes as — a second client keeps running as
    the first client's user (which reads as a tenancy leak that does not
    exist), and a request issued before any login caches an *anonymous* user
    that later 401s a properly-authenticated call.

    Call this immediately before each request whose identity matters:

        login_as(client, user)
        resp = client.get("/solutions/")

    Note ordering: seed and flush the user BEFORE logging in, or the loader
    cannot resolve the id and the request redirects to the login page.
    """
    from flask import g, has_app_context

    def _login(client, user):
        user_id = getattr(user, "id", user)
        # Session revocation (session-invalidation-on-logout) checks a
        # server-side registry record keyed by "_sid" on every authenticated
        # request and fails CLOSED when it is missing. Writing "_user_id"
        # alone -- the pre-existing shape of this fixture -- would now bounce
        # every caller to the login page, so mint a real registry row the
        # same way the production login path does.
        # One accessor for test-session minting (ADR 0008) -- see
        # tests/_session_test_helpers.py::mint_test_sid, the same logic this
        # used to reimplement inline.
        from tests._session_test_helpers import mint_test_sid

        real_user = user if hasattr(user, "id") else None
        org_id = getattr(real_user, "organization_id", None) if real_user is not None else None
        sid = mint_test_sid(user_id, organization_id=org_id, app=app)
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
            sess["_sid"] = sid
        if not has_app_context():
            return
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)

    return _login


@pytest.fixture(scope="module")
def site_url(app):
    """The app served on a loopback port for browser journeys, one server per test module.

    Named site_url, not base_url: pytest-base-url owns a session-scoped base_url, and a module-scoped
    fixture of that name fails every test that uses it with ScopeMismatch.
    """
    import threading

    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
