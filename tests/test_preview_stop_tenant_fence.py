"""Stopping a generated-product preview requires owning the solution and bundle.

DELETE /api/solutions/<id>/product/<bundle_id>/preview used to stop the running
container for ``bundle_id`` without looking at the solution or the bundle.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org_id, label):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    u = User(
        email=f"prv-{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Prv", last_name=label, confirmed=True,
        organization_id=org_id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def scene(db_session, make_org):
    from app.models.solution_models import Solution
    from app.modules.solutions_product.models import SolutionCodeBundle

    org_a = make_org("prv-a")
    org_b = make_org("prv-b")
    user_a = _user(db_session, org_a.id, "a")
    sol_a = Solution(name="Own Solution", organization_id=org_a.id)
    sol_a2 = Solution(name="Own Other Solution", organization_id=org_a.id)
    sol_b = Solution(name="Foreign Solution", organization_id=org_b.id)
    db_session.add_all([sol_a, sol_a2, sol_b])
    db_session.flush()
    tag = uuid.uuid4().hex[:10]
    bundles = {
        "own": f"own-{tag}",
        "foreign": f"foreign-{tag}",
    }
    db_session.add_all([
        SolutionCodeBundle(solution_id=sol_a.id, bundle_id=bundles["own"]),
        SolutionCodeBundle(solution_id=sol_b.id, bundle_id=bundles["foreign"]),
    ])
    db_session.flush()
    ids = {
        "user_a": user_a.id, "sol_a": sol_a.id, "sol_a2": sol_a2.id,
        "sol_b": sol_b.id, **{f"bundle_{k}": v for k, v in bundles.items()},
    }
    db_session.commit()
    return ids


@pytest.fixture
def stopped(monkeypatch):
    """Stub the container call; record every bundle id it is asked to stop."""
    from app.modules.solutions_product.services.sandbox_runner import SandboxRunner

    calls = []
    monkeypatch.setattr(SandboxRunner, "stop_preview", lambda self, bundle_id: calls.append(bundle_id))
    return calls


def _delete(client, login_as, user_id, solution_id, bundle_id):
    from app import db

    db.session.remove()
    login_as(client, user_id)
    db.session.remove()
    resp = client.delete(f"/api/solutions/{solution_id}/product/{bundle_id}/preview")
    db.session.remove()
    return resp


def test_foreign_solution_and_bundle_is_refused(scene, client, login_as, stopped):
    resp = _delete(client, login_as, scene["user_a"], scene["sol_b"], scene["bundle_foreign"])
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert stopped == []


def test_foreign_bundle_under_own_solution_is_refused(scene, client, login_as, stopped):
    resp = _delete(client, login_as, scene["user_a"], scene["sol_a"], scene["bundle_foreign"])
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert stopped == []


def test_own_bundle_under_other_own_solution_is_refused(scene, client, login_as, stopped):
    resp = _delete(client, login_as, scene["user_a"], scene["sol_a2"], scene["bundle_own"])
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert stopped == []


def test_organisation_unknown_is_refused(scene, client, login_as, stopped, monkeypatch):
    import app.middleware.tenant_context as tc

    monkeypatch.setattr(tc, "current_org_id", lambda: None)
    resp = _delete(client, login_as, scene["user_a"], scene["sol_a"], scene["bundle_own"])
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert stopped == []


def test_own_solution_and_bundle_stops_preview(scene, client, login_as, stopped):
    resp = _delete(client, login_as, scene["user_a"], scene["sol_a"], scene["bundle_own"])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert stopped == [scene["bundle_own"]]
