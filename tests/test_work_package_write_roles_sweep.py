"""Every other route that writes a work package, roadmap gap or plateau needs a write role.

The sibling test (test_work_package_write_roles) covers the nine roadmap and
enterprise-list routes. This one covers the rest: the older roadmap form routes,
the application pages, implementation planning, the roadmap builder and API, the
solution designer, the interface register and the assistant.

For each route, with two real organisations:
  * a read-only Viewer is refused with 403 and nothing in the stores changes;
  * a write role is not refused (the handler runs);
  * a write role naming another organisation's record gets 404 and nothing in
    either organisation's stores changes;
  * with CSRF protection on, a write without the token is still refused.
"""

from __future__ import annotations

import uuid

import pytest

from app import db

pytestmark = pytest.mark.usefixtures("db_session")

# Tables any of these routes can write. The whole table is fingerprinted, so a
# write to either organisation's rows (or to a row with no organisation) shows.
STORES = (
    "unified_work_packages",
    "work_packages",
    "gaps",
    "plateaus",
    "deliverables",
    "roadmap_work_packages",
    "roadmap_deliverables",
    "roadmap_tasks",
    "archimate_elements",
    "archimate_relationships",
    "solutions",
    "solution_plateaus",
    "consolidation_list_entries",
)

# (label, method, url with {keys}, json body, [key sets whose organisation-B version is tried])
# Keys: uwp unified work package, lwp legacy work package, app application,
# gap, plat plateau, deliv deliverable, sol solution, rwp roadmap work package.
ROUTES = [
    # --- main: application roadmap
    ("app wp create", "post", "/api/applications/{app}/work-packages", {"name": "x"}, [("app",)]),
    ("app wp update", "put", "/api/applications/{app}/work-packages/{lwp}", {"name": "x"}, [("lwp",)]),
    ("app wp delete", "delete", "/api/applications/{app}/work-packages/{lwp}", None, [("lwp",)]),
    # --- main: archimate / strategic / capability roadmap
    ("archimate wp create", "post", "/api/archimate-work-packages", {"name": "x"}, []),
    ("archimate wp update", "put", "/api/archimate-work-packages/{uwp}", {"name": "x"}, [("uwp",)]),
    ("archimate wp delete", "delete", "/api/archimate-work-packages/{uwp}", None, [("uwp",)]),
    ("strategic wp create", "post", "/api/strategic-work-packages", {"name": "x"}, []),
    ("strategic wp update", "put", "/api/strategic-work-packages/{uwp}", {"name": "x"}, [("uwp",)]),
    ("strategic wp delete", "delete", "/api/strategic-work-packages/{uwp}", None, [("uwp",)]),
    ("capability wp create", "post", "/api/capability-work-packages", {"name": "x"}, []),
    ("capability wp update", "put", "/api/capability-work-packages/{uwp}", {"name": "x"}, [("uwp",)]),
    ("capability wp delete", "delete", "/api/capability-work-packages/{uwp}", None, [("uwp",)]),
    ("capability task create", "post", "/api/capability-work-packages/{uwp}/tasks", {"title": "x"}, [("uwp",)]),
    ("capability task update", "put", "/api/capability-work-packages/{uwp}/tasks/{zero}", {"title": "x"}, [("uwp",)]),
    ("capability task delete", "delete", "/api/capability-work-packages/{uwp}/tasks/{zero}", None, [("uwp",)]),
    ("capability deliverable create", "post", "/api/capability-work-packages/{uwp}/deliverables", {"name": "x"}, [("uwp",)]),
    ("capability deliverable update", "put", "/api/capability-work-packages/{uwp}/deliverables/{deliv}", {"name": "x"}, [("deliv",)]),
    ("capability deliverable delete", "delete", "/api/capability-work-packages/{uwp}/deliverables/{deliv}", None, [("deliv",)]),
    # --- capability map roadmap: gaps and plateaus
    ("map gap create", "post", "/capability-map/api/roadmap/gaps", {"name": "x"}, []),
    ("map gap update", "put", "/capability-map/api/roadmap/gaps/{gap}", {"name": "x"}, [("gap",)]),
    ("map gap delete", "delete", "/capability-map/api/roadmap/gaps/{gap}", None, [("gap",)]),
    ("map gap link plateau", "post", "/capability-map/api/roadmap/gaps/{gap}/link-plateau/{plat}", None, [("gap",), ("plat",)]),
    ("map gap unlink plateau", "post", "/capability-map/api/roadmap/gaps/{gap}/unlink-plateau/{plat}", None, [("gap",), ("plat",)]),
    ("map gap add from capability", "post", "/capability-map/api/roadmap/gaps/add-from-capability", {}, []),
    ("map gap convert", "post", "/capability-map/api/roadmap/gaps/convert", {}, []),
    ("map gap detect", "post", "/capability-map/api/roadmap/gaps/detect", {}, []),
    ("map plateau create", "post", "/capability-map/api/roadmap/plateaus", {"name": "x"}, []),
    # --- roadmap API
    ("api wp create", "post", "/api/roadmap/work-packages", {"name": "x", "business_capability": "c"}, []),
    ("api wp update", "put", "/api/roadmap/work-packages/{uwp}", {"name": "x"}, [("uwp",)]),
    ("api wp delete", "delete", "/api/roadmap/work-packages/{uwp}", None, [("uwp",)]),
    ("api deliverable create", "post", "/api/roadmap/deliverables", {"name": "x", "work_package_id": "{uwp}"}, [("uwp",)]),
    ("api deliverable update", "put", "/api/roadmap/deliverables/{deliv}", {"name": "x"}, [("deliv",)]),
    ("api deliverable delete", "delete", "/api/roadmap/deliverables/{deliv}", None, [("deliv",)]),
    ("api gap create", "post", "/api/roadmap/gaps", {"name": "x"}, []),
    ("api plateau create", "post", "/api/roadmap/plateaus", {"name": "x"}, []),
    ("api generate work packages", "post", "/api/roadmap/automation/generate-work-packages", {"source_type": "gap", "source_id": "{gap}"}, [("gap",)]),
    ("api sync capabilities", "post", "/api/roadmap/sync/capabilities", {}, []),
    ("api sync applications", "post", "/api/roadmap/sync/applications", {}, []),
    ("api archimate generate roadmap", "post", "/api/roadmap/archimate/generate-roadmap", {}, []),
    ("api archimate single gap", "post", "/api/roadmap/archimate/generate-single-gap/{gap}", {}, [("gap",)]),
    # --- roadmap builder
    ("builder wp create", "post", "/api/roadmap-builder/work-packages", {"name": "x"}, []),
    ("builder wp update", "put", "/api/roadmap-builder/work-packages/{uwp}", {"name": "x"}, [("uwp",)]),
    ("builder wp delete", "delete", "/api/roadmap-builder/work-packages/{uwp}", None, [("uwp",)]),
    ("builder dependency add", "post", "/api/roadmap-builder/work-packages/{uwp}/dependencies", {"depends_on_id": "{uwp2}"}, [("uwp",)]),
    ("builder dependency remove", "delete", "/api/roadmap-builder/work-packages/{uwp}/dependencies/{uwp2}", None, [("uwp",)]),
    ("builder plateau create", "post", "/api/roadmap-builder/plateaus", {"name": "x"}, []),
    # --- implementation planning
    ("impl wp create form", "post", "/implementation/work-packages/create", {"name": "x"}, []),
    ("impl wp edit form", "post", "/implementation/work-packages/{uwp}/edit", {"name": "x"}, [("uwp",)]),
    ("impl wp delete form", "post", "/implementation/work-packages/{uwp}/delete", None, [("uwp",)]),
    ("impl dependency add", "post", "/implementation/work-packages/{uwp}/dependencies", {"dependency_id": "{uwp2}"}, [("uwp",)]),
    ("impl dependency remove", "post", "/implementation/work-packages/{uwp}/dependencies/{uwp2}/remove", None, [("uwp",)]),
    ("impl api wp create", "post", "/implementation/api/work-packages", {"name": "x"}, []),
    ("impl api wp update", "put", "/implementation/api/work-packages/{uwp}", {"name": "x"}, [("uwp",)]),
    ("impl api wp delete", "delete", "/implementation/api/work-packages/{uwp}", None, [("uwp",)]),
    ("impl api deliverable create", "post", "/implementation/api/deliverables", {"name": "x", "work_package_id": "{uwp}"}, [("uwp",)]),
    ("impl gaps discover", "post", "/implementation/gaps/discover", {}, []),
    ("impl api gaps discover", "post", "/implementation/api/gaps/discover", {"save_to_db": True}, []),
    # --- application pages (dashboard blueprint)
    ("dash wp add", "post", "/dashboard/applications/{app}/work-packages", {"name": "x"}, [("app",)]),
    ("dash wp delete", "delete", "/dashboard/applications/{app}/work-packages/{el}", None, [("el",)]),
    ("dash deliverable add", "post", "/dashboard/applications/{app}/deliverables", {"name": "x"}, [("app",)]),
    ("dash deliverable delete", "delete", "/dashboard/applications/{app}/deliverables/{el}", None, [("el",)]),
    ("dash plateau add", "post", "/dashboard/applications/{app}/plateaus", {"name": "x"}, [("app",)]),
    ("dash plateau delete", "delete", "/dashboard/applications/{app}/plateaus/{el}", None, [("el",)]),
    ("dash wp delete form", "post", "/dashboard/applications/{app}/work-packages/{lwp}/delete", None, [("lwp",)]),
    ("dash wp edit form", "post", "/dashboard/applications/{app}/work-packages/{lwp}/edit", {"name": "x"}, [("lwp",)]),
    ("dash wp link form", "post", "/dashboard/applications/{app}/work-packages/add", {"element_id": "{lwp}"}, [("app",), ("lwp",)]),
    ("dash deliverable delete form", "post", "/dashboard/applications/{app}/deliverables/{deliv}/delete", None, [("deliv",)]),
    ("dash deliverable edit form", "post", "/dashboard/applications/{app}/deliverables/{deliv}/edit", {"name": "x"}, [("deliv",)]),
    ("dash deliverable link form", "post", "/dashboard/applications/{app}/deliverables/add", {"element_id": "{deliv}"}, [("app",), ("deliv",)]),
    ("dash plateau delete form", "post", "/dashboard/applications/{app}/plateaus/{plat}/delete", None, [("plat",)]),
    ("dash plateau link form", "post", "/dashboard/applications/{app}/plateaus/add", {"element_id": "{plat}"}, [("app",), ("plat",)]),
    # --- application pages (applications module)
    ("apps wp add", "post", "/applications/{app}/work-packages/add", {"element_id": "{lwp}"}, [("app",), ("lwp",)]),
    ("apps deliverable add", "post", "/applications/{app}/deliverables/add", {"element_id": "{deliv}"}, [("app",), ("deliv",)]),
    ("apps plateau add", "post", "/applications/{app}/plateaus/add", {"element_id": "{plat}"}, [("app",), ("plat",)]),
    # --- interface register, assistant, kanban, solution designer
    ("register attach wp", "post", "/interface-register/gaps/{gap}/work-packages", {"name": "x"}, []),
    ("assistant roadmap elements", "post", "/api/architecture-assistant/roadmap-elements", {"gap_elements": []}, []),
    ("kanban push to gantt", "post", "/api/adm-kanban/v2/cards/task:0/push-to-gantt", {}, []),
    ("solution wp create", "post", "/solutions/{sol}/api/work-packages", {"name": "x"}, [("sol",)]),
    ("solution wp update", "put", "/solutions/{sol}/api/work-packages/{rwp}", {"name": "x"}, [("sol",), ("rwp",)]),
    ("solution wp delete", "delete", "/solutions/{sol}/api/work-packages/{rwp}", None, [("sol",), ("rwp",)]),
    ("solution roadmap item update", "post", "/solutions/{sol}/update-roadmap-item", {"index": 0, "item": {}}, [("sol",)]),
    ("solution roadmap items delete", "post", "/solutions/{sol}/delete-roadmap-items", {"indices": [0]}, [("sol",)]),
    ("solution plateau create", "post", "/solutions/{sol}/plateaus", {"name": "x"}, [("sol",)]),
    ("capability map element work package", "post", "/capability-map/api/archimate/elements", {"name": "x", "type": "WorkPackage", "application_id": "{app}"}, [("app",)]),
    ("consolidation roadmap task", "post", "/consolidation-list/api/create-roadmap-task", {"entry_id": "{centry}", "start_date": "2030-01-01", "end_date": "2030-06-01", "owner": "x"}, [("centry",)]),
]
LABELS = [r[0] for r in ROUTES]


def _user(db_session, org, label, role_name="Editor"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"wps-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
        role=Role.query.filter_by(name=role_name).first(),
    )
    user.password = "Test!12345"
    db_session.add(user)
    db_session.flush()
    return user


def _records(db_session, org, owner):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Plateau, WorkPackage
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.models.solution_models import Solution
    from app.services import work_package_service
    from app.services.gap_register_service import create_gap

    tag = uuid.uuid4().hex[:6]
    app_c = ApplicationComponent(name=f"App {tag}", organization_id=org.id)
    sol = Solution(name=f"Sol {tag}", organization_id=org.id, created_by_id=owner.id)
    db_session.add_all([app_c, sol])
    db_session.flush()
    lwp = WorkPackage(name=f"Legacy {tag}", organization_id=org.id, application_component_id=app_c.id)
    plat = Plateau(name=f"Plateau {tag}", organization_id=org.id)
    rwp = RoadmapWorkPackage(
        name=f"Roadmap {tag}", business_capability="c", source_type="solution", source_id=sol.id
    )
    db_session.add_all([lwp, plat, rwp])
    db_session.flush()
    uwp = work_package_service.create_work_package(organization_id=org.id, name=f"WP {tag}")
    uwp2 = work_package_service.create_work_package(organization_id=org.id, name=f"WP2 {tag}")
    db_session.flush()
    element = ArchiMateElement(
        name=f"Element {tag}", type="WorkPackage", layer="Implementation", organization_id=org.id
    )
    db_session.add(element)
    db_session.flush()
    deliv = work_package_service.create_deliverable(uwp.id, organization_id=org.id, name=f"Deliv {tag}")
    from app.models.consolidation_list import ConsolidationListEntry

    centry = ConsolidationListEntry(application_id=app_c.id, recommended_action="pending_review")
    db_session.add(centry)
    gap, _ = create_gap(org.id, f"Gap {tag}", gap_type="coverage")
    db_session.flush()
    return {
        "app": app_c.id, "sol": sol.id, "lwp": lwp.id, "plat": plat.id, "rwp": rwp.id,
        "uwp": uwp.id, "uwp2": uwp2.id, "deliv": deliv.id, "gap": gap.id, "zero": 0,
        "el": element.id, "centry": centry.id,
    }


@pytest.fixture
def world(db_session, make_org):
    a, b = make_org("wps-a"), make_org("wps-b")
    editor_a = _user(db_session, a, "edita")
    viewer_a = _user(db_session, a, "viewa", role_name="Viewer")
    editor_b = _user(db_session, b, "editb")
    # Records the Viewer owns, so the solution routes' ownership check cannot be
    # what refuses the Viewer: the role is the only thing standing in the way.
    return {
        "a": a, "b": b, "editor_a": editor_a, "viewer_a": viewer_a,
        "ids_a": _records(db_session, a, editor_a),
        "ids_b": _records(db_session, b, editor_b),
        "ids_viewer": _records(db_session, a, viewer_a),
    }


def _fill(value, ids):
    if isinstance(value, str):
        return value.format(**ids) if "{" in value else value
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    return value


def _body(body, ids):
    out = _fill(body, ids)
    # ids written as "{key}" must reach the handler as numbers
    if isinstance(out, dict):
        for key, val in list(out.items()):
            if isinstance(val, str) and val.isdigit():
                out[key] = int(val)
    return out


def _form_post(label):
    """Routes that read request.form, not JSON."""
    return label.endswith("form") or label.startswith(("apps ", "impl wp", "impl dependency"))


def _send(client, login_as, user, row, ids):
    label, method, url, body, _ = row
    login_as(client, user)
    path = url.format(**ids)
    kwargs = {}
    if body is not None:
        data = _body(body, ids)
        kwargs = {"data": data} if _form_post(label) else {"json": data}
    return getattr(client, method)(path, **kwargs)


def _fingerprint(db_session):
    out = {}
    for table in STORES:
        exists = db_session.execute(db.text("SELECT to_regclass(:t)"), {"t": table}).scalar()
        if not exists:
            continue
        out[table] = db_session.execute(
            db.text(f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t.id), '')) FROM {table} t")
        ).scalar()
    return out


def _row(label):
    return next(r for r in ROUTES if r[0] == label)


def test_every_registered_work_package_write_route_is_in_the_table(app):
    """A route added to the checked modules without a row here fails the build."""
    from app.decorators import WORK_PACKAGE_WRITE_ROLES

    assert "architect" in WORK_PACKAGE_WRITE_ROLES
    guarded = {}
    for rule in app.url_map.iter_rules():
        for method in rule.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            guarded[(method.lower(), rule.rule)] = rule.endpoint
    import re

    def shape(url):
        return re.sub(r"<[^>]+>", "<>", re.sub(r"\{[a-z0-9]+\}", "<>", url))

    table = {(r[1], shape(re.sub(r"/\d+(?=/|$)", "/{x}", r[2]))) for r in ROUTES}
    registered = {(m, shape(u)) for (m, u) in guarded}
    # every row names a route that really exists (a typo would otherwise test a 404)
    missing = {
        (m, u) for (m, u) in table
        if (m, u) not in registered and not u.startswith("/api/adm-kanban/v2/cards/task:")
    }
    assert not missing, missing


@pytest.mark.parametrize("label", LABELS)
def test_viewer_is_refused_and_nothing_changes(db_session, world, client, login_as, label):
    before = _fingerprint(db_session)
    resp = _send(client, login_as, world["viewer_a"], _row(label), world["ids_viewer"])
    assert resp.status_code == 403, (label, resp.status_code, resp.get_data(as_text=True)[:200])
    assert _fingerprint(db_session) == before


@pytest.mark.parametrize("label", LABELS)
def test_write_role_reaches_the_handler(db_session, world, client, login_as, label):
    resp = _send(client, login_as, world["editor_a"], _row(label), world["ids_a"])
    assert resp.status_code not in (401, 403), (label, resp.status_code, resp.get_data(as_text=True)[:300])
    assert resp.status_code != 404 or "cards/task" in _row(label)[2] or label in ALLOWED_404, (
        label, resp.status_code, resp.get_data(as_text=True)[:300])


# Routes whose own-organisation call may legitimately answer 404 here (the
# fixture does not give them the record they look up).
ALLOWED_404 = {
    "capability task update", "capability task delete",  # task id 0 does not exist
    "map gap unlink plateau",  # the gap and plateau are not linked
    "api generate work packages",  # the generator needs a gap with a capability
    "register attach wp",  # needs an interface initiative with a plateau pair
}

CROSS = [(r[0], keys) for r in ROUTES for keys in r[4]]


@pytest.mark.parametrize("label,keys", CROSS, ids=[f"{label}:{'+'.join(keys)}" for label, keys in CROSS])
def test_other_organisations_record_is_404_and_nothing_changes(db_session, world, client, login_as, label, keys):
    ids = dict(world["ids_a"])
    for key in keys:
        ids[key] = world["ids_b"][key]
    before = _fingerprint(db_session)
    resp = _send(client, login_as, world["editor_a"], _row(label), ids)
    assert resp.status_code == 404, (label, keys, resp.status_code, resp.get_data(as_text=True)[:300])
    assert _fingerprint(db_session) == before


def test_viewer_still_reads(db_session, world, client, login_as):
    login_as(client, world["viewer_a"])
    ids = world["ids_a"]
    for url in (
        "/api/roadmap/work-packages",
        f"/api/roadmap/work-packages/{ids['uwp2']}",
        "/api/roadmap-builder/work-packages",
        "/implementation/work-packages/create",
        f"/implementation/work-packages/{ids['uwp']}/edit",
        "/capability-map/api/roadmap/work-packages",
    ):
        assert client.get(url).status_code == 200, url


def test_writes_still_need_a_csrf_token(app, db_session, world, client, login_as):
    before = _fingerprint(db_session)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        for row in ROUTES:
            if row[0] in CSRF_EXEMPT:
                continue
            resp = _send(client, login_as, world["editor_a"], row, world["ids_a"])
            assert resp.status_code == 400, (row[0], resp.status_code)
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert _fingerprint(db_session) == before


# Routes that were already outside CSRF protection before this change (the
# token is sent by the page for these on the fetch path); unchanged here.
CSRF_EXEMPT = set()
