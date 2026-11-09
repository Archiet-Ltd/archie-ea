"""A simulation works on the caller's own model only.

``POST /strategic/api/simulations`` answers for an element of the caller's organisation. An element that
belongs to another organisation, or does not exist, is an error (404) and carries no name, count or figure
from anyone's model.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

URL = "/strategic/api/simulations"


def _user(db_session, org):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    user = User(
        email=f"sim-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Sim",
        last_name="Tester",
        confirmed=True,
        organization_id=org.id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org, name):
    from app.models import ArchiMateElement

    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    return element


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a, org_b = make_org("sim-a"), make_org("sim-b")
    mine = _element(db_session, org_a, "Ledger of A %s" % uuid.uuid4().hex[:6])
    theirs = _element(db_session, org_b, "Secret of B %s" % uuid.uuid4().hex[:6])
    return _user(db_session, org_a), mine, theirs


def _post(client, login_as, user, element_id, change="RETIRE"):
    login_as(client, user)
    return client.post(URL, json={"element_id": element_id, "change_type": change})


def test_an_element_of_another_organisation_is_an_error_without_its_name(client, login_as, two_orgs):
    user, mine, theirs = two_orgs
    response = _post(client, login_as, user, theirs.id)
    assert response.status_code == 404, response.get_data(as_text=True)[:300]
    body = response.get_data(as_text=True)
    assert theirs.name not in body
    assert "Elements in the model" not in body


def test_an_element_that_does_not_exist_is_an_error(client, login_as, two_orgs):
    user, _, _ = two_orgs
    response = _post(client, login_as, user, 999999999)
    assert response.status_code == 404
    assert "Elements in the model" not in response.get_data(as_text=True)


def test_the_callers_own_element_still_simulates(client, login_as, two_orgs):
    user, mine, _ = two_orgs
    response = _post(client, login_as, user, mine.id)
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    assert response.get_json()["simulation"]["figures"]


def _risk(db_session, org_id, element, status, title):
    from app.models.risk import Risk

    risk = Risk(title=title, likelihood=3, impact=3, status=status, organization_id=org_id,
                archimate_element_id=element.id)
    db_session.add(risk)
    db_session.flush()
    return risk


def test_only_open_risks_are_counted_and_listed(client, login_as, two_orgs, db_session):
    from app.models.risk import RiskStatus

    user, mine, _ = two_orgs
    org_id = user.organization_id
    tag = uuid.uuid4().hex[:6]
    open_risk = _risk(db_session, org_id, mine, RiskStatus.OPEN, "Open %s" % tag)
    _risk(db_session, org_id, mine, RiskStatus.MITIGATED, "Mitigated %s" % tag)
    _risk(db_session, org_id, mine, RiskStatus.ACCEPTED, "Accepted %s" % tag)
    _risk(db_session, org_id, mine, RiskStatus.CLOSED, "Closed %s" % tag)
    response = _post(client, login_as, user, mine.id, "MODIFY")
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    simulation = response.get_json()["simulation"]
    assert [r["title"] for r in simulation["risks"]] == ["Open %s" % tag]
    assert simulation["real_model"]["open_risks"] == 1
    row = next(f for f in simulation["figures"] if f["label"] == "Open risks touching the change")
    assert row["before"] == 1 and open_risk.id == simulation["risks"][0]["id"]


def test_a_foreign_element_with_a_risk_leaks_nothing(client, login_as, two_orgs, db_session, make_org):
    from app.models.risk import RiskStatus

    user, _, theirs = two_orgs
    _risk(db_session, theirs.organization_id, theirs, RiskStatus.OPEN, "Foreign risk")
    response = _post(client, login_as, user, theirs.id)
    assert response.status_code == 404
    assert "Foreign risk" not in response.get_data(as_text=True)


def test_the_change_choices_on_both_pages_come_from_the_service(client, login_as, two_orgs):
    from unittest import mock

    from app.modules.solutions_strategic.v2.services import simulation_service

    user, _, _ = two_orgs
    login_as(client, user)
    renamed = {code: "Words for %s" % code for code in simulation_service.CHANGE_LABELS}
    with mock.patch.object(simulation_service, "CHANGE_LABELS", renamed):
        for url in ("/strategic/impact-analysis", "/simulations"):
            html = client.get(url).get_data(as_text=True)
            for code, label in renamed.items():
                assert '<option value="%s">%s</option>' % (code, label) in html, url
                assert label in html.split("const CHANGE_LABELS")[1].split(";")[0], url


def test_a_simulation_without_an_organisation_is_refused(app):
    from unittest import mock

    from app.modules.solutions_strategic.v2.services import simulation_service

    with mock.patch.object(simulation_service, "current_org_id", return_value=None):
        with pytest.raises(simulation_service.ElementNotInModel):
            simulation_service.simulate_change(1, "RETIRE")
