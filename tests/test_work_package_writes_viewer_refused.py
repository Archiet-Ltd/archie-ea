"""Every route that writes a work package refuses a Viewer and lets an editor of the same organisation through.

One rule decides it: the organisation's permission matrix, ``can(user, action, "work_package")``. Each case is
a method and a URL; ``{wp}`` is a work package seeded in the organisation. The Viewer must get 403 and the
work package must be untouched; the architect must not be refused.
"""

import os

import pytest

from tests.test_ba_tenant_and_authz import _cleanup_ids, _login, _make_org_id, _make_user_id

NAME = {"name": "Written by the test"}
GAP = "/capability-map/api/roadmap/gaps/1"

CASES = [
    ("POST", "/enterprise/api/work-packages", NAME),
    ("PATCH", "/enterprise/api/work-packages/{wp}", NAME),
    ("DELETE", "/enterprise/api/work-packages/{wp}", None),
    ("DELETE", "/enterprise/api/work-packages/bulk", {"ids": ["{wp}"]}),
    ("POST", "/enterprise/implementation/work-packages?delete={wp}", None),
    ("POST", "/implementation/api/work-packages", NAME),
    ("PUT", "/implementation/api/work-packages/{wp}", NAME),
    ("DELETE", "/implementation/api/work-packages/{wp}", None),
    ("POST", "/implementation/api/deliverables", {"name": "D", "work_package_id": "{wp}"}),
    ("POST", "/implementation/work-packages/create", NAME),
    ("POST", "/implementation/work-packages/{wp}/edit", NAME),
    ("POST", "/implementation/work-packages/{wp}/delete", None),
    ("POST", "/implementation/work-packages/{wp}/dependencies", {"depends_on_id": "{wp}"}),
    ("POST", "/implementation/work-packages/{wp}/dependencies/1/remove", None),
    ("POST", "/api/roadmap/work-packages", NAME),
    ("PUT", "/api/roadmap/work-packages/{wp}", NAME),
    ("DELETE", "/api/roadmap/work-packages/{wp}", None),
    ("POST", "/api/roadmap/deliverables", {"name": "D", "work_package_id": "{wp}"}),
    ("PUT", "/api/roadmap/deliverables/{wp}", NAME),
    ("DELETE", "/api/roadmap/deliverables/{wp}", None),
    ("POST", "/api/roadmap/automation/generate-work-packages", {"work_package_ids": ["{wp}"]}),
    ("POST", "/api/roadmap-builder/work-packages", NAME),
    ("PUT", "/api/roadmap-builder/work-packages/{wp}", NAME),
    ("DELETE", "/api/roadmap-builder/work-packages/{wp}", None),
    ("POST", "/api/roadmap-builder/work-packages/{wp}/dependencies", {"depends_on_id": "{wp}"}),
    ("DELETE", "/api/roadmap-builder/work-packages/{wp}/dependencies/1", None),
    ("POST", "/api/archimate-work-packages", NAME),
    ("PUT", "/api/archimate-work-packages/{wp}", NAME),
    ("DELETE", "/api/archimate-work-packages/{wp}", None),
    ("POST", "/api/strategic-work-packages", NAME),
    ("PUT", "/api/strategic-work-packages/{wp}", NAME),
    ("DELETE", "/api/strategic-work-packages/{wp}", None),
    ("POST", "/api/capability-work-packages", NAME),
    ("PUT", "/api/capability-work-packages/{wp}", NAME),
    ("DELETE", "/api/capability-work-packages/{wp}", None),
    ("POST", "/api/capability-work-packages/{wp}/deliverables", NAME),
    ("POST", "/api/capability-work-packages/{wp}/tasks", NAME),
    ("POST", "/api/applications/1/work-packages", NAME),
    ("PUT", "/api/applications/1/work-packages/{wp}", NAME),
    ("DELETE", "/api/applications/1/work-packages/{wp}", None),
    ("POST", "/dashboard/applications/1/work-packages", NAME),
    ("DELETE", "/dashboard/applications/1/work-packages/{wp}", None),
    ("POST", "/dashboard/applications/1/work-packages/add", {"element_id": "{wp}"}),
    ("POST", "/dashboard/applications/1/work-packages/{wp}/edit", NAME),
    ("POST", "/dashboard/applications/1/work-packages/{wp}/delete", None),
    ("POST", "/solutions/1/api/work-packages", NAME),
    ("PUT", "/solutions/1/api/work-packages/{wp}", NAME),
    ("DELETE", "/solutions/1/api/work-packages/{wp}", None),
    ("POST", "/capability-map/api/roadmap/work-packages", NAME),
    ("PUT", "/capability-map/api/roadmap/work-packages/{wp}", NAME),
    ("DELETE", "/capability-map/api/roadmap/work-packages/{wp}", None),
    ("POST", "/capability-map/api/roadmap/work-packages/{wp}/children", NAME),
    ("POST", GAP + "/work-packages", NAME),
    ("POST", "/interface-register/gaps/1/work-packages", None),
    ("POST", "/capability-map/api/archimate/elements", {"type": "WorkPackage", "name": "Written by the test"}),
    ("POST", "/api/architecture-assistant/roadmap-elements", {}),
    ("POST", GAP + "/link-work-package/{wp}", None),
    ("POST", GAP + "/unlink-work-package/{wp}", None),
]


def _fill(value, wp):
    if isinstance(value, str):
        return value.replace("{wp}", str(wp)) if value != "{wp}" else wp
    if isinstance(value, list):
        return [_fill(v, wp) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, wp) for k, v in value.items()}
    return value


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
def people(app):
    """One organisation with a work package, a Viewer and an architect."""
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.services import work_package_service

    with app.app_context():
        org = _make_org_id(db, "WPW")
        viewer = _make_user_id(db, org, "WpViewer", enterprise_role="procurement", role_name="Viewer")
        architect = _make_user_id(db, org, "WpArchitect", enterprise_role="enterprise_architect")
        wp = work_package_service.create_work_package(organization_id=org, name="Seeded package")
        db.session.commit()
        wp_id = wp.id
    yield {"org": org, "viewer": viewer, "architect": architect, "wp": wp_id}
    with app.app_context():
        _cleanup_ids(db, User, [viewer, architect])
        _cleanup_ids(db, Organization, [org])


def _send(client, method, url, body):
    kwargs = {}
    if body is not None:
        kwargs["json"] = body
    return client.open(url, method=method, **kwargs)


# Form routes that refuse by sending the person back to the list with the refusal shown, not with a 403.
REDIRECTS = {
    "/enterprise/implementation/work-packages?delete={wp}",
    "/implementation/work-packages/{wp}/delete",
    "/implementation/work-packages/{wp}/dependencies/1/remove",
}


@pytest.mark.parametrize("method,url,body", CASES, ids=[f"{m} {u}" for m, u, _ in CASES])
def test_viewer_is_refused(app, people, method, url, body):
    client = app.test_client()
    with app.app_context():
        _login(client, people["viewer"])
        r = _send(client, method, url.format(wp=people["wp"]), _fill(body, people["wp"]))
        allowed = {302, 403} if url in REDIRECTS else {403}
        assert r.status_code in allowed, f"{method} {url} answered {r.status_code}: {r.get_data(as_text=True)[:200]}"


def test_viewer_left_the_work_package_in_place(app, people):
    from app import db
    from app.models.unified_work_package import UnifiedWorkPackage

    with app.app_context():
        assert db.session.get(UnifiedWorkPackage, people["wp"]) is not None


@pytest.mark.parametrize("method,url,body", CASES, ids=[f"{m} {u}" for m, u, _ in CASES])
def test_architect_is_not_refused(app, people, method, url, body):
    client = app.test_client()
    with app.app_context():
        _login(client, people["architect"])
        r = _send(client, method, url.format(wp=people["wp"]), _fill(body, people["wp"]))
        assert r.status_code not in (401, 403), f"{method} {url} refused an architect: {r.get_data(as_text=True)[:200]}"


def test_a_grant_in_the_matrix_decides_on_the_roadmap_api(app, people):
    """The roadmap API asks the matrix alone about work packages: a grant opens it for a Viewer, and taking
    the grant away closes it again."""
    from app import db
    from app.core.auth.permissions import set_grant

    def post_as_viewer():
        client = app.test_client()
        with app.app_context():
            _login(client, people["viewer"])
            return client.post("/api/roadmap/work-packages", json=NAME).status_code

    with app.app_context():
        set_grant(people["org"], "user", people["viewer"], "work_package", "create", True)
        db.session.commit()
    try:
        assert post_as_viewer() != 403
    finally:
        with app.app_context():
            set_grant(people["org"], "user", people["viewer"], "work_package", "create", False)
            db.session.commit()
    assert post_as_viewer() == 403
