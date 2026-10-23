"""Creating a Goal or Stakeholder from the motivation screens.

POST /architecture/motivation/<Type>/new with all fields filled answers
400 "Invalid request parameters" on a new organisation and nothing is saved.
The create modal posts name, description and the typed fields from
services/field_configs.py (goal_type/category for Goal,
stakeholder_type/role for Stakeholder, architecture_state for both), so the
create route must persist exactly those fields and keep the 400 for genuinely
invalid input, with a message that names the missing field.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.motivation import Goal, Stakeholder
from app.models.user import Role, User


def _make_user(db_session, org_id, label="creator", role="architect"):
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def client(app):
    return app.test_client()


def _viewer_role_id(db_session):
    viewer = Role.query.filter_by(name="Viewer").one()
    return viewer.id


def test_create_goal_persists_all_posted_fields(app, db_session, client, make_org, login_as):
    org = make_org("motivation-goal")
    user = _make_user(db_session, org.id, "GoalCreator")
    login_as(client, user)

    resp = client.post(
        "/architecture/motivation/Goal/new",
        json={
            "name": "Reduce operational cost by 20 percent",
            "description": "Cut the cost of running the claims platform",
            "goal_type": "strategic",
            "category": "efficiency",
            "architecture_state": "Target",
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is True

    created = db_session.get(Goal, payload["id"])
    assert created is not None, "the posted goal must exist for this organisation"
    assert created.organization_id == org.id
    assert created.name == "Reduce operational cost by 20 percent"
    assert created.description == "Cut the cost of running the claims platform"
    assert created.goal_type == "strategic"
    assert created.category == "efficiency"


def test_create_stakeholder_persists_all_posted_fields(
    app, db_session, client, make_org, login_as
):
    org = make_org("motivation-stakeholder")
    user = _make_user(db_session, org.id, "StakeholderCreator")
    login_as(client, user)

    resp = client.post(
        "/architecture/motivation/Stakeholder/new",
        json={
            "name": "Chief Financial Officer",
            "description": "Owns the finance function and its systems",
            "stakeholder_type": "executive",
            "role": "CFO",
            "architecture_state": "Baseline",
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is True

    created = db_session.get(Stakeholder, payload["id"])
    assert created is not None, "the posted stakeholder must exist for this organisation"
    assert created.organization_id == org.id
    assert created.name == "Chief Financial Officer"
    assert created.description == "Owns the finance function and its systems"
    assert created.stakeholder_type == "executive"
    assert created.role == "CFO"


def test_goal_created_by_org_a_is_invisible_to_org_b(
    app, db_session, client, make_org, login_as
):
    org_a = make_org("isolation-a")
    org_b = make_org("isolation-b")
    user_a = _make_user(db_session, org_a.id, "CreatorA")
    user_b = _make_user(db_session, org_b.id, "ViewerB")
    login_as(client, user_a)

    resp = client.post(
        "/architecture/motivation/Goal/new",
        json={
            "name": "Org A's private goal",
            "description": "only org A may see this",
            "goal_type": "operational",
            "category": "quality",
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    goal_id = resp.get_json()["id"]

    login_as(client, user_b)
    listed = client.get(
        "/architecture/api/layer/motivation/elements",
        query_string={"per_page": 100},
    )
    assert listed.status_code == 200, listed.get_data(as_text=True)
    listed_ids = [element["id"] for element in listed.get_json()["elements"]]
    assert goal_id not in listed_ids

    in_b = db_session.get(Goal, goal_id)
    assert in_b is None or in_b.organization_id != org_b.id
    assert db_session.query(Goal).filter(Goal.organization_id == org_b.id).all() == []


def test_viewer_posting_is_refused(app, db_session, client, make_org, login_as):
    org = make_org("viewer-refused")
    viewer_role_id = _viewer_role_id(db_session)
    user = _make_user(db_session, org.id, "Viewer", role="non_technical_owner")
    user.role_id = viewer_role_id
    db_session.flush()
    login_as(client, user)

    resp = client.post(
        "/architecture/motivation/Goal/new",
        json={
            "name": "Viewer must not create",
            "description": "this must be refused",
        },
    )
    assert resp.status_code == 403, resp.get_data(as_text=True)
    assert db_session.query(Goal).filter(Goal.organization_id == org.id).all() == []


def test_create_goal_without_name_returns_400_naming_the_field(
    app, db_session, client, make_org, login_as
):
    """Genuinely invalid input (no name at all) still gets a 400, and the
    message names the missing field instead of a generic one."""
    org = make_org("missing-field")
    user = _make_user(db_session, org.id, "GoalCreator")
    login_as(client, user)

    resp = client.post(
        "/architecture/motivation/Goal/new",
        json={"description": ""},
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "name" in (body.get("error") or "").lower(), body
    assert (
        db_session.query(Goal).filter(Goal.organization_id == org.id).all()
        == []
    )
