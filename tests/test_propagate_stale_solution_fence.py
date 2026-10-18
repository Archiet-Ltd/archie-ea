"""propagate-stale refuses a solution outside the caller's organisation.

The route is read-only, but it resolves a solution's element ids from the
solution-to-element junction, which has no organisation column. Relationships
and elements are tenant filtered, so another organisation's relationship ids and
element names do not come back; what the route did give a foreign caller was an
answer that depended on the foreign solution's membership (a normal reply for a
member element, a different one for a non-member). It now answers 404 for any
solution not in the caller's active organisation.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org_id, label):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    u = User(
        email=f"stl-{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Stl", last_name=label, confirmed=True,
        organization_id=org_id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def scene(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.solution_models import Solution, SolutionArchiMateElement

    org_a = make_org("stl-a")
    org_b = make_org("stl-b")
    user_a = _user(db_session, org_a.id, "a")

    def _chain(org, tag):
        sol = Solution(name=f"{tag} Solution", organization_id=org.id)
        e1 = ArchiMateElement(name=f"{tag} Source", type="ApplicationComponent",
                              layer="application", organization_id=org.id)
        e2 = ArchiMateElement(name=f"{tag} Downstream MARKER", type="ApplicationComponent",
                              layer="application", organization_id=org.id)
        db_session.add_all([sol, e1, e2])
        db_session.flush()
        db_session.add(ArchiMateRelationship(type="serving", source_id=e1.id,
                                             target_id=e2.id, organization_id=org.id))
        for e in (e1, e2):
            db_session.add(SolutionArchiMateElement(
                solution_id=sol.id, element_id=e.id, element_table="archimate_elements",
                layer_type="application"))
        db_session.flush()
        return sol.id, e1.id, e2.id

    sol_a, a1, a2 = _chain(org_a, "Own")
    sol_b, b1, b2 = _chain(org_b, "Foreign")
    ids = {"user_a": user_a.id, "sol_a": sol_a, "a1": a1, "a2": a2,
           "sol_b": sol_b, "b1": b1, "b2": b2}
    db_session.commit()
    return ids


def _post(client, login_as, user_id, solution_id, element_id):
    from app import db

    db.session.remove()
    login_as(client, user_id)
    db.session.remove()
    resp = client.post(f"/api/solutions/{solution_id}/elements/{element_id}/propagate-stale")
    db.session.remove()
    return resp


def test_foreign_solution_is_404(scene, client, login_as):
    resp = _post(client, login_as, scene["user_a"], scene["sol_b"], scene["b1"])
    assert resp.status_code == 404, resp.get_data(as_text=True)
    body = resp.get_data(as_text=True)
    assert "stale_ids" not in body and "source_element_id" not in body


def test_foreign_data_never_in_body(scene, client, login_as):
    """Whatever the status, no foreign relationship target id or element name."""
    resp = _post(client, login_as, scene["user_a"], scene["sol_b"], scene["b1"])
    body = resp.get_data(as_text=True)
    assert "MARKER" not in body
    assert str(scene["b2"]) not in resp.get_json().get("stale_ids", [])


def test_foreign_solution_with_own_element_is_404(scene, client, login_as):
    resp = _post(client, login_as, scene["user_a"], scene["sol_b"], scene["a1"])
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_organisation_unknown_is_404(scene, client, login_as, monkeypatch):
    import app.middleware.tenant_context as tc

    monkeypatch.setattr(tc, "current_org_id", lambda: None)
    resp = _post(client, login_as, scene["user_a"], scene["sol_a"], scene["a1"])
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_own_solution_still_propagates(scene, client, login_as):
    resp = _post(client, login_as, scene["user_a"], scene["sol_a"], scene["a1"])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["stale_ids"] == [scene["a2"]]
    assert [e["name"] for e in data["stale_elements"]] == ["Own Downstream MARKER"]
