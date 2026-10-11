"""Cross-organisation isolation for saved-viewpoint version snapshots.

``ArchimateViewpointSnapshot`` carries no ``organization_id`` of its own (it
is reached only through its parent ``SavedDiagram``, which IS tenant-scoped).
Both the read (``GET .../snapshots/<sid>``) and restore
(``POST .../snapshots/<sid>/restore``) routes loaded the snapshot with a bare
``db.session.get(ArchimateViewpointSnapshot, sid)`` and then checked only that
the snapshot's own stored ``viewpoint_id`` equalled the ``vp_id`` from the
URL — an internal consistency check, not an ownership check. Any
authenticated user from any organisation who supplied another organisation's
own (vp_id, sid) pair (its own, correctly-matching pair — not a mismatched
guess) could read, and on the restore route overwrite, that organisation's
diagram.

Fix: load and verify the viewpoint's ownership FIRST
(``_get_saved_diagram_scoped``), then load the snapshot scoped to that
already-verified viewpoint in one filtered query
(``ArchimateViewpointSnapshot.query.filter_by(id=sid, viewpoint_id=vp_id)``).

The export route (``GET .../export``) loads its ``SavedDiagram`` the same
unscoped way (``db.session.get(SavedDiagram, vp_id)`` inside
``load_viewpoint_dict``). ``SavedDiagram`` IS a ``TenantMixin``, so this file
proves directly whether a genuinely fresh request (nothing already cached in
the session's identity map) gets the tenant predicate applied on that
SELECT, rather than assuming it either way. The matching ownership check is
added there too.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org_id, label):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    u = User(
        email=f"snap-{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Snap", last_name=label, confirmed=True,
        organization_id=org_id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def scene(db_session, make_org):
    """Org A owns a diagram with two elements, one relationship and a
    snapshot capturing only the first element. Returns plain ids only: test
    bodies issue requests through ``_request``, which calls
    ``db.session.remove()`` for a genuinely fresh session per request (as
    production gets), and that detaches any ORM object still referenced from
    setup — reading an unloaded attribute off a detached instance later
    raises DetachedInstanceError. A commit (here a SAVEPOINT release; the
    outer fixture transaction still discards everything at teardown) makes
    the rows durable to that session churn, the same way
    tests/_isolation_sweep.py commits its seeded rows before the first
    db.session.remove().
    """
    from app.models.archimate_core import (
        ArchiMateElement, ArchiMateRelationship, SavedDiagram,
        SavedDiagramElement, SavedDiagramRelationship,
    )
    from app.models.archimate_viewpoint import ArchimateViewpointSnapshot

    org_a = make_org("snap-a")
    org_b = make_org("snap-b")
    user_a = _user(db_session, org_a.id, "a")
    user_b = _user(db_session, org_b.id, "b")

    el1 = ArchiMateElement(name="Marker Element One", type="ApplicationComponent",
                            layer="application", organization_id=org_a.id)
    el2 = ArchiMateElement(name="Marker Element Two", type="ApplicationComponent",
                            layer="application", organization_id=org_a.id)
    db_session.add_all([el1, el2])
    db_session.flush()

    rel = ArchiMateRelationship(type="association", source_id=el1.id, target_id=el2.id,
                                 organization_id=org_a.id)
    db_session.add(rel)
    db_session.flush()

    diagram = SavedDiagram(name="Org A Secret Diagram", organization_id=org_a.id,
                            created_by_id=user_a.id)
    db_session.add(diagram)
    db_session.flush()

    pos1 = SavedDiagramElement(diagram_id=diagram.id, element_id=el1.id,
                                position_x=10, position_y=10)
    pos2 = SavedDiagramElement(diagram_id=diagram.id, element_id=el2.id,
                                position_x=20, position_y=20)
    relpos = SavedDiagramRelationship(diagram_id=diagram.id, relationship_id=rel.id)
    db_session.add_all([pos1, pos2, relpos])
    db_session.flush()

    snapshot_data = {
        "viewpoint_name": diagram.name,
        "viewpoint_type": None,
        "solution_id": None,
        "elements": [
            {"id": el1.id, "name": el1.name, "type": el1.type, "layer": el1.layer,
             "x": 10, "y": 10, "width": 180, "height": 64, "rendering_mode": "black_box"},
        ],
        "relationships": [],
    }
    snapshot = ArchimateViewpointSnapshot(
        viewpoint_id=diagram.id,
        name="Org A Snapshot MARKERXYZ",
        snapshot_json=json.dumps(snapshot_data),
    )
    db_session.add(snapshot)
    db_session.flush()

    ids = {
        "org_a": org_a.id, "org_b": org_b.id,
        "user_a": user_a.id, "user_b": user_b.id,
        "diagram": diagram.id, "snapshot": snapshot.id,
        "el1": el1.id, "el2": el2.id, "rel": rel.id,
    }
    db_session.commit()
    return ids


def _url_get(vp_id, sid):
    return f"/archimate/api/saved-viewpoints/{vp_id}/snapshots/{sid}"


def _url_restore(vp_id, sid):
    return f"/archimate/api/saved-viewpoints/{vp_id}/snapshots/{sid}/restore"


def _request(client, login_as, user_id, method, url, **kwargs):
    """One request with a genuinely fresh session, as production gets one per
    request — mirrors tests/_isolation_sweep.py::_request exactly. The
    db_session fixture holds one app context open for the whole test, so
    without discarding the session (not just expiring it) a request would
    reuse the identity map that seeded the fixture rows, and Session.get()
    would answer from that cache rather than issuing a real, tenant-filtered
    SELECT — a leak that would exist only in the test, not in production.
    ``login_as`` accepts a raw user id as well as a user object.
    """
    from app import db

    db.session.remove()
    login_as(client, user_id)
    db.session.remove()
    resp = client.open(url, method=method, **kwargs)
    db.session.remove()
    return resp


def _snapshot_diagram_rows(diagram_id):
    """Read the diagram's current element/relationship junction rows, fresh."""
    from app import db
    from app.models.archimate_core import SavedDiagramElement, SavedDiagramRelationship

    db.session.remove()
    elements = sorted(
        (r.element_id, r.position_x, r.position_y)
        for r in SavedDiagramElement.query.filter_by(diagram_id=diagram_id).all()
    )
    rels = sorted(
        r.relationship_id
        for r in SavedDiagramRelationship.query.filter_by(diagram_id=diagram_id).all()
    )
    db.session.remove()
    return elements, rels


# ─────────────────────────── GET snapshot ───────────────────────────


def test_get_snapshot_cross_org_is_refused(scene, client, login_as):
    """The read leak: org B must not be able to read org A's snapshot, even
    when it supplies org A's own correctly-matching (vp_id, sid) pair."""
    resp = _request(client, login_as, scene["user_b"], "GET",
                     _url_get(scene["diagram"], scene["snapshot"]))

    assert resp.status_code == 404, (
        f"expected 404, got {resp.status_code}: {resp.get_data(as_text=True)}"
    )
    body_text = resp.get_data(as_text=True)
    assert "MARKERXYZ" not in body_text, "org A's snapshot name leaked to org B"


def test_get_snapshot_owner_succeeds(scene, client, login_as):
    """Regression: the owning organisation can still read its own snapshot."""
    resp = _request(client, login_as, scene["user_a"], "GET",
                     _url_get(scene["diagram"], scene["snapshot"]))

    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["name"] == "Org A Snapshot MARKERXYZ"
    assert data["data"]["elements"][0]["id"] == scene["el1"]


# ─────────────────────────── POST restore ───────────────────────────


def test_restore_snapshot_cross_org_is_refused_and_data_unchanged(scene, client, login_as):
    """The destructive leak: org B must not be able to restore (and thereby
    overwrite) org A's diagram, and org A's rows must be byte-for-byte
    unchanged after org B's attempt."""
    before_elements, before_rels = _snapshot_diagram_rows(scene["diagram"])
    assert before_elements == sorted([(scene["el1"], 10, 10), (scene["el2"], 20, 20)])
    assert before_rels == [scene["rel"]]

    resp = _request(client, login_as, scene["user_b"], "POST",
                     _url_restore(scene["diagram"], scene["snapshot"]))

    assert resp.status_code == 404, (
        f"expected 404, got {resp.status_code}: {resp.get_data(as_text=True)}"
    )

    after_elements, after_rels = _snapshot_diagram_rows(scene["diagram"])
    assert after_elements == before_elements, (
        "TENANT LEAK: org B's restore attempt changed org A's diagram elements"
    )
    assert after_rels == before_rels, (
        "TENANT LEAK: org B's restore attempt changed org A's diagram relationships"
    )


def test_restore_snapshot_owner_succeeds(scene, client, login_as):
    """Regression: the owning organisation can still restore its own snapshot.

    The snapshot captures only el1's position; restoring must leave exactly
    that one element position (and drop el2's — this is what a real restore
    is supposed to do) for the owner.
    """
    resp = _request(client, login_as, scene["user_a"], "POST",
                     _url_restore(scene["diagram"], scene["snapshot"]))

    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["restored"] is True
    assert body["element_count"] == 1

    after_elements, _after_rels = _snapshot_diagram_rows(scene["diagram"])
    assert after_elements == [(scene["el1"], 10, 10)]


# ─────────────────────────── export (same-class check) ───────────────────────────


@pytest.mark.parametrize("fmt", ["archimate_exchange", "mermaid", "lucid", "archi"])
def test_export_saved_viewpoint_cross_org_is_refused(scene, client, login_as, fmt):
    """Same class of check as the snapshot routes: the export route loads its
    SavedDiagram via a bare db.session.get() (through load_viewpoint_dict)
    rather than the tenant-scoped helper. SavedDiagram IS a TenantMixin, so
    this proves whether a genuinely fresh request (nothing already loaded for
    this id) gets the tenant filter applied, rather than assuming it from the
    general do_orm_execute mechanism."""
    resp = _request(
        client, login_as, scene["user_b"], "GET",
        f"/archimate/api/saved-viewpoints/{scene['diagram']}/export?format={fmt}",
    )

    assert resp.status_code == 404, (
        f"format={fmt}: expected 404, got {resp.status_code}: "
        f"{resp.get_data(as_text=True)[:500]}"
    )


@pytest.mark.parametrize("fmt", ["archimate_exchange", "mermaid", "lucid", "archi"])
def test_export_saved_viewpoint_owner_succeeds(scene, client, login_as, fmt):
    """Regression: the owner can still export its own diagram in every format."""
    resp = _request(
        client, login_as, scene["user_a"], "GET",
        f"/archimate/api/saved-viewpoints/{scene['diagram']}/export?format={fmt}",
    )

    assert resp.status_code == 200, (
        f"format={fmt}: expected 200, got {resp.status_code}: "
        f"{resp.get_data(as_text=True)[:500]}"
    )
