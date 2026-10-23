"""The engine-level tenant context hook, its modes, the shim and the factories (ADR 0013).

These tests drive the mechanism on engines of their own against the suite's
PostgreSQL database (the row-level results are in test_tenant_context_pooled_reuse.py,
which runs as the restricted role). They check what the hook writes, when, in
which mode, and what it leaves alone.
"""

from __future__ import annotations

import logging
import threading

import pytest
from flask import g
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool, QueuePool

from app.middleware import tenant_isolation as ti
from tests._tenant_cursors import recording_cursor_class


STATEMENTS: list = []


def _recorded(fragment):
    return [s for s in STATEMENTS if fragment in s]


@pytest.fixture
def make_engine(app):
    ti._install_engine_hooks()
    engines = []

    def make(pool="queue", **kwargs):
        url = app.config["SQLALCHEMY_DATABASE_URI"]
        options = {
            "connect_args": {"cursor_factory": recording_cursor_class(url, STATEMENTS)}
        }
        if pool == "queue":
            options.update(poolclass=QueuePool, pool_size=1, max_overflow=0, pool_timeout=5,
                           pool_reset_on_return="commit")  # as production: a commit keeps session-level settings
        else:
            options.update(poolclass=NullPool)
        engine = create_engine(url, **options, **kwargs)
        engines.append(engine)
        return engine

    del STATEMENTS[:]
    ti._reset_disagreement_log()
    yield make
    for engine in engines:
        engine.dispose()


@pytest.fixture
def mode(monkeypatch):
    def set_mode(value):
        monkeypatch.setattr(ti, "_default_mode", value)

    return set_mode


def _setting(connection):
    row = connection.execute(
        text("SELECT current_setting('archie.organization_id', true), "
             "current_setting('archie.platform_scope', true)")
    ).one()
    return tuple(value or "" for value in row)


# --------------------------------------------------------------------------- #
# The ambient context and bind_tenant
# --------------------------------------------------------------------------- #


def test_no_application_context_resolves_to_no_tenant():
    assert ti.ambient_tenant_context() == ti.NO_TENANT


def test_ambient_reads_g_attributes_only(app):
    with app.app_context():
        g.current_org_id = 5
        g._platform_scope = "reason"
        assert ti.ambient_tenant_context() == ti.TenantContext(5, True)


@pytest.mark.parametrize("bad", [True, False, "1", 0, -3, 1.5])
def test_a_value_that_is_not_an_organisation_never_resolves(app, bad):
    with app.app_context():
        g.current_org_id = bad
        with pytest.raises(ValueError):
            ti.ambient_tenant_context()


def test_bind_tenant_narrows_and_restores(app):
    with app.app_context():
        g._platform_scope = "reason"
        with ti.bind_tenant(7):
            assert ti.ambient_tenant_context() == ti.TenantContext(7, False)
        assert ti.ambient_tenant_context() == ti.TenantContext(None, True)


def test_run_with_tenant_carries_the_callers_context_into_a_thread():
    seen = {}

    def work():
        seen["thread"] = ti.ambient_tenant_context()

    with ti.bind_tenant(9):
        wrapped = ti.run_with_tenant(work)
    thread = threading.Thread(target=wrapped)
    thread.start()
    thread.join(10)
    assert seen["thread"] == ti.TenantContext(9, False)
    assert ti.ambient_tenant_context() == ti.NO_TENANT


# --------------------------------------------------------------------------- #
# enforce
# --------------------------------------------------------------------------- #


def test_enforce_writes_the_context_before_the_first_statement(make_engine, mode):
    mode("enforce")
    engine = make_engine()
    with ti.bind_tenant(7), engine.connect() as connection:
        assert tuple(_setting(connection)) == ("7", "")
    with engine.connect() as connection:
        assert tuple(_setting(connection)) == ("", "")


def test_enforce_rewrites_when_the_context_changes_inside_a_transaction(make_engine, mode):
    mode("enforce")
    engine = make_engine()
    with engine.connect() as connection:
        with ti.bind_tenant(7):
            assert _setting(connection)[0] == "7"
            writes = len(_recorded("set_config('archie.organization_id', %s"))
            assert _setting(connection)[0] == "7"
            assert len(_recorded("set_config('archie.organization_id', %s")) == writes
        assert _setting(connection)[0] == ""
        with ti.bind_tenant(8):
            assert _setting(connection)[0] == "8"


def test_enforce_clears_the_cached_state_on_savepoint_rollback(make_engine, mode):
    """ROLLBACK TO SAVEPOINT restores the older organisation in the database; the
    hook must not believe the newer one is still there."""
    mode("enforce")
    engine = make_engine()
    with engine.connect() as connection, ti.bind_tenant(7):
        assert _setting(connection)[0] == "7"
        savepoint = connection.begin_nested()
        with ti.bind_tenant(8):
            assert _setting(connection)[0] == "8"
            savepoint.rollback()
            assert _setting(connection)[0] == "8"


def test_enforce_refuses_an_autocommit_connection_with_an_organisation(make_engine, mode):
    mode("enforce")
    engine = make_engine()
    with engine.connect() as connection:
        autocommit = connection.execution_options(isolation_level="AUTOCOMMIT")
        autocommit.execute(text("SELECT 1"))  # no context: allowed
        with ti.bind_tenant(7):
            with pytest.raises(ti.TenantContextUnsupported):
                autocommit.execute(text("SELECT 1"))


# --------------------------------------------------------------------------- #
# log
# --------------------------------------------------------------------------- #


def test_log_mode_writes_nothing_and_logs_each_class_once(make_engine, mode, caplog):
    mode("log")
    caplog.set_level(logging.INFO)
    engine = make_engine()
    manual = "SELECT set_config('archie.organization_id', :v, true)"

    # missing: the context says 7, the connection carries nothing
    with ti.bind_tenant(7), engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        connection.execute(text("SELECT 2"))
    # excess: the connection carries 9, the context says nothing
    with engine.connect() as connection:
        connection.execute(text(manual), {"v": "9"})
        connection.execute(text("SELECT 1"))
    # conflict: the connection carries 9, the context says 7
    with ti.bind_tenant(7), engine.connect() as connection:
        connection.execute(text(manual), {"v": "9"})
        connection.execute(text("SELECT 1"))
    # autocommit: enforce would refuse
    with ti.bind_tenant(7), engine.connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT").execute(text("SELECT 1"))

    assert not _recorded("set_config('archie.organization_id', %s")  # the hook wrote nothing
    lines = [r.getMessage() for r in caplog.records if r.name == "app.tenant_context_guard"]
    classes = sorted(line.split("class=")[1].split()[0] for line in lines)
    assert classes == ["autocommit", "conflict", "excess", "missing"], lines
    for line in lines:
        assert line.startswith("tenant context disagreement class=")
        assert all(word not in line for word in ("ERROR", "CRITICAL", "Traceback"))
        assert " site=" in line and " count=" in line


def test_log_mode_rate_limits_a_repeated_disagreement(make_engine, mode, caplog):
    mode("log")
    caplog.set_level(logging.INFO)
    engine = make_engine()
    for _ in range(3):
        with ti.bind_tenant(7), engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    lines = [r.getMessage() for r in caplog.records if r.name == "app.tenant_context_guard"]
    assert len(lines) == 1


def test_log_mode_still_enforces_a_factory_connection(app, mode, caplog, monkeypatch):
    mode("log")
    monkeypatch.setitem(app.config, "ARCHIE_TENANT_GUC_MODE", "log")
    from app.extensions import db

    ti._install_engine_hooks()
    with app.app_context(), ti.bind_tenant(7):
        with ti.independent_connection() as connection:
            assert tuple(_setting(connection)) == ("7", "")
        with db.engine.connect() as connection:
            assert tuple(_setting(connection)) == ("", "")  # ordinary connection: untouched


# --------------------------------------------------------------------------- #
# off
# --------------------------------------------------------------------------- #


def test_off_mode_issues_no_statement_on_an_ordinary_connection(make_engine, mode):
    mode("off")
    engine = make_engine()
    with ti.bind_tenant(7), engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    assert not _recorded("set_config")
    assert not _recorded("current_setting")


# --------------------------------------------------------------------------- #
# foreign engines, other dialects, pools
# --------------------------------------------------------------------------- #


def test_a_foreign_database_engine_gets_no_hook_statement_and_no_scrub(make_engine, mode):
    mode("enforce")
    engine = make_engine().execution_options(archie_foreign_database=True)
    with ti.bind_tenant(7), engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    assert not _recorded("set_config")


def test_a_sqlite_engine_gets_no_hook_statement_and_no_scrub(mode):
    mode("enforce")
    ti._install_engine_hooks()
    engine = create_engine("sqlite://")
    seen = []
    event.listen(engine, "before_cursor_execute", lambda c, cur, s, *a: seen.append(s))
    with ti.bind_tenant(7), engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    assert seen == ["SELECT 1"]


def test_the_scrub_runs_on_a_reused_pool_and_is_skipped_for_nullpool(make_engine, mode):
    mode("enforce")
    pooled = make_engine()
    with pooled.connect() as connection:
        connection.execute(text("SELECT 1"))
    assert _recorded("set_config('archie.organization_id', '', false)")
    del STATEMENTS[:]
    unpooled = make_engine(pool="null")
    with unpooled.connect() as connection:
        connection.execute(text("SELECT 1"))
    assert not _recorded("set_config('archie.organization_id', '', false)")


def test_a_session_level_value_is_scrubbed_at_checkin(make_engine, mode):
    mode("enforce")
    engine = make_engine()
    raw = engine.raw_connection()
    cursor = raw.cursor()
    cursor.execute("SELECT set_config('archie.organization_id', '5', false)")
    cursor.close()
    raw.close()
    with engine.connect() as connection:
        assert tuple(_setting(connection)) == ("", "")


# --------------------------------------------------------------------------- #
# the shim
# --------------------------------------------------------------------------- #


def test_shim_in_enforce_refuses_a_value_that_differs_from_the_context(make_engine, mode):
    mode("enforce")
    engine = make_engine()
    with ti.bind_tenant(7), engine.connect() as connection:
        ti.set_database_tenant_context(connection, 7)
        assert _setting(connection)[0] == "7"
        with pytest.raises(ti.TenantBindingConflict):
            ti.set_database_tenant_context(connection, 8)


def test_shim_in_log_writes_as_before_and_logs_a_conflict(make_engine, mode, caplog):
    mode("log")
    caplog.set_level(logging.INFO)
    engine = make_engine()
    with ti.bind_tenant(7), engine.connect() as connection:
        ti.set_database_tenant_context(connection, 8)
        assert _setting(connection)[0] == "8"
    lines = [r.getMessage() for r in caplog.records if r.name == "app.tenant_context_guard"]
    assert any("class=conflict" in line for line in lines), lines


# --------------------------------------------------------------------------- #
# the self-check and recursion
# --------------------------------------------------------------------------- #


def test_selfcheck_fails_a_checkout_that_carries_a_value(app, make_engine, mode, monkeypatch):
    mode("enforce")
    monkeypatch.setitem(app.config, "ARCHIE_TENANT_GUC_MODE", "enforce")
    monkeypatch.setitem(app.config, "ARCHIE_TENANT_GUC_SELFCHECK", True)
    monkeypatch.setattr(ti, "_scrub_connection", lambda dbapi_connection: None)
    engine = make_engine()
    with app.app_context():
        raw = engine.raw_connection()
        cursor = raw.cursor()
        cursor.execute("SELECT set_config('archie.organization_id', '5', false)")
        cursor.close()
        raw.close()
        with pytest.raises(AssertionError):
            engine.connect()


def test_a_disagreement_logged_with_the_error_aggregation_handler_attached(
    app, mode, monkeypatch
):
    """The handler opens its own connection from inside the log call. The hook's
    guard stops it re-entering, and one line is one more occurrence in error_events
    (the handler folds repeats of a line into one row's counter)."""
    import uuid

    from sqlalchemy import func

    from app._bootstrap.error_tracking import _ErrorAggregationHandler
    from app.extensions import db
    from app.models.error_event import ErrorEvent

    mode("log")
    monkeypatch.setitem(app.config, "ARCHIE_TENANT_GUC_MODE", "log")
    ti._install_engine_hooks()
    ti._reset_disagreement_log()
    organization = 100000 + uuid.uuid4().int % 900000  # a line no earlier run produced
    root = logging.getLogger()
    installed = app.extensions.get("error_aggregation_handler")
    handler = installed or _ErrorAggregationHandler(app)
    # Every application this process created attached its own handler to the root
    # logger; keep only this application's, so one line is one increment.
    others = [
        h for h in root.handlers
        if isinstance(h, _ErrorAggregationHandler) and h is not handler
    ]
    for other in others:
        root.removeHandler(other)
    if installed is None:
        root.addHandler(handler)

    def occurrences():
        return db.session.query(
            func.coalesce(func.sum(ErrorEvent.occurrence_count), 0)
        ).filter(ErrorEvent.message.like(f"tenant context disagreement%desired_org={organization} %")).scalar()

    try:
        with app.app_context():
            assert occurrences() == 0
            db.session.rollback()
            with ti.bind_tenant(organization), db.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            db.session.remove()
            assert occurrences() == 1
            db.session.remove()
    finally:
        if installed is None:
            root.removeHandler(handler)
        for other in others:
            root.addHandler(other)
