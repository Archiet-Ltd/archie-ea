"""Every route that writes a work package, sent as a Viewer, as an architect and across organisations.

Each route is sent four ways, each with its own parameter id named after the route:
 - a Viewer is refused (403) and the work packages are unchanged;
 - an architect whose organisation has switched work package writes off for them is refused (403)
   by the route itself, which is the check that stands behind the read-only guard;
 - an architect is let through (the route does its work);
 - an architect sending another organisation's work package id is answered 404 and that
   organisation's row is unchanged (routes that take an id only).
"""

import uuid

import pytest

from tests.test_ba_tenant_and_authz import _cleanup_ids, _login, _make_org_id, _make_user_id

NEW = {"name": "Created by role test", "description": "x", "status": "planned"}
DATES = {"start_date": "2026-01-01", "end_date": "2026-03-01", "target_date": "2026-03-01"}

# (route name, method, path, takes a work package id, body)
ROUTES = [
    # implementation planning forms and API
    ("implementation-form-create", "POST", "/implementation/work-packages/create", False, dict(NEW)),
    ("implementation-form-edit", "POST", "/implementation/work-packages/{wp}/edit", True, {"name": "Renamed"}),
    ("implementation-form-delete", "POST", "/implementation/work-packages/{wp}/delete", True, {}),
    ("implementation-form-add-dependency", "POST", "/implementation/work-packages/{wp}/dependencies", True, {"depends_on_id": "{wp2}"}),
    ("implementation-form-remove-dependency", "POST", "/implementation/work-packages/{wp}/dependencies/999999/remove", True, {}),
    ("implementation-api-create", "POST", "/implementation/api/work-packages", False, dict(NEW)),
    ("implementation-api-update", "PUT", "/implementation/api/work-packages/{wp}", True, {"name": "Renamed"}),
    ("implementation-api-delete", "DELETE", "/implementation/api/work-packages/{wp}", True, None),
    # unified enterprise API and form
    ("enterprise-form-delete", "POST", "/enterprise/implementation/work-packages", True, {"ids": ["{wp}"]}),
    ("enterprise-api-create", "POST", "/enterprise/api/work-packages", False, dict(NEW)),
    ("enterprise-api-update", "PATCH", "/enterprise/api/work-packages/{wp}", True, {"name": "Renamed"}),
    ("enterprise-api-delete", "DELETE", "/enterprise/api/work-packages/{wp}", True, None),
    ("enterprise-api-bulk-delete", "DELETE", "/enterprise/api/work-packages/bulk", False, {"ids": ["{wp}"]}),
    # roadmap files under main
    ("application-roadmap-create", "POST", "/api/applications/{app}/work-packages", False, {**NEW, **DATES, "transformation_type": "migrate"}),
    ("application-roadmap-update", "PUT", "/api/applications/{app}/work-packages/{wp}", True, {"name": "Renamed"}),
    ("application-roadmap-delete", "DELETE", "/api/applications/{app}/work-packages/{wp}", True, None),
    ("archimate-roadmap-create", "POST", "/api/archimate-work-packages", False, {**NEW, **DATES}),
    ("archimate-roadmap-update", "PUT", "/api/archimate-work-packages/{wp}", True, {"name": "Renamed"}),
    ("archimate-roadmap-delete", "DELETE", "/api/archimate-work-packages/{wp}", True, None),
    ("capability-roadmap-create", "POST", "/api/capability-work-packages", False, {**NEW, **DATES}),
    ("capability-roadmap-update", "PUT", "/api/capability-work-packages/{wp}", True, {"name": "Renamed"}),
    ("capability-roadmap-delete", "DELETE", "/api/capability-work-packages/{wp}", True, None),
    ("capability-roadmap-task-create", "POST", "/api/capability-work-packages/{wp}/tasks", True, {"name": "Task", "title": "Task"}),
    ("capability-roadmap-task-update", "PUT", "/api/capability-work-packages/{wp}/tasks/999999", True, {"name": "Task"}),
    ("capability-roadmap-task-delete", "DELETE", "/api/capability-work-packages/{wp}/tasks/999999", True, None),
    ("capability-roadmap-deliverable-create", "POST", "/api/capability-work-packages/{wp}/deliverables", True, {"name": "Deliverable", "title": "Deliverable"}),
    ("capability-roadmap-deliverable-update", "PUT", "/api/capability-work-packages/{wp}/deliverables/999999", True, {"name": "Deliverable"}),
    ("capability-roadmap-deliverable-delete", "DELETE", "/api/capability-work-packages/{wp}/deliverables/999999", True, None),
    ("strategic-roadmap-create", "POST", "/api/strategic-work-packages", False, {**NEW, **DATES, "business_capability": "Sales"}),
    ("strategic-roadmap-update", "PUT", "/api/strategic-work-packages/{wp}", True, {"name": "Renamed"}),
    ("strategic-roadmap-delete", "DELETE", "/api/strategic-work-packages/{wp}", True, None),
    # solutions strategic
    ("roadmap-api-create", "POST", "/api/roadmap/work-packages", False, {**NEW, **DATES}),
    ("roadmap-api-update", "PUT", "/api/roadmap/work-packages/{wp}", True, {"name": "Renamed"}),
    ("roadmap-api-delete", "DELETE", "/api/roadmap/work-packages/{wp}", True, None),
    ("roadmap-api-generate", "POST", "/api/roadmap/automation/generate-work-packages", False, {}),
    ("roadmap-builder-create", "POST", "/api/roadmap-builder/work-packages", False, {**NEW, **DATES}),
    ("roadmap-builder-update", "PUT", "/api/roadmap-builder/work-packages/{wp}", True, {"name": "Renamed"}),
    ("roadmap-builder-delete", "DELETE", "/api/roadmap-builder/work-packages/{wp}", True, None),
    ("roadmap-builder-add-dependency", "POST", "/api/roadmap-builder/work-packages/{wp}/dependencies", True, {"depends_on_id": "{wp2}", "dependency_id": "{wp2}"}),
    ("roadmap-builder-remove-dependency", "DELETE", "/api/roadmap-builder/work-packages/{wp}/dependencies/999999", True, None),
    ("solution-design-create", "POST", "/solutions/{sol}/api/work-packages", False, dict(NEW)),
    ("solution-design-update", "PUT", "/solutions/{sol}/api/work-packages/{wp}", True, {"name": "Renamed"}),
    ("solution-design-delete", "DELETE", "/solutions/{sol}/api/work-packages/{wp}", True, None),
    # capability map roadmap
    ("capability-map-gap-create", "POST", "/capability-map/api/roadmap/gaps/{gap}/work-packages", False, dict(NEW)),
    ("capability-map-create", "POST", "/capability-map/api/roadmap/work-packages", False, dict(NEW)),
    ("capability-map-update", "PUT", "/capability-map/api/roadmap/work-packages/{wp}", True, {"name": "Renamed"}),
    ("capability-map-delete", "DELETE", "/capability-map/api/roadmap/work-packages/{wp}", True, None),
    ("capability-map-child-create", "POST", "/capability-map/api/roadmap/work-packages/{wp}/children", True, dict(NEW)),
    ("capability-map-gap-link", "POST", "/capability-map/api/roadmap/gaps/{gap}/link-work-package/{wp}", True, {}),
    ("capability-map-gap-unlink", "POST", "/capability-map/api/roadmap/gaps/{gap}/unlink-work-package/{wp}", True, {}),
    # application pages
    ("application-page-add", "POST", "/applications/{app}/work-packages/add", False, {"element_id": "{wp}"}),
    ("application-mgmt-add", "POST", "/dashboard/applications/{app}/work-packages", False, dict(NEW)),
    ("application-mgmt-delete", "DELETE", "/dashboard/applications/{app}/work-packages/{wp}", True, None),
    ("application-mgmt-form-add", "POST", "/dashboard/applications/{app}/work-packages/add", False, dict(NEW)),
    ("application-mgmt-form-edit", "POST", "/dashboard/applications/{app}/work-packages/{wp}/edit", True, {"name": "Renamed"}),
    ("application-mgmt-form-delete", "POST", "/dashboard/applications/{app}/work-packages/{wp}/delete", True, {}),
    # other writers
    ("requirement-link", "PATCH", "/api/enterprise/requirements/{req}/link-work-package", False, {"work_package_id": "{wp}"}),
    ("gap-attach", "POST", "/interface-register/gaps/{gap}/work-packages", False, dict(NEW)),
    ("delivery-export", "POST", "/api/v1/transformation-programmes/{prog}/workstreams/{ws}/work-packages/{wp}/delivery-exports", True, {}),
    ("delivery-export-retry", "POST", "/api/v1/transformation-programmes/{prog}/workstreams/{ws}/work-packages/{wp}/delivery-exports/999999/retries", True, {}),
]
# Routes whose refusal is a redirect back to the list rather than a 403 (the route flashes why).
REFUSED_BY_REDIRECT = {"enterprise-form-delete"}
# Routes that act on a work package for someone else's record without changing the work package
# row itself (a requirement's pointer, an export attempt). They keep their own check; the Viewer
# is still refused on them, but they take no work package permission of their own.
OWN_CHECK = {"requirement-link", "delivery-export", "delivery-export-retry"}
# Routes that read a form post, not a JSON body.
FORM_POSTS = {"enterprise-form-delete", "application-page-add"}
IDS = [r[0] for r in ROUTES]
assert len(set(IDS)) == len(IDS)


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture(scope="module")
def world(app):
    """Two real organisations, each with people in it, and the rows the routes name."""
    from app import db
    from app.models.organization import Organization
    from app.models.permission_grant import PermissionGrant
    from app.models.user import User

    w = {}
    with app.app_context():
        org_a = _make_org_id(db, "WPA")
        org_b = _make_org_id(db, "WPB")
        w["org_a"], w["org_b"] = org_a, org_b
        w["viewer"] = _make_user_id(db, org_a, "WpViewer", enterprise_role="procurement", role_name="Viewer")
        w["architect"] = _make_user_id(db, org_a, "WpArchitect", enterprise_role="enterprise_architect", role_name="Architect")
        w["denied"] = _make_user_id(db, org_a, "WpDenied", enterprise_role="enterprise_architect", role_name="Architect")
        w["other_architect"] = _make_user_id(db, org_b, "WpOther", enterprise_role="enterprise_architect", role_name="Architect")
        grants = []
        for action in ("create", "edit", "delete", "bulk_delete"):
            g = PermissionGrant(
                organization_id=org_a,
                subject_type="user",
                subject_key=str(w["denied"]),
                object_type="work_package",
                action=action,
                allowed=False,
            )
            db.session.add(g)
            grants.append(g)
        db.session.commit()
        w["grant_ids"] = [g.id for g in grants]
    yield w
    with app.app_context():
        from app.models.unified_work_package import UnifiedWorkPackage

        from app.models.application_portfolio import ApplicationComponent

        _cleanup_ids(db, PermissionGrant, w["grant_ids"])
        db.session.query(ApplicationComponent).filter(ApplicationComponent.organization_id.in_([org_a, org_b])).delete(
            synchronize_session=False
        )
        db.session.query(UnifiedWorkPackage).filter(
            UnifiedWorkPackage.organization_id.in_([org_a, org_b])
        ).delete(synchronize_session=False)
        db.session.commit()
        users = [w["viewer"], w["architect"], w["denied"], w["other_architect"]]
        _cleanup_ids(db, User, users)
        _cleanup_ids(db, Organization, [org_a, org_b])


def _make_wp(app, org_id, name):
    from app import db
    from app.services import work_package_service

    with app.app_context():
        wp = work_package_service.create_work_package(organization_id=org_id, name=name)
        db.session.commit()
        return wp.id


def _make_application(app, org_id):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        component = ApplicationComponent(name="Role test " + uuid.uuid4().hex[:6], organization_id=org_id)
        db.session.add(component)
        db.session.commit()
        return component.id


def _snapshot(app, org_id):
    """The organisation's work packages as (id, name, status): what must not change."""
    from app.models.unified_work_package import UnifiedWorkPackage
    from app import db

    with app.app_context():
        rows = db.session.execute(
            db.select(UnifiedWorkPackage.id, UnifiedWorkPackage.name, UnifiedWorkPackage.status)
            .where(UnifiedWorkPackage.organization_id == org_id)
            .order_by(UnifiedWorkPackage.id)
        ).all()
        return [tuple(r) for r in rows]


def _fill(value, ids):
    if isinstance(value, str):
        out = value
        for key, val in ids.items():
            out = out.replace("{" + key + "}", str(val))
        return int(out) if out.isdigit() and out != value else out
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    return value


def _send(client, app, user_id, route, ids):
    _, method, path, _takes_id, body = route
    with app.app_context():
        _login(client, user_id)
        url = _fill(path, ids)
        if body is None:
            kwargs = {}
        elif route[0] in FORM_POSTS:
            kwargs = {"data": _fill(body, ids)}
        else:
            kwargs = {"json": _fill(body, ids)}
        return client.open(url, method=method, **kwargs)


def _ids(app, world):
    """Two fresh work packages for the first organisation and one for the second."""
    return {
        "wp": _make_wp(app, world["org_a"], "First " + uuid.uuid4().hex[:6]),
        "wp2": _make_wp(app, world["org_a"], "Second " + uuid.uuid4().hex[:6]),
        "other": _make_wp(app, world["org_b"], "Other organisation " + uuid.uuid4().hex[:6]),
        "app": _make_application(app, world["org_a"]),
        "sol": 999999,
        "gap": 999999,
        "req": 999999,
        "prog": 999999,
        "ws": 999999,
    }


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_viewer_is_refused(client, app, world, route):
    ids = _ids(app, world)
    before = _snapshot(app, world["org_a"])
    r = _send(client, app, world["viewer"], route, ids)
    assert r.status_code == 403, r.get_data(as_text=True)[:300]
    assert _snapshot(app, world["org_a"]) == before


@pytest.mark.parametrize("route", [r for r in ROUTES if r[0] not in OWN_CHECK], ids=[i for i in IDS if i not in OWN_CHECK])
def test_route_checks_the_work_package_permission(client, app, world, route):
    ids = _ids(app, world)
    before = _snapshot(app, world["org_a"])
    r = _send(client, app, world["denied"], route, ids)
    assert r.status_code == (302 if route[0] in REFUSED_BY_REDIRECT else 403), r.get_data(as_text=True)[:300]
    assert _snapshot(app, world["org_a"]) == before
