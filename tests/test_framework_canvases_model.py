"""The twelve framework canvases read and write the organisation's model through one service.

Each canvas type picks a layout and the element types of its boxes; an element sits in a box of a canvas's view,
so the same element can be on several canvases, in a different box on each. Nothing is kept as free text, and a
canvas only ever touches its own organisation's elements.
"""

import re
import uuid
from pathlib import Path

import pytest

from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from app.models.archimate_core import ArchiMateElement, SavedDiagramElement
from app.models.business_model import BusinessModelCanvas
from app.modules.business_model_canvas import model_service as ms
from app.modules.business_model_canvas.box_meta import canvas_box_meta

LAYOUTS = Path(__file__).resolve().parents[1] / "app" / "templates" / "components" / "canvas"

# What each box takes, from the canvas mapping (ArchiMate 3.2): the first type is the one a typed name creates.
TAKES = {
    "lean_canvas": {
        "problem": ["Assessment"], "existing_alternatives": ["BusinessActor", "Product", "BusinessProcess"],
        "solution": ["BusinessService", "ApplicationService", "Product"], "key_metrics": ["Metric", "Outcome"],
        "unique_value_proposition": ["Value"], "high_level_concept": ["Value"],
        "unfair_advantage": ["Resource", "Capability"], "channels": ["BusinessInterface", "ApplicationInterface"],
        "customer_segments": ["BusinessActor"], "early_adopters": ["BusinessActor"],
    },
    "empathy_map": {
        "user": ["BusinessRole", "Stakeholder"], "says": ["EvidenceRecord"], "thinks": ["EvidenceRecord"],
        "does": ["BusinessProcess"], "feels": ["EvidenceRecord"], "pains": ["Assessment"], "gains": ["Goal", "Outcome"],
    },
    "swot_analysis": {k: ["Assessment"] for k in ("strengths", "weaknesses", "opportunities", "threats")},
    "pestle_analysis": {
        **{k: ["Driver"] for k in ("political", "economic", "social", "technological", "legal")},
        "environmental": ["Driver", "Risk"],
    },
    "porters_five_forces": {
        "new_entrants": ["Driver", "Assessment", "BusinessActor"], "suppliers": ["Driver", "Assessment", "BusinessActor"],
        "rivalry": ["Driver", "Assessment", "BusinessActor"], "buyers": ["Driver", "Assessment", "BusinessActor"],
        "substitutes": ["Driver", "Assessment", "Product"],
    },
    "ansoff_matrix": {
        "market_penetration": ["CourseOfAction", "Outcome"], "product_development": ["CourseOfAction", "Product", "Outcome"],
        "market_development": ["CourseOfAction", "BusinessActor", "WorkPackage"],
        "diversification": ["CourseOfAction", "Product", "BusinessActor", "WorkPackage"],
    },
    "mckinsey_7s": {
        "shared_values": ["Principle"], "strategy": ["CourseOfAction", "Goal", "Outcome"],
        "structure": ["BusinessActor", "BusinessRole", "Location"],
        "systems": ["BusinessProcess", "ApplicationComponent", "Equipment"],
        "skills": ["Capability", "Skill", "Assessment", "Gap"], "style": ["Assessment"],
        "staff": ["Resource", "Outcome", "WorkPackage"],
    },
    "blue_ocean": {
        "factors": ["Value"], "eliminate": ["CourseOfAction"], "raise": ["CourseOfAction"],
        "reduce": ["CourseOfAction"], "create": ["CourseOfAction", "BusinessService"],
    },
    "wardley_mapping": {
        "user": ["BusinessActor", "BusinessRole"], "user_need": ["Goal", "Value"],
        **{k: ["BusinessService", "ApplicationComponent", "ApplicationService", "Capability", "Resource",
               "BusinessProcess", "Facility", "DataObject", "Material", "TechnologyService"]
           for k in ("genesis", "custom_built", "product", "commodity")},
        "movement": ["WorkPackage"], "inertia": ["Assessment", "Constraint"],
    },
    "balanced_scorecard": {
        **{k: ["Goal"] for k in ("financial", "internal", "vision", "customer", "learning")},
        "strategy": ["CourseOfAction"], "measures": ["Metric"], "targets": ["Outcome"], "initiatives": ["WorkPackage"],
    },
    "okr": {"objective": ["Goal"], "key_results": ["Outcome"], "initiatives": ["WorkPackage"]},
    "product_vision_board": {
        "vision": ["Goal"], "target_group": ["BusinessActor", "BusinessRole"], "needs": ["Goal", "Assessment"],
        "product": ["Product", "BusinessService"], "business_goals": ["Goal", "Outcome"],
    },
}


def _canvas(db_session, org, canvas_type, name=None):
    canvas = BusinessModelCanvas(name=name or f"Test {canvas_type}", organization_id=org.id, canvas_type=canvas_type)
    db_session.add(canvas)
    db_session.flush()
    return canvas


def _element(db_session, org, name, type_):
    el = ArchiMateElement(name=name, type=type_, layer="business", organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    return el


@pytest.mark.parametrize("canvas_type", list(TAKES))
def test_each_type_takes_the_element_types_its_mapping_names(canvas_type):
    zones = {z["box_key"]: z for z in CANVAS_TEMPLATES[canvas_type]["zones"] if z["membership"] != "attribute"}
    assert {k: z["element_types"] for k, z in zones.items()} == TAKES[canvas_type]


@pytest.mark.parametrize("canvas_type", list(TAKES))
def test_each_types_boxes_are_the_boxes_its_layout_draws(canvas_type):
    layout = (LAYOUTS / f"{canvas_type}.html").read_text(encoding="utf8")
    drawn = set(re.findall(r"\{'key': '([a-z0-9-]+)'", layout))
    if canvas_type == "wardley_mapping":  # drawn as a plot: the stages come from the plot, the anchors and extras from its notes
        from app.modules.business_model_canvas import wardley_service

        drawn = {k for k, _t in wardley_service.STAGES} | {"user", "user-need", "movement", "inertia"}
    stored = {z["box_key"].replace("_", "-") for z in CANVAS_TEMPLATES[canvas_type]["zones"]}
    assert drawn == stored


@pytest.mark.parametrize("canvas_type", list(TAKES))
def test_every_box_says_what_it_takes(canvas_type):
    for zone in CANVAS_TEMPLATES[canvas_type]["zones"]:
        meta = canvas_box_meta(canvas_type.replace("_", "-"), zone["box_key"].replace("_", "-"))
        assert meta["guidance"] and meta["element"] != "Note" or zone["membership"] == "attribute", zone["box_key"]


def test_creating_any_of_the_twelve_types_stores_the_type(db_session, make_org, tenant_ctx):
    from app.modules.business_model_canvas import service

    org = make_org("fw-create")
    with tenant_ctx(org.id):
        for canvas_type in TAKES:
            canvas = service.create_canvas(name=canvas_type, canvas_type=canvas_type)
            assert ms.template_key(canvas) == canvas_type
        with pytest.raises(ValueError):
            service.create_canvas(name="x", canvas_type="no_such_canvas")
        with pytest.raises(ValueError):
            service.create_canvas(name="x", canvas_type="business_case")
        assert ms.template_key(service.create_canvas(name="plain")) == "business_model_canvas"


@pytest.mark.parametrize("canvas_type", list(TAKES))
def test_a_typed_name_creates_the_boxes_first_type_on_every_canvas(db_session, make_org, tenant_ctx, canvas_type):
    org = make_org(f"fw-add-{canvas_type}"[:40])
    canvas = _canvas(db_session, org, canvas_type)
    with tenant_ctx(org.id):
        for box, types in TAKES[canvas_type].items():
            el = ms.add_entry(canvas, box, name=f"{box} entry")
            assert el.type == types[0] and el.organization_id == org.id
            assert [e.id for e in ms.entries(canvas, box)] == [el.id]
        # a box takes only its own types
        first = next(iter(TAKES[canvas_type]))
        stranger = _element(db_session, org, "Not a fit", "Gap" if "Gap" not in TAKES[canvas_type][first] else "Location")
        with pytest.raises(ms.CanvasError):
            ms.add_entry(canvas, first, element_id=stranger.id)


def test_the_boxes_with_no_good_fit_store_an_element_not_text(db_session, make_org, tenant_ctx):
    org = make_org("fw-nofit")
    canvas = _canvas(db_session, org, "empathy_map")
    with tenant_ctx(org.id):
        thought = ms.add_entry(canvas, "thinks", name="Wonders if the vendor will still exist")
        feeling = ms.add_entry(canvas, "feels", name="Anxious before an audit")
        assert (thought.type, feeling.type) == ("EvidenceRecord", "EvidenceRecord")
        assert ArchiMateElement.query.filter_by(organization_id=org.id, type="EvidenceRecord").count() == 2


def test_one_element_sits_in_a_different_box_on_each_canvas(db_session, make_org, tenant_ctx):
    org = make_org("fw-shared")
    lean = _canvas(db_session, org, "lean_canvas")
    vision = _canvas(db_session, org, "product_vision_board")
    bmc = _canvas(db_session, org, None, "Plain business model")
    with tenant_ctx(org.id):
        segment = ms.add_entry(lean, "customer_segments", name="Municipal water utilities")
        again = ms.add_entry(vision, "target_group", element_id=segment.id)
        third = ms.add_entry(bmc, "customer_segments", name="municipal   WATER utilities")
        assert segment.id == again.id == third.id
        assert ArchiMateElement.query.filter_by(organization_id=org.id, name="Municipal water utilities").count() == 1
        assert [e.id for e in ms.entries(lean, "customer_segments")] == [segment.id]
        assert [e.id for e in ms.entries(vision, "target_group")] == [segment.id]
        assert ms.entries(lean, "early_adopters") == [] and ms.entries(vision, "needs") == []
        boxes = {
            SavedDiagramElement.query.filter_by(diagram_id=c.saved_diagram_id, element_id=segment.id).one().canvas_box
            for c in (lean, vision, bmc)
        }
        assert boxes == {"customer_segments", "target_group"}
        # removing it from one canvas leaves it on the others and in the model
        ms.remove_entry(lean, "customer_segments", segment.id)
        assert ms.entries(lean, "customer_segments") == []
        assert [e.id for e in ms.entries(vision, "target_group")] == [segment.id]


def test_the_second_canvas_offers_the_element_without_retyping(db_session, make_org, tenant_ctx):
    org = make_org("fw-offer")
    swot = _canvas(db_session, org, "swot_analysis")
    lean = _canvas(db_session, org, "lean_canvas")
    with tenant_ctx(org.id):
        wait = ms.add_entry(swot, "weaknesses", name="Customers wait weeks")
        assert wait.id in [e.id for e in ms.candidates(lean, "problem")]
        ms.add_entry(lean, "problem", element_id=wait.id)
        assert ms.candidates(lean, "problem") == []
        assert ArchiMateElement.query.filter_by(organization_id=org.id, name="Customers wait weeks").count() == 1


def test_an_element_has_one_box_on_a_canvas(db_session, make_org, tenant_ctx):
    org = make_org("fw-onebox")
    swot = _canvas(db_session, org, "swot_analysis")
    with tenant_ctx(org.id):
        el = ms.add_entry(swot, "strengths", name="Trusted certificates")
        with pytest.raises(ms.CanvasError):
            ms.add_entry(swot, "weaknesses", element_id=el.id)
        assert ms.entries(swot, "weaknesses") == []


def test_the_lean_canvas_prices_its_solutions(db_session, make_org, tenant_ctx):
    org = make_org("fw-lean-price")
    lean = _canvas(db_session, org, "lean_canvas")
    swot = _canvas(db_session, org, "swot_analysis")
    with tenant_ctx(org.id):
        service = ms.add_entry(lean, "solution", name="Live tracking")
        plan = ms.add_price_plan(lean, service.id, "Pro", "149", "gbp", "month", "subscription")
        assert [p.id for p in ms.price_plans(lean)] == [plan.id]
        assert ms.priceable_box(swot) is None
        with pytest.raises(ms.CanvasError):
            ms.add_price_plan(swot, service.id, "Pro", "149", "gbp", "month")


def test_two_organisations_never_share_a_framework_canvas(db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("fw-iso-a"), make_org("fw-iso-b")
    canvas_a = _canvas(db_session, org_a, "swot_analysis")
    canvas_b = _canvas(db_session, org_b, "swot_analysis")
    theirs = _element(db_session, org_b, "Only org B knows this", "Assessment")
    with tenant_ctx(org_a.id):
        mine = ms.add_entry(canvas_a, "strengths", name="Org A strength")
        assert theirs.id not in [e.id for e in ms.candidates(canvas_a, "strengths")]
        with pytest.raises(ms.CanvasError):
            ms.add_entry(canvas_a, "strengths", element_id=theirs.id)
        assert ms.add_entry(canvas_a, "weaknesses", name="Only org B knows this").id != theirs.id
    with tenant_ctx(org_b.id):
        ms.add_entry(canvas_b, "threats", element_id=theirs.id)
        assert [e.id for e in ms.entries(canvas_b, "threats")] == [theirs.id]
        assert mine.id not in [e.id for e in ms.candidates(canvas_b, "strengths")]
        assert ms.entries(canvas_b, "strengths") == []


def test_a_signed_in_user_of_another_organisation_gets_not_found_on_every_type(db_session, make_org, client, login_as):
    from tests.test_business_model_canvas_model import _user

    org_a, org_b = make_org("fw-404-a"), make_org("fw-404-b")
    canvases = [_canvas(db_session, org_a, t) for t in TAKES]
    owner, outsider = _user(db_session, org_a), _user(db_session, org_b)
    login_as(client, outsider)
    for canvas in canvases:
        assert client.get(f"/business-model/{canvas.id}").status_code == 404, canvas.canvas_type
        assert client.get(f"/business-model/{canvas.id}/export").status_code == 404, canvas.canvas_type
    box = ms.element_boxes(canvases[0])[0]
    response = client.post(
        f"/business-model/{canvases[0].id}/entries/add", data={"box": box, "name": "Intruder"},
    )
    assert response.status_code == 404
    assert ArchiMateElement.query.filter_by(name="Intruder").count() == 0
    # the owner's organisation opens every type, each drawn by its own layout
    login_as(client, owner)
    for canvas in canvases:
        page = client.get(f"/business-model/{canvas.id}")
        assert page.status_code == 200, canvas.canvas_type
        assert f'data-canvas="{canvas.canvas_type.replace("_", "-")}"' in page.get_data(as_text=True), canvas.canvas_type


def test_the_view_row_keeps_the_box_so_an_export_names_the_empty_ones(db_session, make_org, tenant_ctx):
    from app.services.composer_export_formats import canvas_export_reasons

    org = make_org("fw-export")
    swot = _canvas(db_session, org, "swot_analysis")
    with tenant_ctx(org.id):
        ms.add_entry(swot, "strengths", name="Trusted certificates")
        reasons = {r["box_key"] for r in canvas_export_reasons("swot_analysis", swot.saved_diagram_id)}
        assert reasons == {"weaknesses", "opportunities", "threats"}


def test_the_demonstration_seed_shares_elements_instead_of_copying_them(app, db_session, make_org, tenant_ctx):
    from app.commands import seed_framework_canvases as seed

    org = make_org("fw-seed")
    with tenant_ctx(org.id):
        created, _made = seed.seed(org.id)
        assert created == len(TAKES)
        assert seed.seed(org.id) == (0, 0)
        for name in (seed.SEG_MUNICIPAL, seed.A_WAIT, seed.GOAL_VISION, seed.PKG_RIGS):
            assert ArchiMateElement.query.filter_by(organization_id=org.id, name=name).count() == 1, name
        canvases = {c.canvas_type: c for c in BusinessModelCanvas.query.filter_by(organization_id=org.id).all()}
        assert set(canvases) == set(TAKES)
        for canvas_type, canvas in canvases.items():
            for box in ms.element_boxes(canvas):
                assert ms.entries(canvas, box), (canvas_type, box)
        on = [c for c in canvases.values() if seed.SEG_MUNICIPAL in [e.name for box in ms.element_boxes(c) for e in ms.entries(c, box)]]
        assert len(on) >= 4
