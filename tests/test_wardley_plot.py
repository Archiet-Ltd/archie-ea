"""The Wardley Map is a plot: a component's column is its evolution stage, its row is how many dependency steps it
sits from the user's need, and the lines are relationships the organisation already holds."""

import pytest

from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.business_model import BusinessModelCanvas
from app.modules.business_model_canvas import model_service as ms
from app.modules.business_model_canvas import wardley_service


def _canvas(db_session, org):
    canvas = BusinessModelCanvas(name="Map", organization_id=org.id, canvas_type="wardley_mapping")
    db_session.add(canvas)
    db_session.flush()
    return canvas


def _put(db_session, org, canvas, box, name, type_):
    el = ArchiMateElement(name=name, type=type_, layer="business", organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    ms.place(canvas, box, el)
    db_session.flush()
    return el


def _link(db_session, org, a, b):
    db_session.add(ArchiMateRelationship(source_id=a.id, target_id=b.id, type="Serving", organization_id=org.id))
    db_session.flush()


def _notes(canvas):
    return {
        box.replace("_", "-"): [{"id": e.id, "text": e.name, "href": f"/e/{e.id}", "box": box} for e in ms.entries(canvas, box)]
        for box in ms.element_boxes(canvas)
    }


def _chain(db_session, org):
    canvas = _canvas(db_session, org, )
    user = _put(db_session, org, canvas, "user", "Operator", "BusinessRole")
    need = _put(db_session, org, canvas, "user_need", "Trusted readings", "Value")
    svc = _put(db_session, org, canvas, "product", "Certificate service", "BusinessService")
    cap = _put(db_session, org, canvas, "custom_built", "Calibration", "Capability")
    host = _put(db_session, org, canvas, "commodity", "Hosting", "TechnologyService")
    loose = _put(db_session, org, canvas, "genesis", "Unlinked idea", "Capability")
    _link(db_session, org, svc, need)
    _link(db_session, org, cap, svc)
    _link(db_session, org, host, cap)
    return canvas, dict(user=user, need=need, svc=svc, cap=cap, host=host, loose=loose)


def test_visibility_is_the_number_of_dependency_steps_from_the_need(db_session, make_org, tenant_ctx):
    org = make_org("wardley-depth")
    with tenant_ctx(org.id):
        canvas, e = _chain(db_session, org)
        plot = wardley_service.plot(canvas, _notes(canvas))
    by_name = {c["name"]: c for c in plot["components"]}
    assert [by_name[n]["depth"] for n in ("Certificate service", "Calibration", "Hosting")] == [1, 2, 3]
    # deeper is lower on the map, and a later stage is further right
    assert by_name["Certificate service"]["y"] < by_name["Calibration"]["y"] < by_name["Hosting"]["y"]
    assert by_name["Calibration"]["x"] < by_name["Certificate service"]["x"] < by_name["Hosting"]["x"]
    # a component with no path to the need is on the bottom row and says so
    assert by_name["Unlinked idea"]["y"] > by_name["Hosting"]["y"]
    rows = {r["name"]: r for r in plot["table"]}
    assert rows["Unlinked idea"]["visibility"] == "Not yet linked to the need"
    assert rows["Calibration"]["deps"] == "Hosting" and rows["Calibration"]["stage"] == "Custom-built"


def test_lines_join_the_need_down_through_every_dependency(db_session, make_org, tenant_ctx):
    org = make_org("wardley-lines")
    with tenant_ctx(org.id):
        canvas, e = _chain(db_session, org)
        plot = wardley_service.plot(canvas, _notes(canvas))
    pairs = {(e["from"], e["to"]) for e in plot["edges"]}
    assert {("Trusted readings", "Certificate service"), ("Certificate service", "Calibration"),
            ("Calibration", "Hosting"), ("Operator", "Trusted readings")} <= pairs


def test_movement_is_an_arrow_and_inertia_a_bar_on_the_component_they_name(db_session, make_org, tenant_ctx):
    org = make_org("wardley-move")
    with tenant_ctx(org.id):
        canvas, e = _chain(db_session, org)
        pkg = _put(db_session, org, canvas, "movement", "Productise calibration", "WorkPackage")
        brake = _put(db_session, org, canvas, "inertia", "Three senior technicians", "Assessment")
        orphan = _put(db_session, org, canvas, "inertia", "Unrelated worry", "Assessment")
        _link(db_session, org, pkg, e["cap"])
        _link(db_session, org, brake, e["cap"])
        plot = wardley_service.plot(canvas, _notes(canvas))
    assert [a["component"] for a in plot["arrows"]] == ["Calibration"]
    assert [b["component"] for b in plot["bars"]] == ["Calibration"]
    cap = next(c for c in plot["components"] if c["name"] == "Calibration")
    assert plot["arrows"][0]["x2"] > cap["x"]
    rows = {r["name"]: r for r in plot["table"]}
    assert rows["Unrelated worry"]["deps"].startswith("Not tied to a component yet")


def test_another_organisations_relationship_never_draws_a_line(db_session, make_org, tenant_ctx):
    mine, theirs = make_org("wardley-a"), make_org("wardley-b")
    with tenant_ctx(mine.id):
        canvas, e = _chain(db_session, mine)
        before = len(wardley_service.plot(canvas, _notes(canvas))["edges"])
        # the other organisation holds a relationship between two of my element ids
        db_session.add(ArchiMateRelationship(source_id=e["loose"].id, target_id=e["need"].id, type="Serving", organization_id=theirs.id))
        db_session.flush()
        plot = wardley_service.plot(canvas, _notes(canvas))
    assert len(plot["edges"]) == before
    assert next(r for r in plot["table"] if r["name"] == "Unlinked idea")["visibility"] == "Not yet linked to the need"


def test_an_empty_map_has_nothing_to_draw(db_session, make_org, tenant_ctx):
    org = make_org("wardley-empty")
    with tenant_ctx(org.id):
        canvas = _canvas(db_session, org)
        assert wardley_service.plot(canvas, _notes(canvas)) is None


def test_the_demonstration_map_has_no_near_duplicate_entries(db_session, make_org, tenant_ctx):
    from app.commands import seed_framework_canvases

    org = make_org("wardley-seed")
    with tenant_ctx(org.id):
        seed_framework_canvases.seed(org.id)
        names = [e.name for e in ArchiMateElement.query.filter_by(organization_id=org.id)]
        assert "Rising component prices squeeze margin" not in names
        assert names.count("Component prices rising with supply shortages") == 1
        porters = BusinessModelCanvas.query.filter_by(organization_id=org.id, canvas_type="porters_five_forces").one()
        suppliers = [e.name for e in ms.entries(porters, "suppliers")]
        assert suppliers.count("Component prices rising with supply shortages") == 1 and len(suppliers) == len(set(suppliers))
        wardley = BusinessModelCanvas.query.filter_by(organization_id=org.id, canvas_type="wardley_mapping").one()
        plot = wardley_service.plot(wardley, _notes(wardley))
        assert len(plot["components"]) + len(plot["users"]) + len(plot["needs"]) >= 5 and len(plot["edges"]) >= 4
        assert all(c["depth"] is not None for c in plot["components"])
