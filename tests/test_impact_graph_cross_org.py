"""One organisation cannot read another organisation's impact graph by guessing an element id.

Both endpoints answer 404 with no element names when the id belongs to another organisation,
and still answer 200 with the nodes of the signed-in organisation's own elements.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

GRAPH_URL = "/archimate/api/element/{}/impact-graph"
ANALYSIS_URL = "/archimate/api/impact-analysis/{}"


def _user(db_session, org):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    user = User(
        email=f"fence-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Fence",
        last_name="Tester",
        confirmed=True,
        organization_id=org.id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org, name, kind="ApplicationComponent", layer="Application"):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    return el


def _link(db_session, org, source, target):
    from app.models.archimate_core import ArchiMateRelationship

    db_session.add(ArchiMateRelationship(source_id=source.id, target_id=target.id,
                                         type="serving", organization_id=org.id))
    db_session.flush()


@pytest.fixture
def two_orgs(db_session, make_org):
    tag = uuid.uuid4().hex[:8]
    a, b = make_org("a"), make_org("b")
    a_one = _element(db_session, a, f"Alpha billing {tag}")
    a_two = _element(db_session, a, f"Alpha ledger {tag}", "Node", "Technology")
    _link(db_session, a, a_two, a_one)
    b_one = _element(db_session, b, f"Beta portal {tag}")
    b_two = _element(db_session, b, f"Beta database {tag}", "Node", "Technology")
    _link(db_session, b, b_two, b_one)
    return {"b_user": _user(db_session, b), "a": (a_one, a_two), "b": (b_one, b_two)}


@pytest.mark.parametrize("url", [GRAPH_URL, ANALYSIS_URL])
def test_another_organisations_element_answers_404_with_no_names(client, login_as, two_orgs, url):
    login_as(client, two_orgs["b_user"])
    for foreign in two_orgs["a"]:
        resp = client.get(url.format(foreign.id))
        assert resp.status_code == 404
        body = resp.get_data(as_text=True)
        for el in two_orgs["a"]:
            assert el.name not in body


def test_own_element_still_answers_with_its_own_nodes(client, login_as, two_orgs):
    own, other = two_orgs["b"]
    login_as(client, two_orgs["b_user"])
    resp = client.get(GRAPH_URL.format(own.id))
    assert resp.status_code == 200
    names = {n["name"] for n in resp.get_json()["nodes"]}
    assert names == {own.name, other.name}

    resp = client.get(ANALYSIS_URL.format(own.id))
    assert resp.status_code == 200
    assert resp.get_json()["element"]["name"] == own.name
