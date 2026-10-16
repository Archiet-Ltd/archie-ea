"""An organisation cannot write a comment onto another organisation's element.

``archimate_element_comments`` has no organisation column, so the element is
the only tenant anchor. The list route already refuses a foreign element; the
create route accepted any element id and wrote the row. The comment routes are
list (GET) and create (POST) only; there is no edit, delete, like or reply
route to fence.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org_id, label):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    u = User(
        email=f"cmt-{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Cmt", last_name=label, confirmed=True,
        organization_id=org_id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def scene(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement

    org_a = make_org("cmt-a")
    org_b = make_org("cmt-b")
    user_a = _user(db_session, org_a.id, "a")
    el_a = ArchiMateElement(name="Own Element", type="ApplicationComponent",
                            layer="application", organization_id=org_a.id)
    el_b = ArchiMateElement(name="Foreign Element", type="ApplicationComponent",
                            layer="application", organization_id=org_b.id)
    db_session.add_all([el_a, el_b])
    db_session.flush()
    ids = {"user_a": user_a.id, "el_a": el_a.id, "el_b": el_b.id}
    db_session.commit()
    return ids


def _post(client, login_as, user_id, element_id, text="hello"):
    from app import db

    db.session.remove()
    login_as(client, user_id)
    db.session.remove()
    resp = client.post(
        f"/archimate/api/elements/{element_id}/comments", json={"comment_text": text}
    )
    db.session.remove()
    return resp


def _count(element_id):
    from app import db
    from app.models.archimate_viewpoint import ArchimateElementComment

    db.session.remove()
    n = ArchimateElementComment.query.filter_by(element_id=element_id).count()
    db.session.remove()
    return n


def test_comment_on_foreign_element_is_refused_and_not_written(scene, client, login_as):
    resp = _post(client, login_as, scene["user_a"], scene["el_b"], "intruder")
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert _count(scene["el_b"]) == 0


def test_comment_on_unknown_element_is_refused(scene, client, login_as):
    resp = _post(client, login_as, scene["user_a"], 987654321)
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_comment_on_own_element_still_created(scene, client, login_as):
    resp = _post(client, login_as, scene["user_a"], scene["el_a"], "mine")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert _count(scene["el_a"]) == 1


def test_comment_refused_when_organisation_unknown(scene, client, login_as, monkeypatch):
    """With no active organisation the automatic filter is off; the route must
    refuse and write nothing rather than fall through."""
    import app.middleware.tenant_context as tc

    monkeypatch.setattr(tc, "current_org_id", lambda: None)
    resp = _post(client, login_as, scene["user_a"], scene["el_a"], "x")
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert _count(scene["el_a"]) == 0
