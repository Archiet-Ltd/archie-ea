"""The goal trace page, its entry point on the element page, and the operations sidebar link."""

import uuid

import pytest


def _clear_login_cache():
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _client_for(app, db_session, org, role="platform_admin"):
    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    user = User(
        email=f"trace-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Tra",
        last_name="Ce",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid
    _clear_login_cache()
    return client, user


def _tenant(org_id):
    from flask import g

    g.current_org_id = org_id


def _goal_with_initiative(db_session, org, goal_name="Grow Retail", initiative_name="Digital Checkout"):
    from app.models.archimate_core import ArchiMateElement
    from app.models.motivation import Goal
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    goal = Goal(name=goal_name, organization_id=org.id)
    initiative = EnterpriseInitiative(name=initiative_name, organization_id=org.id)
    db_session.add_all([goal, initiative])
    db_session.flush()
    if goal.archimate_element_id is None:
        element = ArchiMateElement(name=goal_name, type="Goal", layer="motivation", organization_id=org.id)
        db_session.add(element)
        db_session.flush()
        goal.archimate_element_id = element.id
        db_session.flush()
    goal.initiatives.append(initiative)
    db_session.flush()
    return goal, initiative


@pytest.fixture
def world(app, db_session, make_org):
    org_a, org_b = make_org("trace-a"), make_org("trace-b")
    client_a, _ = _client_for(app, db_session, org_a)
    client_b, _ = _client_for(app, db_session, org_b)
    return {"a": (org_a, client_a), "b": (org_b, client_b)}


def test_trace_page_shows_the_recorded_chain(world, db_session):
    org, client = world["a"]
    _tenant(org.id)
    goal, _ = _goal_with_initiative(db_session, org)
    _clear_login_cache()
    page = client.get("/architecture/goals/%d/trace" % goal.id).get_data(as_text=True)
    assert "Grow Retail" in page
    assert "Digital Checkout" in page
    assert "initiative goals" in page
    assert "permanent link" in page


def test_goal_with_nothing_recorded_says_so(world, db_session):
    from app.models.motivation import Goal

    org, client = world["a"]
    _tenant(org.id)
    goal = Goal(name="Lonely goal", organization_id=org.id)
    db_session.add(goal)
    db_session.flush()
    goal.archimate_element_id = None
    db_session.flush()
    _clear_login_cache()
    page = client.get("/architecture/goals/%d/trace" % goal.id).get_data(as_text=True)
    assert "Nothing is recorded below this goal yet" in page


def test_other_organisations_goal_is_not_found(world, db_session):
    org_a, client_a = world["a"]
    org_b, _ = world["b"]
    _tenant(org_b.id)
    goal, _ = _goal_with_initiative(db_session, org_b, "B goal", "B initiative")
    _tenant(org_a.id)
    _clear_login_cache()
    assert client_a.get("/architecture/goals/%d/trace" % goal.id).status_code == 404


def test_unknown_goal_is_not_found(world):
    _, client = world["a"]
    _clear_login_cache()
    assert client.get("/architecture/goals/999999999/trace").status_code == 404


def test_trace_from_here_appears_only_on_a_goal_element(world, db_session):
    from app.models.archimate_core import ArchiMateElement

    org, client = world["a"]
    _tenant(org.id)
    goal, _ = _goal_with_initiative(db_session, org)
    other = ArchiMateElement(name="Plain node", type="Node", layer="technology", organization_id=org.id)
    db_session.add(other)
    db_session.flush()
    _clear_login_cache()
    on_goal = client.get("/architecture/traceability/%d" % goal.archimate_element_id).get_data(as_text=True)
    assert "Trace from here" in on_goal
    assert "/architecture/goals/%d/trace" % goal.id in on_goal
    _clear_login_cache()
    on_node = client.get("/architecture/traceability/%d" % other.id).get_data(as_text=True)
    assert "Trace from here" not in on_node


def test_operations_role_has_the_value_streams_at_risk_link():
    from app.models.user import ROLE_OPERATIONS
    from app.utils.role_access import SIDEBAR_ZONES

    links = [lnk["label"] for zone in SIDEBAR_ZONES[ROLE_OPERATIONS] for lnk in zone["links"]]
    assert "Value Streams at Risk" in links


def test_operations_user_reaches_the_value_streams_page(app, db_session, make_org):
    org = make_org("ops")
    client, _ = _client_for(app, db_session, org, role="operations")
    _clear_login_cache()
    resp = client.get("/intelligence/value-streams-at-risk")
    assert resp.status_code == 200
