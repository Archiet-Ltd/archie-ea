"""The write-access backstop names every request it would have refused, and refuses nothing in report mode.

A route that writes without asking any check is the one the route-level checks cannot see. In
production the backstop logs one line for it (route, method, table); a write a check admitted, a
write to the system's own bookkeeping and a write by a member who may change the model log none.
Every case uses two real organisations.
"""
from __future__ import annotations

import logging

import pytest
from flask import g
from test_viewer_cannot_write_through_the_api import _application, _member, _org

REPORT = "write-access report"


@pytest.fixture
def two_orgs(db_session):
    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    db_session.commit()
    return org_a, org_b


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.write_access" and REPORT in r.getMessage()]


def _store_an_application(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    db_session.add(ApplicationComponent(name=name, organization_id=org.id))
    db_session.commit()


def test_a_route_with_no_check_that_writes_as_a_viewer_is_named_once(app, db_session, two_orgs, caplog):
    org_a, org_b = two_orgs
    viewer = _member(db_session, org_a, "viewer")
    caplog.set_level(logging.WARNING, logger="app.write_access")

    with app.test_request_context("/unchecked", method="GET"):
        g._login_user = viewer
        g.current_org_id = org_a.id
        _store_an_application(db_session, org_a, "Unchecked write")
        _store_an_application(db_session, org_a, "Second unchecked write")

    lines = _lines(caplog)
    assert len(lines) == 1, lines
    assert "table=application_components" in lines[0] and "method=GET" in lines[0] and "route=" in lines[0]
    db_session.rollback()


def test_a_change_a_read_rolled_back_stores_nothing_and_logs_no_line(app, db_session, two_orgs, caplog):
    from app.models.application_portfolio import ApplicationComponent

    org_a, _ = two_orgs
    viewer = _member(db_session, org_a, "viewer")
    caplog.set_level(logging.WARNING, logger="app.write_access")
    with app.test_request_context("/computed", method="GET"):
        g._login_user = viewer
        g.current_org_id = org_a.id
        db_session.add(ApplicationComponent(name="Computed, not kept", organization_id=org_a.id))
        db_session.flush()
        db_session.rollback()
        db_session.commit()
    assert _lines(caplog) == []
    assert ApplicationComponent.query.filter_by(name="Computed, not kept").count() == 0


def test_a_report_refuses_nothing_even_for_a_viewer(app, db_session, two_orgs, caplog):
    from app.models.application_portfolio import ApplicationComponent

    org_a, _ = two_orgs
    viewer = _member(db_session, org_a, "viewer")
    caplog.set_level(logging.WARNING, logger="app.write_access")
    with app.test_request_context("/unchecked", method="GET"):
        g._login_user = viewer
        g.current_org_id = org_a.id
        _store_an_application(db_session, org_a, "Reported but stored")
    assert len(_lines(caplog)) == 1
    assert ApplicationComponent.query.filter_by(name="Reported but stored", organization_id=org_a.id).count() == 1
    db_session.rollback()


def test_a_write_a_check_admitted_logs_no_line(app, db_session, two_orgs, caplog):
    from app._bootstrap.write_access import note_write_admitted
    from app.core.auth.permissions import note_route_granted_write

    org_a, _ = two_orgs
    viewer = _member(db_session, org_a, "viewer")
    caplog.set_level(logging.WARNING, logger="app.write_access")

    for admit in (note_write_admitted, note_route_granted_write):
        with app.test_request_context("/checked", method="POST"):
            g._login_user = viewer
            g.current_org_id = org_a.id
            admit()
            _store_an_application(db_session, org_a, "Admitted write")
    assert _lines(caplog) == []
    db_session.rollback()


def test_a_refusal_helper_that_admits_a_writer_is_a_check_that_admitted(app, db_session, two_orgs, caplog):
    from app._bootstrap.write_access import refuse_read_only

    org_a, _ = two_orgs
    editor = _member(db_session, org_a, "architect")
    caplog.set_level(logging.WARNING, logger="app.write_access")
    with app.test_request_context("/checked", method="POST"):
        g._login_user = editor
        g.current_org_id = org_a.id
        assert refuse_read_only(editor) is None
        assert g.write_admitted_by_check is True
        _store_an_application(db_session, org_a, "Editor write")
    assert _lines(caplog) == []
    db_session.rollback()


def test_a_member_who_may_write_and_the_systems_own_writes_log_no_line(app, db_session, two_orgs, caplog):
    org_a, _ = two_orgs
    editor = _member(db_session, org_a, "architect")
    viewer = _member(db_session, org_a, "viewer")
    caplog.set_level(logging.WARNING, logger="app.write_access")

    with app.test_request_context("/unchecked", method="GET"):
        g._login_user = editor
        g.current_org_id = org_a.id
        _store_an_application(db_session, org_a, "Editor, no check")

    with app.test_request_context("/page", method="GET"):
        g._login_user = viewer
        g.current_org_id = org_a.id
        viewer.last_name = "Stamped at sign-in"
        db_session.commit()
    assert _lines(caplog) == []
    db_session.rollback()


def test_a_persona_admitted_without_a_grant_is_refused_in_production_mode(app, db_session, two_orgs):
    from app._bootstrap.write_access import ChangeNotGrantedByRoute

    org_a, _ = two_orgs
    person = _member(db_session, org_a, "viewer")
    previous_testing = app.testing
    previous_flag = app.config.pop("WRITE_ACCESS_REPORT_ONLY", None)
    app.testing = False
    try:
        with app.test_request_context("/persona", method="POST"):
            g._login_user = person
            g.current_org_id = org_a.id
            g.write_admitted_for_route_check = True
            g.route_granted_write = False
            with pytest.raises(ChangeNotGrantedByRoute):
                _store_an_application(db_session, org_a, "Persona, production")
        db_session.rollback()
    finally:
        app.testing = previous_testing
        if previous_flag is not None:
            app.config["WRITE_ACCESS_REPORT_ONLY"] = previous_flag


def test_a_raw_statement_with_no_check_is_named_for_a_viewer_of_either_organisation(
    app, db_session, two_orgs, caplog
):
    from sqlalchemy import text

    org_a, org_b = two_orgs
    application = _application(db_session, org_a, "Ledger A")
    viewer = _member(db_session, org_b, "viewer")
    caplog.set_level(logging.WARNING, logger="app.write_access")
    with app.test_request_context("/raw", method="GET"):
        g._login_user = viewer
        g.current_org_id = org_b.id
        db_session.execute(
            text("UPDATE application_components SET name = 'Renamed' WHERE id = :id"), {"id": application.id}
        )
        db_session.commit()
    lines = _lines(caplog)
    assert len(lines) == 1 and "table=application_components" in lines[0], lines
    db_session.rollback()
