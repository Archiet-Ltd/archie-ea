"""Unlinking an element from, or tracing an element in, another organisation's solution.

The solution-to-element junction tables (solution_elements and
solution_archimate_elements) have no organisation column, so a delete keyed on
solution id + element id removed a foreign organisation's row for any caller.
The trace route walked the same junction to scope its answer. All three routes
now resolve the solution in the caller's active organisation first.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org_id, label):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    u = User(
        email=f"sef-{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Sef", last_name=label, confirmed=True,
        organization_id=org_id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def scene(db_session, make_org):
    """Org A is the caller. Org B owns the solution the caller aims at."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.solution_element import SolutionElement
    from app.models.solution_models import Solution, SolutionArchiMateElement

    org_a = make_org("sef-a")
    org_b = make_org("sef-b")
    user_a = _user(db_session, org_a.id, "a")

    def _side(org, tag):
        sol = Solution(name=f"{tag} Solution", organization_id=org.id)
        e1 = ArchiMateElement(name=f"{tag} Source MARKER", type="ApplicationComponent",
                              layer="application", organization_id=org.id)
        e2 = ArchiMateElement(name=f"{tag} Downstream", type="ApplicationComponent",
                              layer="application", organization_id=org.id)
        db_session.add_all([sol, e1, e2])
        db_session.flush()
        db_session.add(ArchiMateRelationship(type="serving", source_id=e1.id,
                                             target_id=e2.id, organization_id=org.id))
        plain = SolutionElement(solution_id=sol.id, archimate_element_id=e1.id, layer="application")
        archi = SolutionArchiMateElement(solution_id=sol.id, element_id=e1.id,
                                         element_table="archimate_elements",
                                         layer_type="application")
        db_session.add_all([plain, archi,
                            SolutionArchiMateElement(solution_id=sol.id, element_id=e2.id,
                                                     element_table="archimate_elements",
                                                     layer_type="application")])
        db_session.flush()
        return {"sol": sol.id, "e1": e1.id, "e2": e2.id, "plain": plain.id, "archi": archi.id}

    own = _side(org_a, "Own")
    foreign = _side(org_b, "Foreign")
    ids = {"user_a": user_a.id, "own": own, "foreign": foreign}
    db_session.commit()
    return ids


def _call(client, login_as, scene, method, path):
    from app import db

    db.session.remove()
    login_as(client, scene["user_a"])
    db.session.remove()
    resp = getattr(client, method)(path)
    db.session.remove()
    return resp


def _exists(model, row_id):
    from app import db

    db.session.remove()
    # Raw SQL: the ORM read is tenant-filtered to whoever is signed in, and these tests ask whether
    # the row survived an attempt by ANOTHER organisation.
    return db.session.execute(
        db.text(f'SELECT 1 FROM "{model.__tablename__}" WHERE id = :i'), {"i": row_id}  # nosec B608 - model table name
    ).first() is not None


def _unknown_org(monkeypatch):
    import app.middleware.tenant_context as tc

    monkeypatch.setattr(tc, "current_org_id", lambda: None)


# DELETE /api/solutions/<sid>/archimate-elements/<eid>

def test_unlink_archimate_element_on_foreign_solution_keeps_the_row(scene, client, login_as):
    from app.models.solution_models import SolutionArchiMateElement

    f = scene["foreign"]
    resp = _call(client, login_as, scene, "delete",
                 f"/api/solutions/{f['sol']}/archimate-elements/{f['e1']}")
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert _exists(SolutionArchiMateElement, f["archi"])


def test_unlink_archimate_element_unknown_organisation_keeps_the_row(scene, client, login_as, monkeypatch):
    from app.models.solution_models import SolutionArchiMateElement

    _unknown_org(monkeypatch)
    own = scene["own"]
    resp = _call(client, login_as, scene, "delete",
                 f"/api/solutions/{own['sol']}/archimate-elements/{own['e1']}")
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert _exists(SolutionArchiMateElement, own["archi"])


def test_unlink_archimate_element_on_own_solution_still_works(scene, client, login_as):
    from app.models.solution_models import SolutionArchiMateElement

    own = scene["own"]
    resp = _call(client, login_as, scene, "delete",
                 f"/api/solutions/{own['sol']}/archimate-elements/{own['e1']}")
    assert resp.status_code == 204, resp.get_data(as_text=True)
    assert not _exists(SolutionArchiMateElement, own["archi"])


# DELETE /api/solutions/<sid>/elements/<eid>

def test_remove_element_on_foreign_solution_keeps_the_row(scene, client, login_as):
    from app.models.solution_element import SolutionElement

    f = scene["foreign"]
    resp = _call(client, login_as, scene, "delete",
                 f"/api/solutions/{f['sol']}/elements/{f['plain']}")
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert _exists(SolutionElement, f["plain"])


def test_remove_element_unknown_organisation_keeps_the_row(scene, client, login_as, monkeypatch):
    from app.models.solution_element import SolutionElement

    _unknown_org(monkeypatch)
    own = scene["own"]
    resp = _call(client, login_as, scene, "delete",
                 f"/api/solutions/{own['sol']}/elements/{own['plain']}")
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert _exists(SolutionElement, own["plain"])


def test_remove_element_on_own_solution_still_works(scene, client, login_as):
    from app.models.solution_element import SolutionElement

    own = scene["own"]
    resp = _call(client, login_as, scene, "delete",
                 f"/api/solutions/{own['sol']}/elements/{own['plain']}")
    assert resp.status_code == 204, resp.get_data(as_text=True)
    assert not _exists(SolutionElement, own["plain"])


# GET /api/solutions/<sid>/elements/<eid>/trace

def test_trace_on_foreign_solution_is_404_with_no_foreign_data(scene, client, login_as):
    f = scene["foreign"]
    resp = _call(client, login_as, scene, "get",
                 f"/api/solutions/{f['sol']}/elements/{f['e2']}/trace")
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert "MARKER" not in resp.get_data(as_text=True)


def test_trace_on_foreign_solution_with_own_element_is_404(scene, client, login_as):
    resp = _call(client, login_as, scene, "get",
                 f"/api/solutions/{scene['foreign']['sol']}/elements/{scene['own']['e2']}/trace")
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_trace_unknown_organisation_is_404(scene, client, login_as, monkeypatch):
    _unknown_org(monkeypatch)
    own = scene["own"]
    resp = _call(client, login_as, scene, "get",
                 f"/api/solutions/{own['sol']}/elements/{own['e2']}/trace")
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_trace_on_own_solution_still_answers(scene, client, login_as):
    own = scene["own"]
    resp = _call(client, login_as, scene, "get",
                 f"/api/solutions/{own['sol']}/elements/{own['e2']}/trace")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert [c["id"] for c in resp.get_json()["chain"]] == [own["e1"]]
