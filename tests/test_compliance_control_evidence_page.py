"""The evidence for a control opens inside the app, at that control, and only for the organisation that owns it.

Two real organisations: a control's evidence page answers not found for the other organisation's mapping.
"""

from __future__ import annotations

import re

import pytest

from tests.test_compliance_one_answer import _world, _visible

pytestmark = pytest.mark.usefixtures("db_session")


def _mapping(app_id, control_id):
    from app.models.application_compliance import ApplicationComplianceControl

    return ApplicationComplianceControl.query.filter_by(application_id=app_id, control_id=control_id).one()


def _record_evidence(db_session, row, text):
    from flask import url_for

    row.notes = text
    row.evidence_url = url_for("application_mgmt.compliance_control_evidence", mapping_id=row.id)
    db_session.flush()


def test_the_evidence_page_names_the_control_shows_the_evidence_and_leads_back_to_the_register(
        db_session, make_org, client, login_as):
    org, user, _cap, _bare, controls, app_a = _world(db_session, make_org, "evid")
    in_progress = controls[1]
    row = _mapping(app_a.id, in_progress.id)
    with client.application.test_request_context():
        _record_evidence(db_session, row, "The key rotation plan for the ledger is under way.")
    login_as(client, user)

    register = client.get("/dashboard/compliance").get_data(as_text=True)
    link = re.search(r'href="([^"]*)"[^>]*>\s*Evidence document', register)
    assert link, "the register offers no Evidence document link"
    assert link.group(1) == "/dashboard/compliance/controls/%d/evidence" % row.id
    assert 'id="control-%d"' % row.id in register, "the register has no row to come back to"

    login_as(client, user)
    page = client.get(link.group(1))
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    text = _visible(html)
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S).group(1)
    assert in_progress.control_code in heading and in_progress.title in heading
    assert "The key rotation plan for the ledger is under way." in text
    assert app_a.name in text and "in progress" in text
    back = re.search(r'href="([^"]*)"[^>]*>\s*(?:<[^>]+>\s*)*Back to the register', html)
    assert back and back.group(1) == "/dashboard/compliance#control-%d" % row.id


def test_a_control_with_no_evidence_text_says_so_rather_than_leaving_a_gap(db_session, make_org, client, login_as):
    org, user, _cap, _bare, controls, app_a = _world(db_session, make_org, "bare")
    row = _mapping(app_a.id, controls[2].id)
    login_as(client, user)
    text = _visible(client.get("/dashboard/compliance/controls/%d/evidence" % row.id).get_data(as_text=True))
    assert "No evidence text is recorded for this control yet." in text


def test_another_organisations_evidence_page_is_not_found(db_session, make_org, client, login_as):
    org_a, user_a, _c1, _b1, controls_a, app_a = _world(db_session, make_org, "alpha")
    org_b, user_b, _c2, _b2, controls_b, app_b = _world(db_session, make_org, "beta")
    row_a = _mapping(app_a.id, controls_a[1].id)
    row_b = _mapping(app_b.id, controls_b[1].id)
    with client.application.test_request_context():
        _record_evidence(db_session, row_a, "Alpha holds this.")
        _record_evidence(db_session, row_b, "Beta holds this.")

    login_as(client, user_a)
    own = client.get("/dashboard/compliance/controls/%d/evidence" % row_a.id)
    assert own.status_code == 200 and "Alpha holds this." in own.get_data(as_text=True)
    login_as(client, user_a)
    assert client.get("/dashboard/compliance/controls/%d/evidence" % row_b.id).status_code == 404

    login_as(client, user_b)
    assert client.get("/dashboard/compliance/controls/%d/evidence" % row_a.id).status_code == 404
    login_as(client, user_b)
    assert "Beta holds this." in client.get("/dashboard/compliance/controls/%d/evidence" % row_b.id).get_data(as_text=True)
