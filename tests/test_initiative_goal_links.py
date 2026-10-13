"""An initiative is tied to the goals it serves, from its own page.

The link is the ``initiative_goals`` row the goal trace already reads, so these
tests check the trace sees what the page wrote, that the unlinked list reads
both junctions, and that organisations are kept apart.
"""

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
        email=f"regcheck-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Reg",
        last_name="Check",
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


def _as_org(org):
    """Resolve the tenant the way a request would, for direct service calls."""
    from flask import g

    g.current_org_id = org.id


def _goal(db_session, org, name=None):
    from app.models.motivation import Goal

    goal = Goal(name=name or "Goal %s" % uuid.uuid4().hex[:6], organization_id=org.id)
    db_session.add(goal)
    db_session.flush()
    return goal


def _initiative(db_session, org, name=None):
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    row = EnterpriseInitiative(name=name or "Init %s" % uuid.uuid4().hex[:6], organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def world(app, db_session, make_org):
    org_a, org_b = make_org("goals-a"), make_org("goals-b")
    client_a, user_a = _client_for(app, db_session, org_a)
    client_b, user_b = _client_for(app, db_session, org_b)
    return {"a": (org_a, client_a, user_a), "b": (org_b, client_b, user_b)}


def _post(client, url, data=None):
    _clear_login_cache()
    return client.post(url, data=data or {}, follow_redirects=False)


def test_link_is_saved_and_listed(world, db_session):
    org, client, _ = world["a"]
    goal, initiative = _goal(db_session, org), _initiative(db_session, org)
    resp = _post(client, "/portfolio/initiatives/%d/goals" % initiative.id,
                 {"goal_id": goal.id, "contribution_level": "primary"})
    assert resp.status_code == 302
    from app.modules.portfolio.services.initiative_goal_links import list_links

    assert list_links(initiative.id) == [
        {"goal_id": goal.id, "name": goal.name, "contribution_level": "primary"}
    ]
    _clear_login_cache()
    page = client.get("/portfolio/%d" % initiative.id).get_data(as_text=True)
    assert goal.name in page


def test_goal_trace_sees_the_link(world, db_session):
    org, client, _ = world["a"]
    goal, initiative = _goal(db_session, org), _initiative(db_session, org)
    _post(client, "/portfolio/initiatives/%d/goals" % initiative.id, {"goal_id": goal.id})
    from app.modules.architecture.services.motivation_layer_service import MotivationLayerService

    trace = MotivationLayerService.get_goal_trace(goal.id, org.id)
    assert [i["id"] for i in trace["enterprise_initiatives"]] == [initiative.id]
    assert trace["trace_status"] == "recorded"


def test_link_can_be_removed(world, db_session):
    org, client, _ = world["a"]
    goal, initiative = _goal(db_session, org), _initiative(db_session, org)
    _post(client, "/portfolio/initiatives/%d/goals" % initiative.id, {"goal_id": goal.id})
    _post(client, "/portfolio/initiatives/%d/goals/%d/remove" % (initiative.id, goal.id))
    from app.modules.portfolio.services.initiative_goal_links import list_links

    assert list_links(initiative.id) == []


def test_same_goal_twice_is_refused(world, db_session):
    org, client, _ = world["a"]
    goal, initiative = _goal(db_session, org), _initiative(db_session, org)
    from app.modules.portfolio.services.initiative_goal_links import (
        InitiativeGoalLinkError,
        add_link,
    )

    _as_org(org)
    add_link(initiative.id, goal.id)
    with pytest.raises(InitiativeGoalLinkError) as err:
        add_link(initiative.id, goal.id)
    assert err.value.status == 409


def test_unknown_contribution_is_refused(world, db_session):
    org, _, _ = world["a"]
    goal, initiative = _goal(db_session, org), _initiative(db_session, org)
    from app.modules.portfolio.services.initiative_goal_links import (
        InitiativeGoalLinkError,
        add_link,
    )

    with pytest.raises(InitiativeGoalLinkError):
        add_link(initiative.id, goal.id, "huge")


def test_other_organisations_goal_cannot_be_linked(world, db_session):
    org_a, client_a, _ = world["a"]
    org_b = world["b"][0]
    foreign_goal = _goal(db_session, org_b)
    initiative = _initiative(db_session, org_a)
    _post(client_a, "/portfolio/initiatives/%d/goals" % initiative.id, {"goal_id": foreign_goal.id})
    from app.modules.portfolio.services.initiative_goal_links import list_links

    assert list_links(initiative.id) == []


def test_other_organisation_cannot_change_an_initiative(world, db_session):
    org_a = world["a"][0]
    _, client_b, _ = world["b"]
    initiative = _initiative(db_session, org_a)
    goal = _goal(db_session, org_a)
    resp = _post(client_b, "/portfolio/initiatives/%d/goals" % initiative.id, {"goal_id": goal.id})
    assert resp.status_code == 404
    from app.models.motivation import initiative_goals

    rows = db_session.execute(
        initiative_goals.select().where(initiative_goals.c.initiative_id == initiative.id)
    ).all()
    assert rows == []


def test_list_of_initiatives_serving_no_objective(world, db_session):
    org, client, _ = world["a"]
    linked, bare = _initiative(db_session, org), _initiative(db_session, org)
    goal = _goal(db_session, org)
    _post(client, "/portfolio/initiatives/%d/goals" % linked.id, {"goal_id": goal.id})
    _clear_login_cache()
    page = client.get("/portfolio/?filter=no-objective").get_data(as_text=True)
    assert bare.name in page
    assert linked.name not in page


def test_programme_goal_counts_as_an_objective(world, db_session):
    org, client, _ = world["a"]
    initiative = _initiative(db_session, org)
    goal = _goal(db_session, org)
    from app.models.strategic import StrategicInitiative, strategic_initiative_goals

    programme = StrategicInitiative(name="Prog %s" % uuid.uuid4().hex[:6], organization_id=org.id,
                                    record_kind="transformation_programme")
    db_session.add(programme)
    db_session.flush()
    db_session.execute(strategic_initiative_goals.insert().values(
        strategic_initiative_id=programme.id, goal_id=goal.id))
    initiative.linked_strategic_initiative_id = programme.id
    db_session.flush()
    from app.modules.portfolio.services.initiative_goal_links import initiatives_serving_no_objective

    bare = _initiative(db_session, org)
    _as_org(org)
    ids = [i.id for i in initiatives_serving_no_objective()]
    assert initiative.id not in ids
    assert bare.id in ids


def test_unlinked_list_never_shows_another_organisations_initiatives(world, db_session):
    org_a = world["a"][0]
    _, client_b, _ = world["b"]
    bare_a = _initiative(db_session, org_a)
    _clear_login_cache()
    page = client_b.get("/portfolio/?filter=no-objective").get_data(as_text=True)
    assert bare_a.name not in page


# ── Review round: organisation scoping with and without a resolved tenant ───

def _link_rows(db_session, initiative_id):
    from app.models.motivation import initiative_goals

    return db_session.execute(
        initiative_goals.select().where(initiative_goals.c.initiative_id == initiative_id)
    ).all()


def test_unresolved_organisation_reads_and_writes_nothing(world, db_session):
    from flask import g

    org = world["a"][0]
    goal, initiative = _goal(db_session, org), _initiative(db_session, org)
    from app.modules.portfolio.services import initiative_goal_links as svc

    g.current_org_id = None
    try:
        assert svc.initiatives_serving_no_objective() == []
        with pytest.raises(svc.InitiativeGoalLinkError) as err:
            svc.add_link(initiative.id, goal.id)
        assert err.value.status == 404
        with pytest.raises(svc.InitiativeGoalLinkError) as err:
            svc.remove_link(initiative.id, goal.id)
        assert err.value.status == 404
        with pytest.raises(svc.InitiativeGoalLinkError):
            svc.list_links(initiative.id)
    finally:
        _clear_login_cache()
    assert _link_rows(db_session, initiative.id) == []


def test_resolved_organisation_is_the_callers_not_the_initiatives(world, db_session):
    from flask import g

    org_a, org_b = world["a"][0], world["b"][0]
    initiative_a = _initiative(db_session, org_a)
    goal_a = _goal(db_session, org_a)
    from app.modules.portfolio.services import initiative_goal_links as svc

    g.current_org_id = org_b.id
    try:
        with pytest.raises(svc.InitiativeGoalLinkError) as err:
            svc.add_link(initiative_a.id, goal_a.id)
        assert err.value.status == 404
    finally:
        _clear_login_cache()
    assert _link_rows(db_session, initiative_a.id) == []


def test_other_organisation_cannot_remove_a_link(world, db_session):
    org_a = world["a"][0]
    _, client_b, _ = world["b"]
    goal, initiative = _goal(db_session, org_a), _initiative(db_session, org_a)
    from app.modules.portfolio.services.initiative_goal_links import add_link

    _as_org(org_a)
    add_link(initiative.id, goal.id)
    db_session.flush()
    resp = _post(client_b, "/portfolio/initiatives/%d/goals/%d/remove" % (initiative.id, goal.id))
    assert resp.status_code == 404
    assert len(_link_rows(db_session, initiative.id)) == 1


def test_no_objective_count_is_per_organisation_and_matches_the_list(world, db_session):
    org_a, client_a, _ = world["a"]
    org_b = world["b"][0]
    bare_a1, bare_a2 = _initiative(db_session, org_a), _initiative(db_session, org_a)
    for _ in range(3):
        _initiative(db_session, org_b)
    _clear_login_cache()
    page = client_a.get("/portfolio/").get_data(as_text=True)
    assert "2 initiatives serving no objective" in page or "2 serving no objective" in page \
        or ">2<" in page
    _clear_login_cache()
    filtered = client_a.get("/portfolio/?filter=no-objective").get_data(as_text=True)
    assert bare_a1.name in filtered and bare_a2.name in filtered


def test_programme_goal_initiative_is_excluded_through_the_route_and_shown_on_its_page(
    world, db_session
):
    org, client, _ = world["a"]
    inherits, bare = _initiative(db_session, org), _initiative(db_session, org)
    goal = _goal(db_session, org, name="Grow the franchise")
    from app.models.strategic import StrategicInitiative, strategic_initiative_goals

    programme = StrategicInitiative(name="Prog %s" % uuid.uuid4().hex[:6], organization_id=org.id,
                                    record_kind="transformation_programme")
    db_session.add(programme)
    db_session.flush()
    db_session.execute(strategic_initiative_goals.insert().values(
        strategic_initiative_id=programme.id, goal_id=goal.id))
    inherits.linked_strategic_initiative_id = programme.id
    db_session.flush()
    _clear_login_cache()
    listed = client.get("/portfolio/?filter=no-objective").get_data(as_text=True)
    assert bare.name in listed
    assert inherits.name not in listed
    _clear_login_cache()
    detail = client.get("/portfolio/%d" % inherits.id).get_data(as_text=True)
    assert "Grow the franchise" in detail
    assert "via the programme" in detail
    assert "No objective recorded" not in detail
    _clear_login_cache()
    bare_page = client.get("/portfolio/%d" % bare.id).get_data(as_text=True)
    assert "No objective recorded" in bare_page


@pytest.mark.parametrize("role", ["portfolio_manager", "solution_architect"])
def test_isolation_holds_for_a_non_platform_role(app, db_session, make_org, role):
    org_a, org_b = make_org("goals-np-a"), make_org("goals-np-b")
    client_b, _ = _client_for(app, db_session, org_b, role=role)
    goal, initiative = _goal(db_session, org_a), _initiative(db_session, org_a)
    from app.modules.portfolio.services.initiative_goal_links import add_link

    _as_org(org_a)
    add_link(initiative.id, goal.id)
    db_session.flush()
    resp = _post(client_b, "/portfolio/initiatives/%d/goals/%d/remove" % (initiative.id, goal.id))
    assert resp.status_code in (403, 404)
    assert len(_link_rows(db_session, initiative.id)) == 1
    _clear_login_cache()
    page = client_b.get("/portfolio/?filter=no-objective").get_data(as_text=True)
    assert initiative.name not in page
    _clear_login_cache()
    assert client_b.get("/portfolio/%d" % initiative.id).status_code in (403, 404)
