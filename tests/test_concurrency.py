"""Two people editing the same record must not silently overwrite each other.

OptimisticLockMixin declares version_id_col, so SQLAlchemy should append
`WHERE version = <read value>` to every UPDATE and raise StaleDataError when that
matches no row. Whether it actually does is not something you can tell by reading
the model - the mixin is declarative, and a mapper misconfiguration produces a
class that looks correct and silently last-write-wins.

That failure mode is quiet and expensive. Two architects open the same
application, both save, and the second overwrites the first with no error shown
to anyone. Nothing appears in the log, and the data is simply wrong afterwards.

These tests use two independent sessions against one row - the same shape as two
browser tabs - and assert the second write is refused.
"""

import uuid

import pytest

pytestmark = pytest.mark.journey


@pytest.fixture(scope="module")
def app():
    import os

    os.environ.setdefault("SECRET_KEY", "x" * 32)
    from app import create_app, db

    application = create_app("testing")
    with application.app_context():
        db.create_all()

    # A route that loses the race on demand. Provoking a genuine conflict over
    # HTTP would need two interleaved requests against one row; what is under
    # test here is the app's response to StaleDataError, not the ORM's ability
    # to raise it - that is covered above. Registered before any request is
    # served, because Flask refuses to add routes afterwards.
    from sqlalchemy.orm.exc import StaleDataError

    def _lose_the_race():
        raise StaleDataError("simulated concurrent update")

    application.add_url_rule("/__test__/conflict", "test_conflict", _lose_the_race)
    return application


def _lock_attr(cls):
    """The attribute SQLAlchemy actually increments, whatever it is called.

    ApplicationComponent cannot use the mixin's `version`: that name is already
    taken by the application's own release string ("2.1.0"), which a user edits.
    Asking the mapper avoids hard-coding either name and keeps the test honest
    if a model renames its lock column later.
    """
    from sqlalchemy import inspect as sa_inspect

    col = sa_inspect(cls).version_id_col
    assert col is not None, (
        "%s maps without version_id_col, so concurrent writes are "
        "last-write-wins" % cls.__name__)
    return col.name


@pytest.fixture
def component(app):
    """A row to fight over, plus its organisation.

    Real, fully committed rows rather than db_session/make_org: every test
    below opens independent raw sessions bound directly to db.engine (two
    real connections, the same shape as two browser tabs) to prove
    optimistic locking holds across genuinely separate transactions.
    db_session captures one connection and keeps everything inside an
    uncommitted SAVEPOINT on it; a row created that way would be invisible
    to a second, independent connection — exactly the scenario these tests
    need to be real. Deletes exactly what it created, by id, instead.
    """
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        org = Organization(name="Lock %s" % marker, slug="lock-%s" % marker)
        db.session.add(org)
        db.session.commit()
        row = ApplicationComponent(name="Contended %s" % marker, organization_id=org.id)
        db.session.add(row)
        db.session.commit()
        row_id, org_id = row.id, org.id

    yield {"id": row_id, "org_id": org_id, "marker": marker}

    with app.app_context():
        from app.models.audit_log import AuditLog

        # ApplicationComponent is an audited ("controlled") model: its insert
        # above, and every raw-session UPDATE the tests in this file make to
        # it, each write a soc2_audit_log row keyed on this record_id. Clear
        # those first — soc2_audit_log is append-only by design (no FK from
        # it blocks the component delete below, but leaving these rows is
        # its own leak).
        db.session.query(AuditLog).filter_by(
            table_name="application_component", record_id=row_id
        ).delete(synchronize_session=False)
        db.session.query(ApplicationComponent).filter_by(id=row_id).delete()
        db.session.query(Organization).filter_by(id=org_id).delete()
        db.session.commit()


def test_the_version_column_exists_and_starts_populated(app, component):
    """A NULL version silently disables the whole mechanism."""
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        attr = _lock_attr(ApplicationComponent)
        row = ApplicationComponent.query.get(component["id"])
        assert getattr(row, attr, None) is not None, (
            "%s is NULL - SQLAlchemy compares it with `= NULL`, which matches no "
            "row, so every save on this application would be refused" % attr)


def test_a_concurrent_update_is_refused(app, component):
    """The core guarantee, exercised with two real sessions."""
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.orm.exc import StaleDataError

    from app import db
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        Session = sessionmaker(bind=db.engine)
        first, second = Session(), Session()
        try:
            # Both read the row at the same version - two tabs, one record.
            attr = _lock_attr(ApplicationComponent)
            a = first.get(ApplicationComponent, component["id"])
            b = second.get(ApplicationComponent, component["id"])
            assert getattr(a, attr) == getattr(b, attr), (
                "the two sessions did not start level")

            a.description = "written by the first editor"
            first.commit()

            b.description = "written by the second editor"
            with pytest.raises(StaleDataError):
                second.commit()
        finally:
            first.close()
            second.close()


def test_the_first_writer_survives(app, component):
    """A refused write must not be a partially applied one."""
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.orm.exc import StaleDataError

    from app import db
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        Session = sessionmaker(bind=db.engine)
        first, second = Session(), Session()
        try:
            a = first.get(ApplicationComponent, component["id"])
            b = second.get(ApplicationComponent, component["id"])
            a.description = "kept"
            first.commit()
            b.description = "lost"
            try:
                second.commit()
            except StaleDataError:
                second.rollback()
        finally:
            first.close()
            second.close()

        db.session.expire_all()
        row = ApplicationComponent.query.get(component["id"])
        assert row.description == "kept", (
            "the second writer overwrote the first - optimistic locking is not "
            "protecting this row")


def test_the_version_advances_on_write(app, component):
    """If version never moves, the guard is inert even when configured."""
    from sqlalchemy.orm import sessionmaker

    from app import db
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        Session = sessionmaker(bind=db.engine)
        attr = _lock_attr(ApplicationComponent)
        session = Session()
        try:
            row = session.get(ApplicationComponent, component["id"])
            before = getattr(row, attr)
            row.description = "bumped"
            session.commit()
            after = getattr(row, attr)
        finally:
            session.close()

    assert after > before, (
        "version did not advance on UPDATE (%s -> %s), so a later writer reading "
        "the old value would be accepted" % (before, after))


def test_a_conflict_is_explained_rather_than_crashing(app):
    """A refused write is only an improvement if the person can tell what happened.

    Without a handler, StaleDataError is a 500 and an unexplained error page.
    The user cannot distinguish "your colleague saved first" from "this product
    is broken", and their edits are gone either way. 409 plus a plain-English
    message is the difference between a safeguard and a fault.
    """
    client = app.test_client()
    response = client.get("/__test__/conflict")

    assert response.status_code == 409, (
        "expected 409 Conflict, got %s - the request was well-formed and "
        "permitted, it lost a race" % response.status_code)
    body = response.get_data(as_text=True).lower()
    assert "someone else" in body, (
        "the page does not say another user saved first, so the reader has no "
        "way to understand why their work was rejected")
    assert "reload" in body, "the page does not say how to recover"


def test_a_conflict_on_a_json_request_stays_json(app):
    """An API caller must get JSON, not an HTML error page it cannot parse.

    A front end that receives HTML where it expects JSON fails at the parse
    step, so the user sees a generic script error instead of the conflict
    message - the handler's whole purpose, lost at the last hop.
    """
    client = app.test_client()
    response = client.get("/__test__/conflict",
                          headers={"Accept": "application/json"})

    assert response.status_code == 409
    assert response.is_json, (
        "conflict returned %s to a JSON caller, which the front end cannot "
        "parse" % response.content_type)
    payload = response.get_json()
    assert payload.get("conflict") is True, (
        "no machine-readable conflict flag, so the front end cannot tell this "
        "apart from any other failure: %r" % payload)
    assert "someone else" in (payload.get("error") or "").lower()


def test_every_optimistically_locked_model_is_wired_up(app):
    """Inheriting the mixin is not the same as being configured by it.

    A model can carry OptimisticLockMixin and still map without version_id_col if
    __mapper_args__ is overridden downstream - which looks entirely correct in the
    class definition.
    """
    from sqlalchemy import inspect as sa_inspect

    from app.models.mixins import OptimisticLockMixin

    unwired = []
    with app.app_context():
        from app import db

        for mapper in db.Model.registry.mappers:
            cls = mapper.class_
            if not issubclass(cls, OptimisticLockMixin):
                continue
            if sa_inspect(cls).version_id_col is None:
                unwired.append(cls.__name__)

    assert not unwired, (
        "%d model(s) inherit OptimisticLockMixin but map without version_id_col, "
        "so concurrent writes are silently last-write-wins: %s"
        % (len(unwired), sorted(unwired)))
