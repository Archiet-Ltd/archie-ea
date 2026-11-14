"""The Permissions tab lists work packages with their defaults, and a saved
override changes what the work package routes allow."""

import uuid

import pytest

from tests.test_permission_matrix_slice1 import _org, _user


@pytest.fixture
def client(app):
    return app.test_client()


def test_tab_lists_work_packages_with_defaults_and_override_is_honoured(
    app, db_session, login_as, client
):
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service

    org = _org(db_session, "wp")
    other = _org(db_session, "wpo")
    admin = _user(db_session, org, "org_admin")
    architect = _user(db_session, org, "architect")
    other_architect = _user(db_session, other, "architect")
    db_session.commit()

    login_as(client, admin)
    page = client.get("/admin/team?tab=permissions")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-testid="permissions-grid-work_package"' in html
    assert "Delete work packages" in html

    def _new():
        wp = work_package_service.create_work_package(
            organization_id=org.id, name=f"WP {uuid.uuid4().hex[:6]}"
        )
        db_session.commit()
        return wp.id

    def _gone(wp_id):
        db_session.expire_all()
        return db_session.get(UnifiedWorkPackage, wp_id) is None

    # Default: an architect may delete a work package.
    first = _new()
    login_as(client, architect)
    assert client.delete(f"/enterprise/api/work-packages/{first}").status_code in (200, 302)
    assert _gone(first)

    # Administrator switches architect delete off for this organisation.
    login_as(client, admin)
    resp = client.post(
        "/admin/team/permissions/role",
        data={
            "role": "architect",
            "object_type": "work_package",
            "action": "delete",
            "allowed": "0",
        },
    )
    assert resp.status_code in (200, 302)

    second = _new()
    login_as(client, architect)
    assert client.delete(f"/enterprise/api/work-packages/{second}").status_code == 403
    assert not _gone(second)

    # The override is per organisation: another organisation's architect is unaffected.
    third = work_package_service.create_work_package(
        organization_id=other.id, name=f"WP {uuid.uuid4().hex[:6]}"
    ).id
    db_session.commit()
    login_as(client, other_architect)
    assert client.delete(f"/enterprise/api/work-packages/{third}").status_code in (200, 302)
    assert _gone(third)


def test_deliverable_writes_follow_the_work_package_override(
    app, db_session, login_as, client
):
    org = _org(db_session, "wpd")
    admin = _user(db_session, org, "org_admin")
    architect = _user(db_session, org, "architect")
    db_session.commit()

    for action in ("edit", "delete"):
        login_as(client, admin)
        client.post(
            "/admin/team/permissions/role",
            data={
                "role": "architect",
                "object_type": "work_package",
                "action": action,
                "allowed": "0",
            },
        )

    login_as(client, architect)
    put = client.put("/api/roadmap/deliverables/1", json={"name": "renamed"})
    assert put.status_code == 403, put.get_data(as_text=True)
    gone = client.delete("/api/roadmap/deliverables/1")
    assert gone.status_code == 403, gone.get_data(as_text=True)
