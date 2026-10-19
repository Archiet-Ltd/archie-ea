"""The Composer page's focus-mode parameters: ?element=<id>&depth=<1-5>&derived=<0|1>.

``element`` opens that element's impact neighbourhood as an unsaved view. The page only
passes the parameters on (as ``focusElement``, ``focusName``, ``focusDepth`` and
``focusDerived`` in the page config); the neighbourhood itself is fetched by the browser from
the one impact endpoint. What the route owns is the tenant fence (a missing id and another
organisation's id are the same 404), the depth clamp, the derived default, and precedence:
a saved diagram's ``viewpoint_id`` wins over ``element``.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest

BASE_KEYS = {"solutionId", "solutionName", "startBlank", "initialViewpoint", "initialLayer"}


def _user(db_session, org):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"focus-{uuid.uuid4().hex[:6]}@example.test", first_name="Focus", last_name="Tester",
        organization_id=org.id, confirmed=True, role=role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org, name):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(
        name=name, type="ApplicationComponent", layer="application", organization_id=org.id
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def world(db_session, make_org, login_as, client):
    org_a = make_org("focus-a")
    org_b = make_org("focus-b")
    user = _user(db_session, org_a)
    own = _element(db_session, org_a, "Billing Service")
    foreign = _element(db_session, org_b, "Other Tenant Service")
    db_session.commit()

    def get(query):
        login_as(client, user)
        return client.get("/archimate/composer" + query)

    return {"get": get, "own": own.id, "foreign": foreign.id, "missing": 987654321}


def _config(response):
    text = response.get_data(as_text=True)
    block = re.search(r"window\.__COMPOSER_CONFIG__ = \{(.*?)\n\};", text, re.S)
    assert block, "page config not found"
    keys = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
    values = {}
    for key in keys:
        found = re.search(r"^\s*%s:\s*(.*?),\s*$" % key, block.group(1), re.M)
        values[key] = json.loads(found.group(1)) if found and key.startswith("focus") else None
    return keys, values


def test_own_element_renders_with_focus_config(world):
    response = world["get"]("?element=%s" % world["own"])
    assert response.status_code == 200
    keys, values = _config(response)
    assert {"focusElement", "focusName", "focusDepth", "focusDerived"} <= keys
    assert values["focusElement"] == world["own"]
    assert values["focusName"] == "Billing Service"
    assert values["focusDepth"] == 3
    assert values["focusDerived"] is True


def test_other_organisation_and_missing_ids_are_the_same_404(world):
    foreign = world["get"]("?element=%s" % world["foreign"])
    missing = world["get"]("?element=%s" % world["missing"])
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.get_data() == missing.get_data()


@pytest.mark.parametrize(
    "raw,expected",
    [("0", 1), ("-2", 1), ("6", 5), ("99", 5), ("abc", 3), ("1", 1), ("5", 5)],
)
def test_depth_is_clamped_to_one_to_five(world, raw, expected):
    response = world["get"]("?element=%s&depth=%s" % (world["own"], raw))
    assert response.status_code == 200
    assert _config(response)[1]["focusDepth"] == expected


@pytest.mark.parametrize("raw,expected", [("0", False), ("1", True), ("true", True), ("no", False)])
def test_derived_flag(world, raw, expected):
    response = world["get"]("?element=%s&derived=%s" % (world["own"], raw))
    assert _config(response)[1]["focusDerived"] is expected


def test_derived_defaults_on(world):
    response = world["get"]("?element=%s" % world["own"])
    assert _config(response)[1]["focusDerived"] is True


def test_viewpoint_id_wins_over_element(world):
    response = world["get"]("?viewpoint_id=1&element=%s" % world["own"])
    assert response.status_code == 200
    keys, _ = _config(response)
    assert not any(k.startswith("focus") for k in keys)


def test_without_element_the_config_keys_are_unchanged(world):
    response = world["get"]("")
    assert response.status_code == 200
    keys, _ = _config(response)
    assert keys == BASE_KEYS
