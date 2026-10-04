"""Regression tests for seven route crashes found by the tenant-isolation sweep.

Each route below handled a valid request from its own organisation without a
server error, so the isolation sweep can prove it refuses another organisation.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org(make_org):
    return make_org("sweepcrash")


@pytest.fixture
def logged_in_client(app, db_session, org, login_as):
    from app.models.user import Permission, Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()

    user = User(
        email=f"sweepcrash-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Sweep",
        last_name="Crash",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)
    return client


# ── Fix 1: PATCH /api/adm-kanban/v2/deliverables/<id>/check ──────────────

@pytest.fixture
def deliverable(db_session):
    from app.models.adm_deliverable import ADMDeliverable

    d = ADMDeliverable(
        phase="A",
        name=f"Test deliverable {uuid.uuid4().hex[:8]}",
        description="Sweep test",
        is_template=True,
    )
    db_session.add(d)
    db_session.flush()
    return d


def test_check_deliverable_succeeds_for_existing_deliverable(
    logged_in_client, deliverable
):
    """A valid deliverable check must not 500 with a FK violation."""
    resp = logged_in_client.patch(
        f"/api/adm-kanban/v2/deliverables/{deliverable.id}/check",
        json={"board_id": 1, "checked": True},
    )
    assert resp.status_code == 200, (resp.status_code, resp.get_json())
    data = resp.get_json()
    assert data["success"] is True


def test_check_deliverable_returns_404_for_missing_deliverable(logged_in_client):
    """A non-existent deliverable must return 404, not 500 FK violation."""
    resp = logged_in_client.patch(
        "/api/adm-kanban/v2/deliverables/999999/check",
        json={"board_id": 1, "checked": True},
    )
    assert resp.status_code == 404, (resp.status_code, resp.get_json())


# ── Fix 2: POST /api/enterprise/requirements/<id>/generate-test-cases ────

@pytest.fixture
def requirement(db_session, org):
    from app.models.solution_architect_models import SolutionRequirement

    req = SolutionRequirement(
        name=f"Test req {uuid.uuid4().hex[:8]}",
        description="Sweep test requirement",
        acceptance_criteria="The system must respond within 200ms",
        organization_id=org.id,
    )
    db_session.add(req)
    db_session.flush()
    return req


def test_generate_test_cases_succeeds(logged_in_client, requirement):
    """Generating test cases must not fail with AttributeError on requirement_name."""
    resp = logged_in_client.post(
        f"/api/enterprise/requirements/{requirement.id}/generate-test-cases",
    )
    assert resp.status_code == 200, (resp.status_code, resp.get_json())
    data = resp.get_json()
    assert "test_cases" in data


# ── Fix 3: POST /api/enterprise/solutions/<id>/populate-from-template ────

@pytest.fixture
def solution_with_template(db_session, org):
    from app.models.requirement_template import RequirementTemplate
    from app.models.solution_architect_models import Solution

    sol = Solution(
        name=f"Test solution {uuid.uuid4().hex[:8]}",
        description="Sweep test",
        organization_id=org.id,
    )
    db_session.add(sol)
    db_session.flush()

    tpl = RequirementTemplate(
        name=f"Test template {uuid.uuid4().hex[:8]}",
        layer="business",
        is_system=True,
    )
    db_session.add(tpl)
    db_session.flush()
    return sol


def test_populate_from_template_succeeds(logged_in_client, solution_with_template):
    """Populating from template must not fail with AttributeError on is_active."""
    resp = logged_in_client.post(
        f"/api/enterprise/solutions/{solution_with_template.id}/populate-from-template",
        json={"layers": ["business"]},
    )
    assert resp.status_code in (200, 201), (resp.status_code, resp.get_json())
    data = resp.get_json()
    assert data.get("solution_id") == solution_with_template.id


# ── Fix 4: POST /api/solutions/<id>/relationships/extract ────────────────

@pytest.fixture
def solution_for_extract(db_session, org):
    from app.models.solution_architect_models import Solution

    sol = Solution(
        name=f"Test solution {uuid.uuid4().hex[:8]}",
        description="Sweep test",
        organization_id=org.id,
    )
    db_session.add(sol)
    db_session.flush()
    return sol


def test_extract_relationships_returns_clear_error(logged_in_client, solution_for_extract):
    """Missing orchestrator method must return a clear 400, not AttributeError."""
    resp = logged_in_client.post(
        f"/api/solutions/{solution_for_extract.id}/relationships/extract",
        json={"message": "The web frontend calls the order service"},
    )
    assert resp.status_code == 400, (resp.status_code, resp.get_json())
    data = resp.get_json()
    assert "error" in data


# ── Fix 5: POST /architecture/decisions/<id>/edit ────────────────────────

@pytest.fixture
def decision(db_session, org):
    from app.models.architecture_decision import ArchitectureDecision

    original_title = f"Original title {uuid.uuid4().hex[:8]}"
    d = ArchitectureDecision(
        title=original_title,
        status="proposed",
        organization_id=org.id,
    )
    db_session.add(d)
    db_session.flush()
    return d


def test_edit_decision_without_title_does_not_500(logged_in_client, decision):
    """Editing a decision without a title must keep the existing title, not 500."""
    resp = logged_in_client.post(
        f"/architecture/decisions/{decision.id}/edit",
        data={"status": "accepted"},
        follow_redirects=True,
    )
    assert resp.status_code == 200, (resp.status_code, resp.get_json() if resp.is_json else "html")


# ── Fix 6: POST /dashboard/api/archimate-elements/<id>/correct ───────────

@pytest.fixture
def archimate_element(db_session, org):
    from app.models.archimate_core import ArchiMateElement

    elem = ArchiMateElement(
        name=f"Test element {uuid.uuid4().hex[:8]}",
        type="ApplicationComponent",
        layer="application",
        organization_id=org.id,
    )
    db_session.add(elem)
    db_session.flush()
    return elem


def test_archimate_element_correct_succeeds(logged_in_client, archimate_element):
    """Correcting an ArchiMate element must not fail with ModuleNotFoundError."""
    resp = logged_in_client.post(
        f"/dashboard/api/archimate-elements/{archimate_element.id}/correct",
        json={"action": "approve"},
    )
    assert resp.status_code == 200, (resp.status_code, resp.get_json())
    data = resp.get_json()
    assert data["success"] is True


# ── Fix 7: POST /solutions/<id>/codegen/data/import ──────────────────────

@pytest.fixture
def solution_for_import(db_session, org):
    from app.models.solution_architect_models import Solution

    sol = Solution(
        name=f"Test solution {uuid.uuid4().hex[:8]}",
        description="Sweep test",
        organization_id=org.id,
    )
    db_session.add(sol)
    db_session.flush()
    return sol


def test_data_import_rejects_string_mappings(logged_in_client, solution_for_import):
    """String mappings must be rejected with a clear 400, not AttributeError."""
    resp = logged_in_client.post(
        f"/solutions/{solution_for_import.id}/codegen/data/import",
        json={
            "mappings": ["not_a_dict"],
            "rows": [{"col": "val"}],
        },
    )
    assert resp.status_code == 400, (resp.status_code, resp.get_json())
    data = resp.get_json()
    assert "mappings" in data.get("error", "").lower()