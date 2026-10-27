"""A viewer the permission matrix grants "edit application" can save an application; without the grant the
save is refused (the applications blueprint's general write floor no longer overrides the matrix)."""
from __future__ import annotations

import uuid

import pytest


def _viewer_and_application(db_session, org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import Role, User

    Role.insert_roles()
    viewer_role = Role.query.filter_by(name="Viewer").first()
    assert viewer_role is not None and viewer_role.permissions == 0
    viewer = User(email="viewer-%s@example.com" % uuid.uuid4().hex[:8], organization_id=org.id,
                  confirmed=True, role=viewer_role)
    viewer.password = "Viewer!Test2026"
    application = ApplicationComponent(name="Billing", organization_id=org.id)
    db_session.add_all([viewer, application])
    db_session.commit()
    return viewer, application


def _save(client, login_as, viewer, application):
    login_as(client, viewer)
    return client.post("/applications/%d/edit" % application.id, data={"name": "Billing renamed"})


def test_a_viewer_without_the_grant_cannot_save(client, db_session, make_org, login_as):
    org = make_org("viewer-no-grant")
    viewer, application = _viewer_and_application(db_session, org)
    assert _save(client, login_as, viewer, application).status_code == 403


def test_a_viewer_with_the_edit_grant_saves(client, db_session, make_org, login_as):
    from app.core.auth.permissions import role_key, set_grant

    org = make_org("viewer-grant")
    viewer, application = _viewer_and_application(db_session, org)
    set_grant(org.id, "role", role_key(viewer, org.id), "application", "edit", True)
    db_session.commit()

    resp = _save(client, login_as, viewer, application)

    assert resp.status_code in (200, 302), resp.get_data(as_text=True)[:300]
    db_session.refresh(application)
    assert application.name == "Billing renamed"
