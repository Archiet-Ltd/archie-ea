"""compliance/run-all is administrator-only and limited to the caller's organisation.

The route's docstring said admin-only but it only required a sign-in, and it
asked the drift service to schedule checks for every solution it could see (all
organisations when the active organisation is unknown). Each scheduled check
calls the solution's service URL and may fire its webhooks.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org_id, label, *, admin=False):
    from werkzeug.security import generate_password_hash

    from app.models.user import Role, User

    kwargs = {}
    if admin:
        admin_role = Role.query.filter_by(name="Administrator").first()
        if admin_role is None:
            Role.insert_roles()
            admin_role = Role.query.filter_by(name="Administrator").first()
        kwargs["role"] = admin_role
    u = User(
        email=f"ra-{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Ra", last_name=label, confirmed=True,
        organization_id=org_id,
        password_hash=generate_password_hash("x"),
        **kwargs,
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def scene(db_session, make_org):
    from app.models.solution_models import Solution

    org_a = make_org("ra-a")
    org_b = make_org("ra-b")
    admin_a = _user(db_session, org_a.id, "admin", admin=True)
    member_a = _user(db_session, org_a.id, "member")
    sol_a = Solution(name="Own Scheduled", organization_id=org_a.id)
    sol_b = Solution(name="Foreign Scheduled", organization_id=org_b.id)
    db_session.add_all([sol_a, sol_b])
    db_session.flush()
    ids = {"admin_a": admin_a.id, "member_a": member_a.id, "sol_a": sol_a.id, "sol_b": sol_b.id}
    db_session.commit()
    return ids


@pytest.fixture
def checks(monkeypatch):
    """Record every solution a scheduled check is started for; nothing goes on the wire."""
    from app.modules.solutions_product.services.drift_remediation_service import (
        DriftRemediationService,
    )

    started = []

    # Solution has no persisted metadata column to hold a schedule, so every
    # solution is presented as scheduled and the organisation scope alone
    # decides which checks start.
    monkeypatch.setattr(
        DriftRemediationService, "_get_metadata",
        lambda self, solution: {"compliance_schedule": {
            "enabled": True, "service_url": "https://svc.example.test"}},
    )

    def fake_run(self, solution_id):
        started.append(solution_id)
        return {"success": True, "status": "compliant"}

    monkeypatch.setattr(DriftRemediationService, "run_scheduled_check", fake_run)
    return started


def _run_all(client, login_as, user_id, solution_id):
    from app import db

    db.session.remove()
    login_as(client, user_id)
    db.session.remove()
    resp = client.post(f"/api/solutions/{solution_id}/compliance/run-all")
    db.session.remove()
    return resp


def test_plain_member_is_refused_and_starts_nothing(scene, client, login_as, checks):
    resp = _run_all(client, login_as, scene["member_a"], scene["sol_a"])
    assert resp.status_code == 403, resp.get_data(as_text=True)
    assert checks == []


def test_admin_runs_only_their_own_organisations_checks(scene, client, login_as, checks):
    resp = _run_all(client, login_as, scene["admin_a"], scene["sol_a"])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert checks == [scene["sol_a"]]
    assert [r["solution_id"] for r in resp.get_json()["results"]] == [scene["sol_a"]]


def test_admin_with_a_foreign_solution_id_is_404_and_starts_nothing(scene, client, login_as, checks):
    resp = _run_all(client, login_as, scene["admin_a"], scene["sol_b"])
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert checks == []


def test_unknown_organisation_starts_nothing(scene, client, login_as, checks, monkeypatch):
    import app.middleware.tenant_context as tc

    monkeypatch.setattr(tc, "current_org_id", lambda: None)
    resp = _run_all(client, login_as, scene["admin_a"], scene["sol_a"])
    assert resp.status_code in (403, 404), resp.get_data(as_text=True)
    assert checks == []


def test_schedule_checks_filters_by_organisation_even_without_the_tenant_filter(scene, app):
    """The service names the organisation itself; it does not rely on the ambient filter."""
    from app import db
    from app.modules.solutions_product.services.drift_remediation_service import (
        DriftRemediationService,
    )
    from app.models.solution_models import Solution

    org_a = db.session.get(Solution, scene["sol_a"]).organization_id
    svc = DriftRemediationService()
    svc._get_metadata = lambda solution: {"compliance_schedule": {
        "enabled": True, "service_url": "https://svc.example.test"}}
    seen = []
    svc.run_scheduled_check = lambda solution_id: seen.append(solution_id) or {"success": True}

    # No active organisation in this context: the ambient filter is off.
    results = svc.schedule_checks(organization_id=org_a)
    assert scene["sol_a"] in seen and scene["sol_b"] not in seen
    assert all(r["solution_id"] != scene["sol_b"] for r in results)
