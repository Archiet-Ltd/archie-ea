"""Webhook registration and compliance checks refuse another organisation's solution.

A webhook row has no organisation column; its solution carries it. Registering a
URL on a solution the caller cannot see would make the platform post that
solution's drift and compliance data to the caller's URL. The compliance check
loaded a published spec by id alone, so a posted spec_id from another
organisation was checked and its missing endpoints returned.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org_id, label):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    u = User(
        email=f"whc-{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Whc", last_name=label, confirmed=True,
        organization_id=org_id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def scene(db_session, make_org):
    from app.models.published_api_spec import PublishedAPISpec
    from app.models.solution_models import Solution

    org_a = make_org("whc-a")
    org_b = make_org("whc-b")
    user_a = _user(db_session, org_a.id, "a")
    sol_a = Solution(name="Own Solution", organization_id=org_a.id)
    sol_b = Solution(name="Foreign Solution", organization_id=org_b.id)
    db_session.add_all([sol_a, sol_b])
    db_session.flush()
    spec = {"openapi": "3.0.0", "paths": {"/secret-b-endpoint": {"get": {}}}}
    spec_a = PublishedAPISpec(solution_id=sol_a.id, spec_type="openapi", spec_version="1.0.0",
                              spec_content={"openapi": "3.0.0", "paths": {"/own": {"get": {}}}},
                              status="published")
    spec_b = PublishedAPISpec(solution_id=sol_b.id, spec_type="openapi", spec_version="1.0.0",
                              spec_content=spec, status="published")
    db_session.add_all([spec_a, spec_b])
    db_session.flush()
    ids = {"user_a": user_a.id, "sol_a": sol_a.id, "sol_b": sol_b.id,
           "spec_a": spec_a.id, "spec_b": spec_b.id}
    db_session.commit()
    return ids


def _call(client, login_as, scene, method, path, **kwargs):
    from app import db

    db.session.remove()
    login_as(client, scene["user_a"])
    db.session.remove()
    resp = getattr(client, method)(path, **kwargs)
    db.session.remove()
    return resp


def _webhook_count(solution_id):
    from app import db
    from app.models.spec_webhook import SpecWebhook

    db.session.remove()
    return SpecWebhook.query.filter_by(solution_id=solution_id).count()


@pytest.fixture
def checker_spy(monkeypatch):
    """Replace the checker so a reached check is visible and nothing goes on the wire."""
    from app.modules.solutions_product.services import compliance_checker

    calls = []

    def fake_check(self, published_spec_id, service_url, timeout=10, checked_by_id=None):
        calls.append(published_spec_id)
        return {"success": True, "status": "compliant", "missing_endpoints": ["/secret-b-endpoint"]}

    monkeypatch.setattr(compliance_checker.ComplianceChecker, "check", fake_check)
    return calls


WEBHOOK = {"url": "https://attacker.example.test/hook", "event_types": ["drift_detected"]}


def test_webhook_on_foreign_solution_is_404_and_writes_nothing(scene, client, login_as):
    resp = _call(client, login_as, scene, "post", f"/api/solutions/{scene['sol_b']}/webhooks",
                 json=WEBHOOK)
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert _webhook_count(scene["sol_b"]) == 0


def test_webhook_on_unknown_solution_gives_the_same_answer(scene, client, login_as):
    foreign = _call(client, login_as, scene, "post", f"/api/solutions/{scene['sol_b']}/webhooks",
                    json=WEBHOOK)
    missing = _call(client, login_as, scene, "post", "/api/solutions/999999999/webhooks",
                    json=WEBHOOK)
    assert foreign.status_code == missing.status_code == 404


def test_webhook_with_unknown_organisation_is_404_and_writes_nothing(scene, client, login_as, monkeypatch):
    import app.middleware.tenant_context as tc

    monkeypatch.setattr(tc, "current_org_id", lambda: None)
    for sid in (scene["sol_a"], scene["sol_b"]):
        resp = _call(client, login_as, scene, "post", f"/api/solutions/{sid}/webhooks", json=WEBHOOK)
        assert resp.status_code == 404, resp.get_data(as_text=True)
        assert _webhook_count(sid) == 0


def test_webhook_on_own_solution_is_still_created(scene, client, login_as):
    resp = _call(client, login_as, scene, "post", f"/api/solutions/{scene['sol_a']}/webhooks",
                 json=WEBHOOK)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert _webhook_count(scene["sol_a"]) == 1


def test_compliance_check_on_foreign_solution_is_404(scene, client, login_as, checker_spy):
    resp = _call(client, login_as, scene, "post", f"/api/solutions/{scene['sol_b']}/compliance/check",
                 json={"service_url": "https://svc.example.test"})
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert "secret-b-endpoint" not in resp.get_data(as_text=True)
    assert checker_spy == []


def test_compliance_check_with_foreign_spec_id_is_404(scene, client, login_as, checker_spy):
    """Own solution in the path, another organisation's spec id in the body."""
    resp = _call(client, login_as, scene, "post", f"/api/solutions/{scene['sol_a']}/compliance/check",
                 json={"service_url": "https://svc.example.test", "spec_id": scene["spec_b"]})
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert "secret-b-endpoint" not in resp.get_data(as_text=True)
    assert checker_spy == []


def test_compliance_check_with_unknown_organisation_is_404(scene, client, login_as, checker_spy, monkeypatch):
    import app.middleware.tenant_context as tc

    monkeypatch.setattr(tc, "current_org_id", lambda: None)
    resp = _call(client, login_as, scene, "post", f"/api/solutions/{scene['sol_a']}/compliance/check",
                 json={"service_url": "https://svc.example.test"})
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert checker_spy == []


def test_compliance_check_on_own_solution_still_runs(scene, client, login_as, checker_spy):
    resp = _call(client, login_as, scene, "post", f"/api/solutions/{scene['sol_a']}/compliance/check",
                 json={"service_url": "https://svc.example.test", "spec_id": scene["spec_a"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert checker_spy == [scene["spec_a"]]
    # and with no spec_id the solution's own latest spec is used
    resp = _call(client, login_as, scene, "post", f"/api/solutions/{scene['sol_a']}/compliance/check",
                 json={"service_url": "https://svc.example.test"})
    assert resp.status_code == 200
    assert checker_spy == [scene["spec_a"], scene["spec_a"]]
