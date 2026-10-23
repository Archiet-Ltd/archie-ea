"""Creating, changing and deleting a work package needs a write role.

Every route that writes a work package (the roadmap form routes and the
enterprise list routes) refuses a read-only Viewer with 403, lets a write role
through, answers another organisation's id as a missing one (404, nothing
changed) and still requires the CSRF token.
"""

from __future__ import annotations

import uuid

import pytest

from app import db

pytestmark = pytest.mark.usefixtures("db_session")

RM = "/capability-map/api/roadmap"
EN = "/enterprise/api/work-packages"


def _user(db_session, org, label, role_name="Editor"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"wpw-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
        role=Role.query.filter_by(name=role_name).first(),
    )
    user.password = "Test!12345"
    db_session.add(user)
    db_session.flush()
    return user


def _wp(db_session, org, name=None):
    from app.services import work_package_service

    wp = work_package_service.create_work_package(
        organization_id=org.id, name=name or f"WP {uuid.uuid4().hex[:6]}")
    db_session.flush()
    return wp


def _gap(db_session, org):
    from app.services.gap_register_service import create_gap

    gap, _ = create_gap(org.id, f"Gap {uuid.uuid4().hex[:6]}", gap_type="coverage")
    db_session.flush()
    return gap


@pytest.fixture
def world(db_session, make_org):
    a, b = make_org("wpw-a"), make_org("wpw-b")
    return {
        "a": a, "b": b,
        "editor_a": _user(db_session, a, "edita"),
        "viewer_a": _user(db_session, a, "viewa", role_name="Viewer"),
        "wp_a": _wp(db_session, a),
        "wp_b": _wp(db_session, b),
        "gap_a": _gap(db_session, a),
    }


def _routes(w):
    """(label, method, url, json-body) for every work package write route."""
    wp, gap = w["wp_a"], w["gap_a"]
    return [
        ("roadmap create", "post", f"{RM}/work-packages", {"name": "New one"}),
        ("roadmap create for gap", "post", f"{RM}/gaps/{gap.id}/work-packages", {"name": "For gap"}),
        ("roadmap update", "put", f"{RM}/work-packages/{wp.id}", {"name": "Renamed"}),
        ("roadmap child", "post", f"{RM}/work-packages/{wp.id}/children", {"name": "Child"}),
        ("roadmap delete", "delete", f"{RM}/work-packages/{wp.id}", None),
        ("list create", "post", EN, {"name": "Listed"}),
        ("list update", "patch", f"{EN}/{wp.id}", {"name": "Renamed"}),
        ("list bulk delete", "delete", f"{EN}/bulk", {"ids": [wp.id]}),
        ("list delete", "delete", f"{EN}/{wp.id}", None),
    ]


def _send(client, login_as, user, method, url, body):
    login_as(client, user)
    kwargs = {"json": body} if body is not None else {}
    return getattr(client, method)(url, **kwargs)


def _names(db_session, org):
    # Raw SQL: the ORM tenant filter follows the last signed-in caller, not ``org``.
    rows = db_session.execute(
        db.text("SELECT name FROM unified_work_packages WHERE organization_id = :o ORDER BY name"),
        {"o": org.id},
    )
    return [r[0] for r in rows]


ROUTE_LABELS = [r[0] for r in _routes({"wp_a": type("W", (), {"id": 1}), "gap_a": type("G", (), {"id": 1})})]


@pytest.mark.parametrize("label", ROUTE_LABELS)
def test_viewer_is_refused_and_nothing_changes(db_session, world, client, login_as, label):
    before = _names(db_session, world["a"])
    row = next(r for r in _routes(world) if r[0] == label)
    resp = _send(client, login_as, world["viewer_a"], *row[1:])
    assert resp.status_code == 403, (label, resp.status_code, resp.get_data(as_text=True)[:200])
    assert _names(db_session, world["a"]) == before


@pytest.mark.parametrize("label", ROUTE_LABELS)
def test_write_role_is_not_refused(db_session, world, client, login_as, label):
    row = next(r for r in _routes(world) if r[0] == label)
    resp = _send(client, login_as, world["editor_a"], *row[1:])
    assert resp.status_code in (200, 201), (label, resp.status_code, resp.get_data(as_text=True)[:300])


def test_viewer_still_reads(db_session, world, client, login_as):
    login_as(client, world["viewer_a"])
    for url in (EN, f"{RM}/work-packages", f"{RM}/work-packages/{world['wp_a'].id}"):
        assert client.get(url).status_code == 200, url


def test_other_organisations_id_is_404_and_unchanged(db_session, world, client, login_as):
    wp_b = world["wp_b"]
    before_b = _names(db_session, world["b"])
    for method, url, body in (
        ("put", f"{RM}/work-packages/{wp_b.id}", {"name": "Hijack"}),
        ("post", f"{RM}/work-packages/{wp_b.id}/children", {"name": "Hijack"}),
        ("delete", f"{RM}/work-packages/{wp_b.id}", None),
        ("patch", f"{EN}/{wp_b.id}", {"name": "Hijack"}),
        ("delete", f"{EN}/{wp_b.id}", None),
    ):
        resp = _send(client, login_as, world["editor_a"], method, url, body)
        assert resp.status_code == 404, (method, url, resp.status_code)
    resp = _send(client, login_as, world["editor_a"], "delete", f"{EN}/bulk", {"ids": [wp_b.id]})
    assert resp.get_json()["deleted"] == 0
    assert _names(db_session, world["b"]) == before_b


def test_writes_still_need_a_csrf_token(app, db_session, world, client, login_as):
    before = _names(db_session, world["a"])
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        for label, method, url, body in _routes(world):
            resp = _send(client, login_as, world["editor_a"], method, url, body)
            assert resp.status_code == 400, (label, resp.status_code)
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert _names(db_session, world["a"]) == before
