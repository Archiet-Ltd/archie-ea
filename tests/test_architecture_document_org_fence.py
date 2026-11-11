"""Application documents: list, upload and download answer only inside the document's organisation.

Two real organisations, each with an application and a stored document file. Every request is made
as a signed-in user through the real route.
"""
from __future__ import annotations

import io
import uuid

import pytest

from tests.test_compliance_policies_org_fence import _user

BASE = "/dashboard/api/applications/{app_id}/architecture/documents"
ONE = BASE + "/{doc_id}"


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
    w.org_a, w.org_b = make_org("dfa"), make_org("dfb")
    w.editor_a = _member(db_session, w.org_a, "architect")
    w.viewer_a = _member(db_session, w.org_a, "viewer")
    w.editor_b = _member(db_session, w.org_b, "architect")
    for key, org, user in (("a", w.org_a, w.editor_a), ("b", w.org_b, w.editor_b)):
        application = ApplicationComponent(name=f"Billing {key} {tag}", organization_id=org.id)
        db_session.add(application)
        db_session.flush()
        path = tmp_path / f"design-{key}.pdf"
        path.write_bytes(b"%PDF-1.4 contents-of-" + key.encode())
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
    db_session.commit()
    return w


def _count(db_session, application_id):
    from app.models.miscellaneous import ArchitectureDocument

    db_session.expire_all()
    return ArchitectureDocument.query.filter_by(application_id=application_id).count()


# ---- download ----

def test_a_person_downloads_their_own_organisations_document(world, client, login_as):
    login_as(client, world.editor_a)
    resp = client.get(ONE.format(app_id=world.app_a.id, doc_id=world.doc_a.id))
    assert resp.status_code == 200
    assert resp.data == b"%PDF-1.4 contents-of-a"


def test_a_viewer_downloads_their_own_organisations_document(world, client, login_as):
    login_as(client, world.viewer_a)
    resp = client.get(ONE.format(app_id=world.app_a.id, doc_id=world.doc_a.id))
    assert resp.status_code == 200
    assert resp.data == b"%PDF-1.4 contents-of-a"


def test_another_organisation_cannot_download_by_the_owners_ids(world, client, login_as):
    login_as(client, world.editor_b)
    resp = client.get(ONE.format(app_id=world.app_a.id, doc_id=world.doc_a.id))
    assert resp.status_code == 404
    assert b"contents-of-a" not in resp.data


def test_another_organisation_cannot_download_under_its_own_application_id(
    world, client, login_as
):
    login_as(client, world.editor_b)
    resp = client.get(ONE.format(app_id=world.app_b.id, doc_id=world.doc_a.id))
    assert resp.status_code == 404
    assert b"contents-of-a" not in resp.data


# ---- list ----

def test_a_person_lists_their_own_applications_documents(world, client, login_as):
    login_as(client, world.editor_a)
    resp = client.get(BASE.format(app_id=world.app_a.id))
    assert resp.status_code == 200
    assert [d["filename"] for d in resp.get_json()["documents"]] == ["design-a.pdf"]


def test_another_organisation_cannot_list_the_documents(world, client, login_as):
    login_as(client, world.editor_b)
    resp = client.get(BASE.format(app_id=world.app_a.id))
    assert resp.status_code == 404
    assert b"design-a.pdf" not in resp.data


# ---- upload ----

def _upload(client, app_id):
    return client.post(
        BASE.format(app_id=app_id),
        data={"file": (io.BytesIO(b"%PDF-1.4 planted"), "planted.pdf", "application/pdf")},
        content_type="multipart/form-data",
    )


def test_a_person_uploads_to_their_own_application(world, client, login_as, db_session, app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    login_as(client, world.editor_a)
    resp = _upload(client, world.app_a.id)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert _count(db_session, world.app_a.id) == 2


def test_another_organisation_cannot_upload_to_the_application(
    world, client, login_as, db_session, app, tmp_path
):
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    login_as(client, world.editor_b)
    resp = _upload(client, world.app_a.id)
    assert resp.status_code == 404
    assert _count(db_session, world.app_a.id) == 1
    assert not (tmp_path / "uploads").exists()


def test_a_viewer_cannot_upload(world, client, login_as, db_session, app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    login_as(client, world.viewer_a)
    resp = _upload(client, world.app_a.id)
    assert resp.status_code == 403
    assert _count(db_session, world.app_a.id) == 1
