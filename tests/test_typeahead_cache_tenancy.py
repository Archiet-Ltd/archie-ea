"""The element typeahead and the capability picker are cached per organisation.

Both answers used to be held in a process-wide dictionary under a key that did not
name the organisation, so the second organisation to ask the same question was
handed the first organisation's element and capability names, ids, types and
layers. A cache hit runs no query, so row-level security could not help.
"""
from __future__ import annotations

import uuid

import pytest

URL = "/api/architecture-assistant/motivation-elements"
CAP_URL = "/api/architecture-assistant/capabilities"


def _user(db_session, org):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"tacache-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.modules.architecture.routes import architecture_assistant_routes as routes
    from app.models.unified_capability import UnifiedCapability

    routes._catalog_cache.clear()

    class World:
        pass

    w = World()
    w.org_a, w.org_b = make_org("tca"), make_org("tcb")
    w.user_a, w.user_b = _user(db_session, w.org_a), _user(db_session, w.org_b)
    tag = uuid.uuid4().hex[:6]
    w.tag = tag
    w.a_names = [f"Alpha {tag} {i}" for i in range(3)]
    w.b_names = [f"Beta {tag} {i}" for i in range(3)]
    w.a_caps = [f"Alpha Cap {tag}"]
    w.b_caps = [f"Beta Cap {tag}"]
    for org, names in ((w.org_a, w.a_names), (w.org_b, w.b_names)):
        for n in names:
            db_session.add(
                ArchiMateElement(name=n, type="Goal", layer="Motivation", organization_id=org.id)
            )
    for org, names in ((w.org_a, w.a_caps), (w.org_b, w.b_caps)):
        for n in names:
            db_session.add(UnifiedCapability(name=n, organization_id=org.id))
    db_session.commit()
    yield w
    routes._catalog_cache.clear()


def _names(resp, key="elements"):
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return {row["name"] for row in resp.get_json()[key]}


def _get(client, login_as, user, url):
    login_as(client, user)
    return client.get(url)


@pytest.mark.parametrize("first,second", [("a", "b"), ("b", "a")])
def test_second_organisation_never_sees_the_first_organisations_elements(
    client, login_as, world, first, second
):
    users = {"a": world.user_a, "b": world.user_b}
    names = {"a": world.a_names, "b": world.b_names}
    url = f"{URL}?q={world.tag}&per_page=50"
    got_first = _names(_get(client, login_as, users[first], url))
    got_second = _names(_get(client, login_as, users[second], url))
    assert got_first == set(names[first])
    assert got_second == set(names[second])
    assert not got_second & set(names[first])


def test_ids_do_not_cross_either(client, login_as, world):
    url = f"{URL}?q={world.tag}&per_page=50"
    a = _get(client, login_as, world.user_a, url).get_json()["elements"]
    b = _get(client, login_as, world.user_b, url).get_json()["elements"]
    assert not {r["id"] for r in a} & {r["id"] for r in b}


def test_one_organisation_still_gets_a_cached_answer(client, login_as, world, db_session):
    from app.models.archimate_core import ArchiMateElement

    url = f"{URL}?q={world.tag}&per_page=50"
    first = _names(_get(client, login_as, world.user_a, url))
    assert first == set(world.a_names)
    # Change the data underneath: an identical second call is answered from the cache.
    ArchiMateElement.query.filter(ArchiMateElement.name.in_(world.a_names)).update(
        {ArchiMateElement.type: "Principle"}, synchronize_session=False
    )
    db_session.commit()
    again = _get(client, login_as, world.user_a, url)
    assert [r["type"] for r in again.get_json()["elements"]] == ["Goal"] * 3


def test_anonymous_caller_gets_no_cached_data(client, world):
    from app.modules.architecture.routes import architecture_assistant_routes as routes

    # Seed what a signed-in organisation would have left behind.
    routes._catalog_cache["motivation_typeahead:%s::::50" % world.tag] = {
        "data": {"elements": [{"name": "LEAK"}]},
        "ts": routes._time.time(),
    }
    resp = client.get(f"{URL}?q={world.tag}&per_page=50")
    assert resp.status_code in (301, 302, 401, 403)
    assert b"LEAK" not in resp.get_data()


def test_no_organisation_means_nothing_is_cached(app, world):
    from flask import g

    from app.modules.architecture.routes import architecture_assistant_routes as routes

    routes._catalog_cache.clear()
    calls = []
    with app.test_request_context("/"):
        g.current_org_id = None
        routes._get_cached("k", lambda: calls.append(1) or {"x": 1}, ttl=300, scoped=True)
        routes._get_cached("k", lambda: calls.append(1) or {"x": 1}, ttl=300, scoped=True)
    assert len(calls) == 2
    assert not routes._catalog_cache


@pytest.mark.parametrize("first,second", [("a", "b"), ("b", "a")])
def test_capability_picker_is_not_shared_between_organisations(
    client, login_as, world, first, second
):
    users = {"a": world.user_a, "b": world.user_b}
    caps = {"a": world.a_caps, "b": world.b_caps}
    url = f"{CAP_URL}?search={world.tag}"
    got_first = _names(_get(client, login_as, users[first], url), "capabilities")
    got_second = _names(_get(client, login_as, users[second], url), "capabilities")
    assert got_first == set(caps[first])
    assert got_second == set(caps[second])
