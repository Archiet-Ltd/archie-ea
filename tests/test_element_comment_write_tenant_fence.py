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
    user_b = _user(db_session, org_b.id, "b")
    el_a = ArchiMateElement(name="Own Element", type="ApplicationComponent",
                            layer="application", organization_id=org_a.id)
    el_b = ArchiMateElement(name="Foreign Element", type="ApplicationComponent",
                            layer="application", organization_id=org_b.id)
    db_session.add_all([el_a, el_b])
    db_session.flush()
    ids = {
        "org_a": org_a.id, "org_b": org_b.id,
        "user_a": user_a.id, "user_b": user_b.id,
        "el_a": el_a.id, "el_b": el_b.id,
    }
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


def test_foreign_organisation_cannot_comment_on_an_element(scene, client, login_as):
    """A user of organisation B cannot write a comment on organisation A's
    element through the existing create route, and nothing is written."""
    resp = _post(client, login_as, scene["user_b"], scene["el_a"], "intruder")
    assert resp.status_code == 404, resp.get_data(as_text=True)
    assert _count(scene["el_a"]) == 0


def test_foreign_organisation_cannot_list_an_elements_comments(scene, client, login_as):
    """Organisation A's comment on its own element is not listed to
    organisation B's session through the existing list route."""
    from app import db

    _post(client, login_as, scene["user_a"], scene["el_a"], "mine")
    db.session.remove()
    login_as(client, scene["user_b"])
    resp = client.get(f"/archimate/api/elements/{scene['el_a']}/comments")
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_own_organisation_lists_its_comments(scene, client, login_as):
    """A user can list the comments on its own element through the existing
    list route, with the response shape unchanged."""
    from app import db

    _post(client, login_as, scene["user_a"], scene["el_a"], "mine")
    db.session.remove()
    login_as(client, scene["user_a"])
    resp = client.get(f"/archimate/api/elements/{scene['el_a']}/comments")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert len(body["comments"]) == 1
    assert body["comments"][0]["comment_text"] == "mine"
    assert set(body["comments"][0]) == {
        "id", "element_id", "viewpoint_id", "user_id", "user_name",
        "comment_text", "created_at",
    }


def test_foreign_organisation_cannot_comment_on_its_own_element(scene, client, login_as):
    """Organisation B can still comment on its own element; organisation A's
    session cannot see that comment."""
    from app import db

    _post(client, login_as, scene["user_b"], scene["el_b"], "b comment")
    assert _count(scene["el_b"]) == 1
    db.session.remove()
    login_as(client, scene["user_a"])
    resp = client.get(f"/archimate/api/elements/{scene['el_b']}/comments")
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_comment_table_is_tenant_fenced(scene, client, login_as, tenant_ctx):
    """The comment table itself is fenced: organisation B cannot see organisation A's comment."""
    from app import db
    from app.models.archimate_viewpoint import ArchimateElementComment

    # Organisation A creates a comment on its own element
    _post(client, login_as, scene["user_a"], scene["el_a"], "A's comment")
    db.session.remove()

    # Organisation B's session queries the comment table directly
    with tenant_ctx(scene["org_b"]):
        comments_b = db.session.query(ArchimateElementComment).filter_by(element_id=scene["el_a"]).all()
    assert len(comments_b) == 0, "Organisation B should not see organisation A's comment"

    # Organisation A's session still sees its own comment
    with tenant_ctx(scene["org_a"]):
        comments_a = db.session.query(ArchimateElementComment).filter_by(element_id=scene["el_a"]).all()
    assert len(comments_a) == 1, "Organisation A should see its own comment"
    assert comments_a[0].comment_text == "A's comment"
