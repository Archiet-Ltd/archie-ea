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

NEW = {"name": "Created by role test", "description": "x", "status": "planned", "business_capability": "Sales", "capability_ids": []}
DATES = {"start_date": "2026-01-01", "end_date": "2026-03-01", "target_date": "2026-03-01"}

# (route name, method, path, takes a work package id, body)
ROUTES = [
    # implementation planning forms and API
    ("implementation-form-create", "POST", "/implementation/work-packages/create", False, dict(NEW)),
    ("implementation-form-edit", "POST", "/implementation/work-packages/{wp}/edit", True, {"name": "Renamed"}),
    ("implementation-form-delete", "POST", "/implementation/work-packages/{wp}/delete", True, {}),
    ("implementation-form-add-dependency", "POST", "/implementation/work-packages/{wp}/dependencies", True, {"depends_on_id": "{wp2}", "dependency_id": "{wp2}", "depends_on": "{wp2}"}),
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
    ("capability-roadmap-task-create", "POST", "/api/capability-work-packages/{wp3}/tasks", True, {"name": "Task", "title": "Task"}),
    ("capability-roadmap-task-update", "PUT", "/api/capability-work-packages/{wp3}/tasks/{task}", True, {"name": "Task"}),
    ("capability-roadmap-task-delete", "DELETE", "/api/capability-work-packages/{wp3}/tasks/{task}", True, None),
    ("capability-roadmap-deliverable-create", "POST", "/api/capability-work-packages/{wp3}/deliverables", True, {"name": "Deliverable", "title": "Deliverable"}),
    ("capability-roadmap-deliverable-update", "PUT", "/api/capability-work-packages/{wp3}/deliverables/{deliv}", True, {"name": "Deliverable"}),
    ("capability-roadmap-deliverable-delete", "DELETE", "/api/capability-work-packages/{wp3}/deliverables/{deliv}", True, None),
    ("strategic-roadmap-create", "POST", "/api/strategic-work-packages", False, {**NEW, **DATES, "business_capability": "Sales"}),
    ("strategic-roadmap-update", "PUT", "/api/strategic-work-packages/{wp}", True, {"name": "Renamed"}),
    ("strategic-roadmap-delete", "DELETE", "/api/strategic-work-packages/{wp}", True, None),
    # solutions strategic
    ("roadmap-api-create", "POST", "/api/roadmap/work-packages", False, {"name": "Created by role test", "business_capability": "Role test capability", "start_date": "2030-01-01", "end_date": "2030-06-01"}),
    ("roadmap-api-update", "PUT", "/api/roadmap/work-packages/{wp}", True, {"name": "Renamed"}),
    ("roadmap-api-delete", "DELETE", "/api/roadmap/work-packages/{wp}", True, None),
    ("roadmap-api-generate", "POST", "/api/roadmap/automation/generate-work-packages", False, {"source_type": "capability", "source_id": "{cap}"}),
    ("roadmap-builder-create", "POST", "/api/roadmap-builder/work-packages", False, {**NEW, **DATES}),
    ("roadmap-builder-update", "PUT", "/api/roadmap-builder/work-packages/{wp}", True, {"name": "Renamed"}),
    ("roadmap-builder-delete", "DELETE", "/api/roadmap-builder/work-packages/{wp}", True, None),
    ("roadmap-builder-add-dependency", "POST", "/api/roadmap-builder/work-packages/{wp}/dependencies", True, {"depends_on_id": "{wp2}", "dependency_id": "{wp2}"}),
    ("roadmap-builder-remove-dependency", "DELETE", "/api/roadmap-builder/work-packages/{wp}/dependencies/999999", True, None),
    ("solution-design-create", "POST", "/solutions/{sol}/api/work-packages", False, dict(NEW)),
    ("solution-design-update", "PUT", "/solutions/{sol}/api/work-packages/{rwp}", True, {"name": "Renamed"}),
    ("solution-design-delete", "DELETE", "/solutions/{sol}/api/work-packages/{rwp}", True, None),
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
    ("application-mgmt-delete", "DELETE", "/dashboard/applications/{app}/work-packages/{element}", True, None),
    ("application-mgmt-form-add", "POST", "/dashboard/applications/{app}/work-packages/add", False, dict(NEW)),
    ("application-mgmt-form-edit", "POST", "/dashboard/applications/{app}/work-packages/{wp}/edit", True, {"name": "Renamed"}),
    ("application-mgmt-form-delete", "POST", "/dashboard/applications/{app}/work-packages/{wp}/delete", True, {}),
    # other writers
    ("requirement-link", "PATCH", "/api/enterprise/requirements/{req}/link-work-package", False, {"work_package_id": "{wp}"}),
    ("gap-attach", "POST", "/interface-register/gaps/{gap}/work-packages", False, {"name": "Attached", "initiative_id": "{wp}"}),
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
FORM_POSTS = {"enterprise-form-delete", "application-page-add", "gap-attach"}
# Routes that need a whole architecture and initiative behind them to run to the end; they are sent
# as a Viewer and as an architect barred from work packages, not driven to completion. The
# generate route also reads a table (applications) that the schema does not have.
NOT_DRIVEN_TO_END = {"gap-attach", "application-mgmt-add", "roadmap-api-generate"}
# Only an administrator deletes several work packages at once; an architect is refused.
ADMINISTRATOR_ONLY = {"enterprise-api-bulk-delete"}
# Routes that act on the older work package table, so they need a row there.
OLDER_TABLE = {
    "application-roadmap-update",
    "application-roadmap-delete",
    "application-mgmt-delete",
    "application-mgmt-form-edit",
    "application-mgmt-form-delete",
    "application-page-add",
}
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
        w["administrator"] = _make_user_id(db, org_b, "WpAdmin", enterprise_role="enterprise_architect", role_name="Administrator")
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
        from app.models.implementation_migration import WorkPackage

        _cleanup_ids(db, PermissionGrant, w["grant_ids"])
        db.session.query(ApplicationComponent).filter(ApplicationComponent.organization_id.in_([org_a, org_b])).delete(
            synchronize_session=False
        )
        from app.models.implementation_migration import Deliverable, Gap
        from app.models.roadmap import RoadmapTask
        from app.models.roadmap_models import RoadmapWorkPackage
        from app.models.unified_capability import UnifiedCapability
        from app.models.solution_models import Solution

        orgs = [org_a, org_b]
        solution_ids = [r[0] for r in db.session.query(Solution.id).filter(Solution.organization_id.in_(orgs))]
        if solution_ids:
            db.session.query(RoadmapWorkPackage).filter(
                RoadmapWorkPackage.source_type == "solution", RoadmapWorkPackage.source_id.in_(solution_ids)
            ).delete(synchronize_session=False)
        for model in (RoadmapTask, Deliverable, Gap, Solution, UnifiedCapability):
            db.session.query(model).filter(model.organization_id.in_(orgs)).delete(synchronize_session=False)
        db.session.query(WorkPackage).filter(WorkPackage.organization_id.in_([org_a, org_b])).delete(
            synchronize_session=False
        )
        db.session.query(UnifiedWorkPackage).filter(
            UnifiedWorkPackage.organization_id.in_([org_a, org_b])
        ).delete(synchronize_session=False)
        db.session.commit()
        users = [w["viewer"], w["administrator"], w["architect"], w["denied"], w["other_architect"]]
        _cleanup_ids(db, User, users)
        _cleanup_ids(db, Organization, [org_a, org_b])


def _make_wp(app, org_id, name, application_id=None):
    from datetime import datetime

    from app import db
    from app.services import work_package_service

    with app.app_context():
        wp = work_package_service.create_work_package(
            organization_id=org_id,
            name=name,
            start_date=datetime(2026, 1, 1),
            end_date=datetime(2026, 3, 1),
            application_component_id=application_id,
        )
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


def _make_older_wp(app, org_id, name, application_id=None):
    from datetime import date

    from app import db
    from app.models.implementation_migration import WorkPackage

    with app.app_context():
        wp = WorkPackage(
            name=name,
            organization_id=org_id,
            application_component_id=application_id,
            start_date=date(2026, 1, 1),
            target_date=date(2026, 3, 1),
        )
        db.session.add(wp)
        db.session.commit()
        return wp.id


def _seed_linked(app, world, ids):
    """The records some routes name besides the work package: a gap, a solution, a task, a deliverable."""
    from datetime import date

    from app import db
    from app.models.implementation_migration import Gap
    from app.models.roadmap import RoadmapTask
    from app.models.implementation_migration import Deliverable
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.models.solution_models import Solution
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_work_package import UnifiedWorkPackage

    with app.app_context():
        capability = UnifiedCapability(name="Role test capability", organization_id=world["org_a"], level=1)
        db.session.add(capability)
        gap = Gap(name="Gap " + uuid.uuid4().hex[:6], organization_id=world["org_a"])
        solution = Solution(name="Solution " + uuid.uuid4().hex[:6], organization_id=world["org_a"], created_by_id=world["architect"])
        db.session.add_all([gap, solution])
        db.session.flush()
        other_solution = Solution(name="Other solution " + uuid.uuid4().hex[:6], organization_id=world["org_b"], created_by_id=world["other_architect"])
        db.session.add(other_solution)
        db.session.flush()
        rwp = RoadmapWorkPackage(name="Solution package", source_type="solution", source_id=solution.id, business_capability="Sales")
        rwp_other = RoadmapWorkPackage(name="Other package", source_type="solution", source_id=other_solution.id, business_capability="Sales")
        task = RoadmapTask(
            title="Task", unified_work_package_id=ids["wp3"], organization_id=world["org_a"], start_date=date(2026, 1, 1)
        )
        deliverable = Deliverable(name="Deliverable", unified_work_package_id=ids["wp3"], organization_id=world["org_a"])
        db.session.add_all([rwp, rwp_other, task, deliverable])
        db.session.flush()
        ids.update(
            gap=gap.id,
            cap=capability.id,
            sol=solution.id,
            rwp=rwp.id,
            rwp_other=rwp_other.id,
            task=task.id,
            deliv=deliverable.id,
            element=db.session.get(UnifiedWorkPackage, ids["wp"]).archimate_element_id,
            element_other=db.session.get(UnifiedWorkPackage, ids["other"]).archimate_element_id,
        )
        db.session.commit()
    return ids


def _snapshot(app, org_id):
    """The organisation's work packages in both tables as (id, name, status): what must not change."""
    from app import db
    from app.models.implementation_migration import WorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage

    with app.app_context():
        rows = []
        for model in (UnifiedWorkPackage, WorkPackage):
            found = db.session.execute(
                db.select(model.id, model.name, model.status)
                .where(model.organization_id == org_id)
                .order_by(model.id)
            ).all()
            rows.append([tuple(r) for r in found])
        return rows


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
    if route[0] in OLDER_TABLE:
        ids = {**ids, "wp": ids["older"]}
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
    application_id = _make_application(app, world["org_a"])
    return _seed_linked(app, world, {
        "wp": _make_wp(app, world["org_a"], "First " + uuid.uuid4().hex[:6], application_id),
        "wp3": _make_wp(app, world["org_a"], "Third " + uuid.uuid4().hex[:6], application_id),
        "wp2": _make_wp(app, world["org_a"], "Second " + uuid.uuid4().hex[:6], application_id),
        "other": _make_wp(app, world["org_b"], "Other organisation " + uuid.uuid4().hex[:6]),
        "older": _make_older_wp(app, world["org_a"], "Older " + uuid.uuid4().hex[:6], application_id),
        "older_other": _make_older_wp(app, world["org_b"], "Older other " + uuid.uuid4().hex[:6]),
        "app": application_id,
        "req": 999999,
        "prog": 999999,
        "ws": 999999,
    })


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


DRIVEN = [r for r in ROUTES if r[0] not in OWN_CHECK | NOT_DRIVEN_TO_END | ADMINISTRATOR_ONLY]


@pytest.mark.parametrize("route", DRIVEN, ids=[r[0] for r in DRIVEN])
def test_architect_is_let_through(client, app, world, route):
    ids = _ids(app, world)
    r = _send(client, app, world["architect"], route, ids)
    assert r.status_code in (200, 201, 202, 204, 302), (r.status_code, r.get_data(as_text=True)[:300])


@pytest.mark.parametrize(
    "route", [r for r in ROUTES if r[3] and r[0] not in OWN_CHECK], ids=[r[0] for r in ROUTES if r[3] and r[0] not in OWN_CHECK]
)
def test_other_organisations_work_package_is_not_found(client, app, world, route):
    ids = _ids(app, world)
    ids["wp"] = ids["other"]
    ids["wp3"] = ids["other"]
    ids["older"] = ids["older_other"]
    ids["element"] = ids["element_other"]
    ids["rwp"] = ids["rwp_other"]
    before = _snapshot(app, world["org_b"])
    r = _send(client, app, world["architect"], route, ids)
    assert r.status_code == (302 if route[0] in REFUSED_BY_REDIRECT else 404), (r.status_code, r.get_data(as_text=True)[:300])
    assert _snapshot(app, world["org_b"]) == before


@pytest.mark.parametrize("route", [r for r in ROUTES if r[0] in ADMINISTRATOR_ONLY], ids=sorted(ADMINISTRATOR_ONLY))
def test_only_an_administrator_deletes_several_at_once(client, app, world, route):
    """Run in the second organisation, whose architect and administrator these are."""
    ids = {"wp": _make_wp(app, world["org_b"], "Bulk " + uuid.uuid4().hex[:6])}
    before = _snapshot(app, world["org_b"])
    refused = _send(client, app, world["other_architect"], route, ids)
    assert refused.status_code == 403, refused.get_data(as_text=True)[:300]
    assert _snapshot(app, world["org_b"]) == before
    allowed = _send(client, app, world["administrator"], route, ids)
    assert allowed.status_code in (200, 204), (allowed.status_code, allowed.get_data(as_text=True)[:300])
    assert _snapshot(app, world["org_b"]) != before
