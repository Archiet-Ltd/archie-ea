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
