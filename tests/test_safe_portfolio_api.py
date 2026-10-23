"""Unit tests for SAFe Portfolio Kanban API."""

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.user import User
from app.services.element_profile_service import set_kind, set_wsjf_components


def _login(client, user_id):
    """Login a user in the test client session."""
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id, app=client.application)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context
    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        g.pop(cached, None)


@pytest.fixture
def test_user(db_session, make_org):
    """Create a test user with Architect role."""
    org = make_org()
    user = User(
        first_name="Test",
        last_name="Architect",
        email=f"architect_{org.id}@test.example",
        organization_id=org.id,
        enterprise_role="Architect",
        confirmed=True,
    )
    user.password = "TestPassword123!"
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def epic_element(db_session, make_org):
    """Create a work package element marked as a SAFe Epic."""
    org = make_org()
    element = ArchiMateElement(
        name="Test Epic",
        type="WorkPackage",
        organization_id=org.id,
        description="Test epic for portfolio kanban",
    )
    db_session.add(element)
    db_session.flush()

    set_kind(element, "Epic", framework_id="safe", source="test")
    set_wsjf_components(
        element,
        business_value=8,
        time_criticality=7,
        risk_reduction=6,
        job_size=3,
        source="test",
    )
    db_session.commit()
    return element


def test_portfolio_index_loads(app, client, test_user, epic_element, tenant_ctx):
    """GET /safe/portfolio returns 200 with the kanban board."""
    _login(client, test_user.id)
    with tenant_ctx(epic_element.organization_id):
        response = client.get("/safe/portfolio")
        assert response.status_code == 200
        assert b"Portfolio Kanban" in response.data
        assert b"Test Epic" in response.data


def test_portfolio_api_returns_json(app, client, test_user, epic_element, tenant_ctx):
    """GET /safe/portfolio/api returns JSON with columns and epics."""
    _login(client, test_user.id)
    with tenant_ctx(epic_element.organization_id):
        response = client.get("/safe/portfolio/api")
        assert response.status_code == 200
        data = response.get_json()

        assert "columns" in data
        assert "states" in data
        assert "can_write" in data

        # Verify states
        expected_states = ["Funnel", "Reviewing", "Analyzing", "Portfolio Backlog", "Implementing", "Done"]
        assert data["states"] == expected_states

        # Verify epic is in Funnel (default)
        funnel_epics = data["columns"]["Funnel"]
        assert len(funnel_epics) == 1
        assert funnel_epics[0]["name"] == "Test Epic"
        assert funnel_epics[0]["wsjf_score"] == 7.0  # (8+7+6)/3


def test_portfolio_api_wsjf_ordering(app, client, test_user, db_session, make_org, tenant_ctx):
    """Epics within a column are ordered by WSJF score descending."""
    org = make_org()

    # Create three epics with different WSJF scores
    epics_data = [
        {"name": "Low Priority", "bv": 2, "tc": 2, "rr": 2, "js": 3},  # WSJF = 2.0
        {"name": "High Priority", "bv": 9, "tc": 9, "rr": 9, "js": 3},  # WSJF = 9.0
        {"name": "Med Priority", "bv": 5, "tc": 5, "rr": 5, "js": 3},  # WSJF = 5.0
    ]

    for data in epics_data:
        element = ArchiMateElement(
            name=data["name"],
            type="WorkPackage",
            organization_id=org.id,
        )
        db_session.add(element)
        db_session.flush()
        set_kind(element, "Epic", framework_id="safe", source="test")
        set_wsjf_components(
            element,
            business_value=data["bv"],
            time_criticality=data["tc"],
            risk_reduction=data["rr"],
            job_size=data["js"],
            source="test",
        )
    db_session.commit()

    _login(client, test_user.id)
    with tenant_ctx(org.id):
        response = client.get("/safe/portfolio/api")
        assert response.status_code == 200
        data = response.get_json()

        funnel_epics = data["columns"]["Funnel"]
        assert len(funnel_epics) == 3

        # Verify descending order by WSJF
        assert funnel_epics[0]["name"] == "High Priority"
        assert funnel_epics[0]["wsjf_score"] == 9.0
        assert funnel_epics[1]["name"] == "Med Priority"
        assert funnel_epics[1]["wsjf_score"] == 5.0
        assert funnel_epics[2]["name"] == "Low Priority"
        assert funnel_epics[2]["wsjf_score"] == 2.0


def test_portfolio_api_move_epic_to_new_state(app, client, test_user, epic_element, tenant_ctx, db_session):
    """PATCH /safe/portfolio/api/<id> moves an epic to a new kanban state."""
    _login(client, test_user.id)
    with tenant_ctx(epic_element.organization_id):
        response = client.patch(
            f"/safe/portfolio/api/{epic_element.id}",
            json={"kanban_state": "Implementing"},
        )
        assert response.status_code == 200
        data = response.get_json()

        assert data["id"] == epic_element.id
        assert data["kanban_state"] == "Implementing"
        assert data["name"] == "Test Epic"

        # Verify it persisted
        db_session.expire_all()
        from app.services.element_profile_service import _read_property_value
        db_session.refresh(epic_element)
        kanban_state = _read_property_value(epic_element, "kanban_state")
        assert kanban_state == "Implementing"


def test_portfolio_api_rejects_invalid_state(app, client, test_user, epic_element, tenant_ctx):
    """PATCH with invalid kanban state returns 400."""
    _login(client, test_user.id)
    with tenant_ctx(epic_element.organization_id):
        response = client.patch(
            f"/safe/portfolio/api/{epic_element.id}",
            json={"kanban_state": "InvalidState"},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "Invalid kanban state" in data["error"]


def test_portfolio_api_rejects_non_epic(app, client, test_user, db_session, make_org, tenant_ctx):
    """PATCH on a non-Epic work package returns 400."""
    org = make_org()
    # Create a work package that is NOT an Epic
    element = ArchiMateElement(
        name="Plain Work Package",
        type="WorkPackage",
        organization_id=org.id,
    )
    db_session.add(element)
    db_session.commit()

    _login(client, test_user.id)
    with tenant_ctx(org.id):
        response = client.patch(
            f"/safe/portfolio/api/{element.id}",
            json={"kanban_state": "Implementing"},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "not an Epic" in data["error"]


def test_portfolio_api_tenant_isolation(app, client, test_user, db_session, make_org, tenant_ctx):
    """An epic from org A is not visible to org B."""
    org_a = make_org()
    org_b = make_org()

    element = ArchiMateElement(
        name="Org A Epic",
        type="WorkPackage",
        organization_id=org_a.id,
    )
    db_session.add(element)
    db_session.flush()
    set_kind(element, "Epic", framework_id="safe", source="test")
    set_wsjf_components(element, business_value=5, time_criticality=5, risk_reduction=5, job_size=5)
    db_session.commit()
    element_id = element.id

    _login(client, test_user.id)
    # Switch to org B
    with tenant_ctx(org_b.id):
        # Try to access org A's epic
        response = client.patch(
            f"/safe/portfolio/api/{element_id}",
            json={"kanban_state": "Implementing"},
        )
        assert response.status_code == 404

        # Verify org B sees empty board
        response = client.get("/safe/portfolio/api")
        assert response.status_code == 200
        data = response.get_json()
        for state, epics in data["columns"].items():
            assert len(epics) == 0, f"Org B should see no epics in {state}"


def test_portfolio_api_updates_wsjf_components(app, client, test_user, epic_element, tenant_ctx, db_session):
    """PATCH can update WSJF components and returns new score."""
    _login(client, test_user.id)
    with tenant_ctx(epic_element.organization_id):
        response = client.patch(
            f"/safe/portfolio/api/{epic_element.id}",
            json={
                "business_value": 10,
                "time_criticality": 10,
                "risk_reduction": 10,
                "job_size": 5,
            },
        )
        assert response.status_code == 200
        data = response.get_json()

        # New WSJF = (10+10+10)/5 = 6.0
        assert data["wsjf_score"] == 6.0
        assert data["business_value"] == 10
        assert data["time_criticality"] == 10
        assert data["risk_reduction"] == 10
        assert data["job_size"] == 5

        # Verify persistence
        db_session.expire_all()
        from app.services.element_profile_service import read_profile
        db_session.refresh(epic_element)
        profile = read_profile(epic_element)
        assert profile["wsjf_score"] == 6.0

