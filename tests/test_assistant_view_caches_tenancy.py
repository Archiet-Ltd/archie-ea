"""Two more per-process caches are kept per organisation.

* ``/applications/rationalization/api/portfolio-scores`` held its payload (application
  names and scores) under a key that named only the disposition filter.
* The assistant's context helper is a process-wide singleton whose cached context
  (recent application names, counts) and field suggestions named no organisation.
"""
from __future__ import annotations

import uuid

import pytest

SCORES_URL = "/applications/rationalization/api/portfolio-scores"


def _user(db_session, org):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"avc-{uuid.uuid4().hex[:8]}@example.com",
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
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_rationalization import ApplicationRationalizationScore
    from app.modules.applications.routes import rationalization_api_routes as rat

    rat._PORTFOLIO_SCORES_CACHE.clear()

    class World:
        pass

    w = World()
    w.org_a, w.org_b = make_org("ava"), make_org("avb")
    w.user_a, w.user_b = _user(db_session, w.org_a), _user(db_session, w.org_b)
    tag = uuid.uuid4().hex[:6]
    w.a_name, w.b_name = f"Alpha app {tag}", f"Beta app {tag}"
    for org, name in ((w.org_a, w.a_name), (w.org_b, w.b_name)):
        app_row = ApplicationComponent(name=name, organization_id=org.id)
        db_session.add(app_row)
        db_session.flush()
        db_session.add(
            ApplicationRationalizationScore(
                application_component_id=app_row.id,
                organization_id=org.id,
                overall_health_score=50,
                rationalization_action="TOLERATE",
            )
        )
    db_session.commit()
    yield w
    rat._PORTFOLIO_SCORES_CACHE.clear()


@pytest.mark.parametrize("first,second", [("a", "b"), ("b", "a")])
def test_portfolio_scores_are_not_shared_between_organisations(
    client, login_as, world, first, second
):
    users = {"a": world.user_a, "b": world.user_b}
    names = {"a": world.a_name, "b": world.b_name}

    def names_for(who):
        login_as(client, users[who])
        resp = client.get(SCORES_URL)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return {row["app_name"] for row in resp.get_json()}

    got_first, got_second = names_for(first), names_for(second)
    assert names[first] in got_first and names[second] not in got_first
    assert names[second] in got_second and names[first] not in got_second


def test_portfolio_scores_still_cached_per_organisation(client, login_as, world):
    login_as(client, world.user_a)
    assert client.get(SCORES_URL).headers.get("X-Cache") == "MISS"
    assert client.get(SCORES_URL).headers.get("X-Cache") == "HIT"


@pytest.mark.parametrize("first,second", [("a", "b"), ("b", "a")])
def test_context_helper_does_not_hand_one_organisation_the_others_context(
    tenant_ctx, db_session, world, first, second
):
    from app.models.unified_capability import UnifiedCapability
    from app.services.context_aware_ai_helper import ContextAwareAIHelper, ContextType

    # Organisation A holds more capabilities than B, so their counts differ.
    for i in range(3):
        db_session.add(UnifiedCapability(name=f"Extra {i} {uuid.uuid4().hex[:6]}", organization_id=world.org_a.id))
    db_session.commit()

    orgs = {"a": world.org_a, "b": world.org_b}

    def total(helper, who):
        with tenant_ctx(orgs[who].id):
            return helper.load_context(ContextType.CAPABILITY_MAPPING)["total_capabilities"]

    expected = {who: total(ContextAwareAIHelper(user_id=1), who) for who in orgs}
    assert expected["a"] != expected["b"]
    shared = ContextAwareAIHelper(user_id=1)  # one instance, as the singleton is
    assert total(shared, first) == expected[first]
    assert total(shared, second) == expected[second]


def test_context_helper_caches_nothing_without_an_organisation(app, world):
    from app.services.context_aware_ai_helper import ContextAwareAIHelper, ContextType

    helper = ContextAwareAIHelper(user_id=1)
    with app.test_request_context("/"):
        helper.load_context(ContextType.GENERAL)
    assert helper._context_cache == {}
