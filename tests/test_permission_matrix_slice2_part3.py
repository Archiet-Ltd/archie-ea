"""Permission matrix slice 2, part 3: motivation element write routes.

Viewer-refused tests for every write route in:
- app/application_mgmt/motivation_layer_routes.py
- app/modules/applications/routes/element_routes.py

Each test fails before the gate and passes after.
"""

import uuid

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"PM {label} {suffix}", slug=f"pm-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, org_role, platform_role="User"):
    """A confirmed user whose organisation role is ``org_role``."""
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    role = Role.query.filter_by(name=platform_role).first()
    user = User(
        email=f"pm-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Perm",
        last_name=org_role,
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, org_role)
    if org_role == "org_admin":
        user.grant_org_admin()
    db_session.flush()
    return user


def _application(db_session, org, name=None):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name=name or f"PM app {uuid.uuid4().hex[:6]}",
        description="permission matrix test",
        organization_id=org.id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _goal(db_session, org, name=None):
    from app.models.motivation import Goal

    row = Goal(name=name or f"Goal {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _driver(db_session, org, name=None):
    from app.models.motivation import Driver

    row = Driver(name=name or f"Driver {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _requirement(db_session, org, title=None):
    from app.models.models import Requirement

    row = Requirement(title=title or f"Req {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _business_process(db_session, org, name=None):
    from app.models.process_data import BusinessProcess

    row = BusinessProcess(name=name or f"BP {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _business_actor(db_session, org, name=None):
    from app.models.business_layer import BusinessActor

    row = BusinessActor(name=name or f"BA {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _business_service(db_session, org, name=None):
    from app.models.business_layer import BusinessService

    row = BusinessService(name=name or f"BS {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _business_role(db_session, org, name=None):
    from app.models.business_layer import BusinessRole

    row = BusinessRole(name=name or f"BR {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _application_interface(db_session, org, name=None):
    from app.models.application_layer import ApplicationInterface

    row = ApplicationInterface(name=name or f"AI {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _application_service(db_session, org, name=None):
    from app.models.application_layer import ApplicationService
    from app.models.archimate_core import ArchiMateElement

    # Create ArchiMateElement first (ApplicationService listener has a bug:
    # it creates an ArchiMateElement without organization_id)
    archimate_elem = ArchiMateElement(
        name=name or f"AS {uuid.uuid4().hex[:6]}",
        type="ApplicationService",
        layer="Application",
        description="Application service",
        organization_id=org.id,
    )
    db_session.add(archimate_elem)
    db_session.flush()

    row = ApplicationService(
        name=name or f"AS {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        archimate_element_id=archimate_elem.id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _data_object(db_session, org, name=None):
    from app.models.application_layer import DataObject

    row = DataObject(name=name or f"DO {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _technology_node(db_session, org, name=None):
    from app.models.technology_layer import Node

    row = Node(name=name or f"TN {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _technology_device(db_session, org, name=None):
    from app.models.technology_layer import Device

    row = Device(name=name or f"TD {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _system_software(db_session, org, name=None):
    from app.models.technology_layer import SystemSoftware

    row = SystemSoftware(name=name or f"SS {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _work_package(db_session, org, name=None):
    from app.models.implementation_migration import WorkPackage

    row = WorkPackage(name=name or f"WP {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _deliverable(db_session, org, name=None):
    from app.models.implementation_migration import Deliverable, WorkPackage

    # Create WorkPackage first (Deliverable requires work_package_id or unified_work_package_id)
    wp = WorkPackage(name=f"WP for Del {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(wp)
    db_session.flush()

    row = Deliverable(
        name=name or f"Del {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        work_package_id=wp.id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _plateau(db_session, org, name=None):
    from app.models.implementation_migration import Plateau

    row = Plateau(name=name or f"Plat {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _login(client, login_as, user):
    login_as(client, user)


class TestMotivationElementCreateDelete:
    """Viewer must be refused on create/delete motivation element routes."""

    def test_viewer_refused_create_stakeholder(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-stakeholder-create")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/dashboard/applications/{app_obj.id}/stakeholders",
            json={"name": "Test Stakeholder"},
        )
        assert resp.status_code == 403

    def test_viewer_refused_delete_stakeholder(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-stakeholder-delete")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.delete(f"/dashboard/applications/{app_obj.id}/stakeholders/999")
        assert resp.status_code == 403

    def test_viewer_refused_create_requirement_inline(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-req-create")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/dashboard/applications/{app_obj.id}/requirements",
            json={"title": "Test Requirement"},
        )
        assert resp.status_code == 403

    def test_viewer_refused_delete_requirement_inline(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-req-delete")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.delete(f"/dashboard/applications/{app_obj.id}/requirements/999")
        assert resp.status_code == 403


class TestMotivationElementLinkRoutes:
    """Viewer must be refused on all motivation element link routes (application_mgmt)."""

    def test_viewer_refused_link_goal(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-goal-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        goal = _goal(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/dashboard/applications/{app_obj.id}/goals/add",
            data={"element_id": goal.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_driver(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-driver-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        driver = _driver(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/dashboard/applications/{app_obj.id}/drivers/add",
            data={"element_id": driver.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_requirement(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-req-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        req = _requirement(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/dashboard/applications/{app_obj.id}/requirements/add",
            data={"element_id": req.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403


class TestUnifiedApplicationsLinkRoutes:
    """Viewer must be refused on all unified applications link routes (element_routes)."""

    def test_viewer_refused_link_requirement(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-req-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        req = _requirement(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/requirements/add",
            data={"element_id": req.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_business_process(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-bp-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        bp = _business_process(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/business-processes/add",
            data={"element_id": bp.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_business_actor(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-ba-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        ba = _business_actor(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/business-actors/add",
            data={"element_id": ba.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_business_service(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-bs-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        bs = _business_service(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/business-services/add",
            data={"element_id": bs.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_business_role(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-br-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        br = _business_role(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/business-roles/add",
            data={"element_id": br.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_application_interface(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-ai-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        ai = _application_interface(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/interfaces/add",
            data={"element_id": ai.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_application_service(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-as-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        asvc = _application_service(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/services/add",
            data={"element_id": asvc.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_data_object(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-do-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        dobj = _data_object(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/data-objects/add",
            data={"element_id": dobj.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_technology_node(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-tn-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        tn = _technology_node(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/technology-nodes/add",
            data={"element_id": tn.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_technology_device(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-td-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        td = _technology_device(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/technology-devices/add",
            data={"element_id": td.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_system_software(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-ss-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        ss = _system_software(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/system-software/add",
            data={"element_id": ss.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_goal(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-goal-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        goal = _goal(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/goals/add",
            data={"element_id": goal.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_driver(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-driver-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        driver = _driver(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/drivers/add",
            data={"element_id": driver.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_work_package(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-wp-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        wp = _work_package(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/work-packages/add",
            data={"element_id": wp.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_deliverable(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-del-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        deliv = _deliverable(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/deliverables/add",
            data={"element_id": deliv.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403

    def test_viewer_refused_link_plateau(self, app, db_session, login_as, client):
        org = _org(db_session, "viewer-u-plat-link")
        viewer = _user(db_session, org, "viewer")
        app_obj = _application(db_session, org)
        plat = _plateau(db_session, org)
        db_session.commit()

        _login(client, login_as, viewer)
        resp = client.post(
            f"/applications/{app_obj.id}/plateaus/add",
            data={"element_id": plat.id},
            follow_redirects=True,
        )
        assert resp.status_code == 403


class TestArchitectAllowed:
    """Architect must be allowed on all gated routes (sanity check)."""

    def test_architect_allowed_link_goal(self, app, db_session, login_as, client):
        org = _org(db_session, "arch-goal-link")
        architect = _user(db_session, org, "architect")
        app_obj = _application(db_session, org)
        goal = _goal(db_session, org)
        db_session.commit()

        _login(client, login_as, architect)
        resp = client.post(
            f"/dashboard/applications/{app_obj.id}/goals/add",
            data={"element_id": goal.id},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_architect_allowed_link_driver(self, app, db_session, login_as, client):
        org = _org(db_session, "arch-driver-link")
        architect = _user(db_session, org, "architect")
        app_obj = _application(db_session, org)
        driver = _driver(db_session, org)
        db_session.commit()

        _login(client, login_as, architect)
        resp = client.post(
            f"/dashboard/applications/{app_obj.id}/drivers/add",
            data={"element_id": driver.id},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_architect_allowed_link_requirement(self, app, db_session, login_as, client):
        org = _org(db_session, "arch-req-link")
        architect = _user(db_session, org, "architect")
        app_obj = _application(db_session, org)
        req = _requirement(db_session, org)
        db_session.commit()

        _login(client, login_as, architect)
        resp = client.post(
            f"/dashboard/applications/{app_obj.id}/requirements/add",
            data={"element_id": req.id},
            follow_redirects=True,
        )
        assert resp.status_code == 200