"""The compliance dashboard shows which frameworks the organisation has adopted and lets an authorised person adopt one."""

from __future__ import annotations

import uuid

import pytest

from tests.test_framework_catalogue import _seed_control, _seed_framework
from tests.test_gap_work_package_link import _user

pytestmark = pytest.mark.usefixtures("db_session")

DASHBOARD = "/dashboard/compliance"


def _framework(db_session, controls=2):
    code = "FW-%s" % uuid.uuid4().hex[:6].upper()
    fw = _seed_framework(db_session, code, "Framework %s" % code)
    for i in range(controls):
        _seed_control(db_session, fw.id, "%s.%d" % (code, i + 1), "Control %d" % (i + 1))
    return fw


def _cell(client, login_as, user, fw):
    login_as(client, user)
    html = client.get(DASHBOARD).get_data(as_text=True)
    marker = 'data-testid="framework-adoption-%d"' % fw.id
    assert marker in html, "the framework row is not on the dashboard"
    return html.split(marker, 1)[1].split("</td>", 1)[0]


def _adopt(client, login_as, user, fw):
    login_as(client, user)
    return client.post("/dashboard/api/compliance/frameworks/%d/adopt" % fw.id)


def test_adoption_state_is_per_organisation(db_session, make_org, client, login_as):
    a, b = make_org("adopt-a"), make_org("adopt-b")
    editor_a, editor_b = _user(db_session, a, "adopta"), _user(db_session, b, "adoptb")
    adopted, other = _framework(db_session), _framework(db_session)
    assert _adopt(client, login_as, editor_a, adopted).status_code == 200

    assert "Adopted" in _cell(client, login_as, editor_a, adopted)
    assert "Not adopted" in _cell(client, login_as, editor_a, other)
    assert "Not adopted" in _cell(client, login_as, editor_b, adopted)
    assert "Not adopted" in _cell(client, login_as, editor_b, other)


def test_only_an_authorised_role_is_offered_the_adopt_action(db_session, make_org, client, login_as):
    org = make_org("adopt-roles")
    editor = _user(db_session, org, "editor")
    viewer = _user(db_session, org, "viewer", role_name="Viewer")
    fw = _framework(db_session)

    assert "Adopt" in _cell(client, login_as, editor, fw).replace("Not adopted", "")
    assert "Adopt" not in _cell(client, login_as, viewer, fw).replace("Not adopted", "")


def test_adopting_shows_the_framework_adopted_with_its_controls_counted(db_session, make_org, client, login_as):
    org = make_org("adopt-count")
    editor = _user(db_session, org, "counter")
    fw = _framework(db_session, controls=3)

    assert _adopt(client, login_as, editor, fw).status_code == 200
    cell = _cell(client, login_as, editor, fw)
    assert "Adopted" in cell and "3 controls" in cell


def test_adopting_twice_is_refused_plainly_and_creates_no_second_adoption(db_session, make_org, client, login_as):
    from app.models.regulatory_framework import FrameworkAdoption

    org = make_org("adopt-twice")
    editor = _user(db_session, org, "twice")
    fw = _framework(db_session)

    assert _adopt(client, login_as, editor, fw).status_code == 200
    again = _adopt(client, login_as, editor, fw)
    assert again.status_code == 409 and again.get_json().get("error")
    assert FrameworkAdoption.query.filter_by(organization_id=org.id, framework_id=fw.id).count() == 1


def test_empty_catalogue_and_none_adopted_states(db_session, make_org, client, login_as):
    from app.models.compliance_models import RegulatoryFramework

    org = make_org("adopt-empty")
    editor = _user(db_session, org, "empty")
    RegulatoryFramework.query.filter_by(status="active").update({"status": "inactive"})
    db_session.flush()

    login_as(client, editor)
    html = client.get(DASHBOARD).get_data(as_text=True)
    assert "No frameworks are available to adopt yet" in html

    _framework(db_session)
    login_as(client, editor)
    html = client.get(DASHBOARD).get_data(as_text=True)
    assert 'data-testid="none-adopted"' in html and "None adopted yet" in html


def test_a_read_only_viewer_cannot_adopt(db_session, make_org, client, login_as):
    from app.models.regulatory_framework import FrameworkAdoption

    org = make_org("adopt-viewer")
    viewer = _user(db_session, org, "viewer", role_name="Viewer")
    fw = _framework(db_session)

    assert _adopt(client, login_as, viewer, fw).status_code == 403
    assert FrameworkAdoption.query.filter_by(organization_id=org.id, framework_id=fw.id).count() == 0
