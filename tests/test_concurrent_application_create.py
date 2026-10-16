"""Concurrent application writes must not inherit the single-connection fixture."""
import re
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from queue import Queue

import pytest
from sqlalchemy.engine import make_url


@pytest.fixture(scope='session')
def concurrency_database_guard():
    value = os.environ.get('TEST_DATABASE_URL')
    if not value:
        raise pytest.UsageError('Concurrency qualification requires an explicit disposable TEST_DATABASE_URL')
    url = make_url(value)
    if url.get_backend_name() != 'postgresql' or not url.database:
        raise pytest.UsageError('Concurrency qualification requires a named PostgreSQL test database')
    return url


@pytest.fixture
def independent_application_race(concurrency_database_guard, app, _schema):
    """Exceptional committed fixture: independent transactions are the subject.

    No db_session/make_org dependency and no session-factory substitution.
    Every committed row belongs to an exact unique organization/name pair.
    """
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex
    state = dict(name=f'Concurrent App {suffix}', slug=f'raceorg-{suffix}', threads=[])
    with app.app_context():
        assert app.testing
        assert make_url(app.config['SQLALCHEMY_DATABASE_URI']) == concurrency_database_guard
        assert db.engine.url == concurrency_database_guard
        assert db.session.get_bind() is db.engine, 'Concurrent workers must not inherit a pinned Connection'
        organization = Organization(name=f'RaceOrg {suffix}', slug=state['slug'])
        db.session.add(organization)
        db.session.commit()
        state['org_id'] = organization.id
        db.session.remove()
    try:
        yield state
    finally:
        alive = [thread.name for thread in state['threads'] if thread.is_alive()]
        assert not alive, f'Refusing row cleanup while workers remain alive: {alive}'
        with app.app_context():
            db.session.remove()
            org_id, name = state['org_id'], state['name']
            applications = ApplicationComponent.query.filter_by(organization_id=org_id, name=name).all()
            app_ids = [row.id for row in applications]
            # The ORM before_insert creates mirrors even if an application
            # flush later fails; the committed exact-org/name scan covers both.
            mirrors = ArchiMateElement.query.filter_by(
                organization_id=org_id, name=name, type='ApplicationComponent').all()
            mirror_ids = [row.id for row in mirrors]
            ApplicationComponent.query.filter(
                ApplicationComponent.organization_id == org_id,
                ApplicationComponent.id.in_(app_ids)).delete(synchronize_session=False)
            ArchiMateElement.query.filter(
                ArchiMateElement.organization_id == org_id,
                ArchiMateElement.id.in_(mirror_ids)).delete(synchronize_session=False)
            # Application writes emit SOC2 audit rows keyed to the organisation;
            # they must go before the organisation or its delete violates the FK.
            from app.models.audit_log import AuditLog
            AuditLog.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
            from tests._outbox_cleanup import purge_outbox_events
            purge_outbox_events(db.session, [org_id])
            assert Organization.query.filter_by(id=org_id, slug=state['slug']).delete() == 1
            db.session.commit()
            assert ApplicationComponent.query.filter_by(organization_id=org_id).count() == 0
            assert ArchiMateElement.query.filter_by(organization_id=org_id).count() == 0
            assert Organization.query.filter_by(id=org_id).count() == 0
            db.session.remove()


def test_concurrent_identical_creates_produce_one_row(app, independent_application_race):
    """Five independent transactions exercise the real production duplicate guard."""
    from flask import g
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.utils.duplicate_guard import find_duplicate_by_name, lock_name_for_write

    fixture = independent_application_race
    workers, barrier = 5, threading.Barrier(5)
    outcomes, errors = Queue(), Queue()
    connections, connection_lock = {}, threading.Lock()

    def create(index):
        try:
            with app.test_request_context('/'):
                g.current_org_id = fixture['org_id']
                try:
                    connection = db.session.connection()
                    connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
                    connection.exec_driver_sql("SET LOCAL statement_timeout = '15s'")
                    backend_id = connection.exec_driver_sql('SELECT pg_backend_pid()').scalar_one()
                    with connection_lock:
                        connections[index] = (backend_id, connection.connection.driver_connection)
                    barrier.wait(timeout=10)
                    assert lock_name_for_write(ApplicationComponent, fixture['name']) is True
                    existing = find_duplicate_by_name(ApplicationComponent, fixture['name'])
                    # Keep the original critical-window amplification.
                    time.sleep(0.25)
                    created = existing is None
                    if created:
                        existing = ApplicationComponent(name=fixture['name'])
                        db.session.add(existing)
                        db.session.flush()
                    row_id = existing.id
                    db.session.commit()
                    outcomes.put((created, row_id))
                except BaseException:
                    db.session.rollback()
                    raise
                finally:
                    db.session.remove()
        except BaseException:
            errors.put(traceback.format_exc())
            barrier.abort()

    threads = [threading.Thread(target=create, args=(index,), name=f'application-race-{index}', daemon=True)
               for index in range(workers)]
    fixture['threads'] = threads
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 35
    for thread in threads:
        thread.join(timeout=max(0, deadline - time.monotonic()))
    if any(thread.is_alive() for thread in threads):
        barrier.abort()
        # Cancel only this fixture's recorded native connections, never a PID
        # discovered elsewhere or a shared connection. Deadlines remain bounded.
        with connection_lock:
            for index, (_, connection) in connections.items():
                if threads[index].is_alive():
                    try:
                        connection.cancel()
                    except Exception:
                        errors.put(traceback.format_exc())
        deadline = time.monotonic() + 5
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
    assert all(not thread.is_alive() for thread in threads), 'Concurrent workers did not stop before cleanup'
    failures = []
    while not errors.empty():
        failures.append(errors.get_nowait())
    assert not failures, '\n'.join(failures)
    assert len(connections) == workers
    assert len({backend_id for backend_id, _ in connections.values()}) == workers
    results = []
    while not outcomes.empty():
        results.append(outcomes.get_nowait())
    assert len(results) == workers
    assert sum(created for created, _ in results) == 1
    assert len({row_id for _, row_id in results}) == 1
    with app.app_context():
        db.session.remove()
        rows = ApplicationComponent.query.filter_by(
            organization_id=fixture['org_id'], name=fixture['name']).all()
        assert len(rows) == 1, f'{workers} concurrent identical creates made {len(rows)} rows'
        assert rows[0].id == results[0][1]
        db.session.remove()


def test_concurrency_fixture_plan_has_no_shared_rollback_connection():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-m', 'pytest',
         'tests/test_concurrent_application_create.py::test_concurrent_identical_creates_produce_one_row',
         '--setup-plan', '-q'], cwd=root, text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'test_concurrent_identical_creates_produce_one_row' in result.stdout
    assert re.search(r'SETUP\s+\w+\s+independent_application_race\b', result.stdout), result.stdout
    assert not re.search(r'SETUP\s+\w+\s+(db_session|make_org)\b', result.stdout), result.stdout
