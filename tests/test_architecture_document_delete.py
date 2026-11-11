"""Deleting an application document: the owner's organisation may, no other may, a viewer may not.

Two real organisations, each with an application and a stored document file. Every request is made
as a signed-in user through the real route.
"""
from __future__ import annotations

import os
import uuid

import pytest

from tests.test_compliance_policies_org_fence import _user

URL = "/dashboard/api/applications/{app_id}/architecture/documents/{doc_id}"


def _member(db_session, org, org_role):
    from app.models.org_role import OrgRole

    user = _user(db_session, org, role_name="User")
    db_session.add(OrgRole(organization_id=org.id, user_id=user.id, role=org_role))
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org, tmp_path):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.miscellaneous import ArchitectureDocument

    class World:
        pass

    w = World()
    tag = uuid.uuid4().hex[:6]
    w.org_a, w.org_b = make_org("dda"), make_org("ddb")
    w.editor_a = _member(db_session, w.org_a, "architect")
    w.viewer_a = _member(db_session, w.org_a, "viewer")
    w.editor_b = _member(db_session, w.org_b, "architect")
    for key, org, user in (("a", w.org_a, w.editor_a), ("b", w.org_b, w.editor_b)):
        application = ApplicationComponent(name=f"Billing {key} {tag}", organization_id=org.id)
        db_session.add(application)
        db_session.flush()
        path = tmp_path / f"design-{key}.pdf"
        path.write_bytes(b"%PDF-1.4 " + key.encode())
        doc = ArchitectureDocument(
            application_id=application.id,
            filename=f"design-{key}.pdf",
            file_path=str(path),
            mime_type="application/pdf",
            uploaded_by_id=user.id,
        )
        db_session.add(doc)
        db_session.flush()
        setattr(w, f"app_{key}", application)
        setattr(w, f"doc_{key}", doc)
        setattr(w, f"path_{key}", str(path))
    db_session.commit()
    return w


def _exists(db_session, doc_id):
    from app.models.miscellaneous import ArchitectureDocument

    db_session.expire_all()
    return (
        db_session.query(ArchitectureDocument.id).filter(ArchitectureDocument.id == doc_id).first()
        is not None
    )


def test_an_editor_deletes_their_own_organisations_document(world, client, login_as, db_session):
    login_as(client, world.editor_a)
    resp = client.delete(URL.format(app_id=world.app_a.id, doc_id=world.doc_a.id))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["success"] is True
    assert not _exists(db_session, world.doc_a.id)
    assert not os.path.exists(world.path_a)
    assert _exists(db_session, world.doc_b.id)
    assert os.path.exists(world.path_b)


def test_another_organisation_gets_not_found_and_nothing_is_deleted(
    world, client, login_as, db_session
):
    login_as(client, world.editor_b)
    resp = client.delete(URL.format(app_id=world.app_a.id, doc_id=world.doc_a.id))
    assert resp.status_code == 404
    assert _exists(db_session, world.doc_a.id)
    assert os.path.exists(world.path_a)


def test_a_document_under_the_wrong_application_is_not_found(world, client, login_as, db_session):
    login_as(client, world.editor_a)
    resp = client.delete(URL.format(app_id=world.app_b.id, doc_id=world.doc_a.id))
    assert resp.status_code == 404
    assert _exists(db_session, world.doc_a.id)


def test_a_viewer_is_refused_and_nothing_is_deleted(world, client, login_as, db_session):
    login_as(client, world.viewer_a)
    resp = client.delete(URL.format(app_id=world.app_a.id, doc_id=world.doc_a.id))
    assert resp.status_code == 403
    assert _exists(db_session, world.doc_a.id)
    assert os.path.exists(world.path_a)
