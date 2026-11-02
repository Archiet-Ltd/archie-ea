"""The canvases and method frameworks chosen at sign-up are loaded for that organisation and no other.

Two real organisations in every test. The load runs as a background job the choices queue; these tests
run that job directly, as tests/test_onboarding_starter_data.py does.
"""

import re
import uuid

import pytest

from app import db
from app.models.business_model import BusinessModelCanvas
from app.modules.account.services import onboarding_service as onboarding
from app.modules.business_model_canvas import model_service
from app.services.archimate_backbone import create_backbone_element

pytestmark = pytest.mark.usefixtures("db_session")

METHOD_KEYS = ("togaf", "archimate", "safe", "itil4", "cobit2019", "nistcsf")


def _owner(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="canvas.%s@example.test" % uuid.uuid4().hex[:8], first_name="Can", last_name="Vas",
        organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.flush()
    return user


@pytest.fixture
def two(db_session, make_org):
    one, other = make_org("canvas-one"), make_org("canvas-two")
    return {"one": one, "two": other, "one_owner": _owner(db_session, one), "two_owner": _owner(db_session, other)}


def _element(org, element_type, layer, name):
    return create_backbone_element(element_type=element_type, layer=layer, name=name, organization_id=org.id)


def _choose(client, login_as, user, canvases, frameworks):
    login_as(client, user)
    reply = client.post("/account/welcome/choices", json={"industry": None, "frameworks": frameworks, "canvases": canvases})
    assert reply.status_code == 200, reply.get_data(as_text=True)
    return onboarding.run_starter_data(user.organization_id)


def _canvases(org):
    return BusinessModelCanvas.query.filter_by(organization_id=org.id).order_by(BusinessModelCanvas.id).all()


def _boxes(canvas):
    return {box: [el.name for el in model_service.entries(canvas, box)] for box in model_service.element_boxes(canvas)}


def _adopted(org):
    return {
        row.code: row.items for row in db.session.execute(
            db.text(
                "SELECT f.code, (SELECT count(*) FROM compliance_controls c WHERE c.framework_id = f.id) AS items "
                "FROM framework_adoptions a JOIN regulatory_frameworks f ON f.id = a.framework_id "
                "WHERE a.organization_id = :org"), {"org": org.id},
        )
    }


def test_a_chosen_canvas_is_created_and_filled_from_the_model(client, login_as, two):
    _element(two["one"], "BusinessActor", "business", "Small retailers")
    _element(two["one"], "Capability", "strategy", "Order handling")
    _choose(client, login_as, two["one_owner"], ["lean_canvas", "business_model_canvas"], [])
    canvases = {c.name: c for c in _canvases(two["one"])}
    assert set(canvases) == {"Lean Canvas", "Business Model Canvas"}
    assert canvases["Lean Canvas"].canvas_type == "lean_canvas"
    assert canvases["Business Model Canvas"].canvas_type is None
    bmc = _boxes(canvases["Business Model Canvas"])
    assert bmc["customer_segments"] == ["Small retailers"]
    assert bmc["key_activities"] == ["Order handling"]
    assert _boxes(canvases["Lean Canvas"])["customer_segments"] == ["Small retailers"]


def test_a_canvas_with_nothing_in_the_model_is_created_empty_and_says_so(client, login_as, two):
    _choose(client, login_as, two["one_owner"], ["okr"], [])
    (okr,) = _canvases(two["one"])
    assert okr.name == "OKR" and not any(_boxes(okr).values())
    login_as(client, two["one_owner"])
    page = client.get("/account/welcome?step=4").get_data(as_text=True)
    assert "Empty until your model holds elements" in page


def test_the_second_organisation_never_sees_the_first_ones_canvases_or_entries(client, login_as, two):
    _element(two["one"], "BusinessActor", "business", "Only in org one")
    _element(two["two"], "BusinessActor", "business", "Only in org two")
    _choose(client, login_as, two["one_owner"], ["business_model_canvas"], ["soc2", "safe"])
    one = _canvases(two["one"])
    one_boxes = _boxes(one[0])
    _choose(client, login_as, two["two_owner"], ["swot_analysis"], ["togaf"])
    other = _canvases(two["two"])
    assert [c.name for c in one] == ["Business Model Canvas"] and [c.name for c in other] == ["SWOT"]
    assert not {c.id for c in one} & {c.id for c in other}
    assert one_boxes["customer_segments"] == ["Only in org one"]
    assert set(_adopted(two["one"])) == {"SOC-2", "SAFE"} and set(_adopted(two["two"])) == {"TOGAF"}
    login_as(client, two["two_owner"])
    assert client.get("/business-model/%d" % one[0].id).status_code == 404
    listing = client.get("/business-model/").get_data(as_text=True)
    assert "/business-model/%d" % other[0].id in listing and "/business-model/%d" % one[0].id not in listing


def test_loading_again_creates_and_adds_nothing(client, login_as, two):
    _element(two["one"], "BusinessActor", "business", "Small retailers")
    _choose(client, login_as, two["one_owner"], ["lean_canvas"], ["itil4"])
    first = (len(_canvases(two["one"])), sum(len(v) for v in _boxes(_canvases(two["one"])[0]).values()), _adopted(two["one"]))
    again = onboarding.run_starter_data(two["one"].id)
    assert again["canvases_created"] == 0 and again["frameworks_adopted"] == 0
    assert (len(_canvases(two["one"])), sum(len(v) for v in _boxes(_canvases(two["one"])[0]).values()), _adopted(two["one"])) == first


def test_a_canvas_the_person_changed_is_left_alone_on_a_second_load(client, login_as, two):
    _element(two["one"], "BusinessActor", "business", "Small retailers")
    _choose(client, login_as, two["one_owner"], ["lean_canvas"], [])
    (canvas,) = _canvases(two["one"])
    other_box = next(b for b, names in _boxes(canvas).items() if names == ["Small retailers"])
    model_service.remove_entry(canvas, other_box, model_service.entries(canvas, other_box)[0].id)
    onboarding.run_starter_data(two["one"].id)
    assert _boxes(_canvases(two["one"])[0])[other_box] == []


@pytest.mark.parametrize("key", METHOD_KEYS)
def test_every_method_framework_offered_loads_its_own_items(client, login_as, two, key):
    _choose(client, login_as, two["one_owner"], [], [key])
    code = onboarding.FRAMEWORK_CATALOGUES[key]
    assert _adopted(two["one"]).get(code, 0) > 0, "choosing %s loaded nothing" % key


def test_every_canvas_offered_is_created(client, login_as, two):
    keys = [key for key, _name in onboarding.canvas_choices()]
    assert len(keys) >= 12
    _choose(client, login_as, two["one_owner"], keys, [])
    made = {c.canvas_type or "business_model_canvas" for c in _canvases(two["one"])}
    assert made == set(keys)


def test_the_method_frameworks_list_their_own_structure():
    from app.services.compliance.method_frameworks import METHOD_FRAMEWORKS

    by_code = {f["code"]: [c["control_code"] for c in f["controls"]] for f in METHOD_FRAMEWORKS}
    assert by_code["TOGAF"][:3] == ["Prelim", "A", "B"] and "H" in by_code["TOGAF"]
    assert len(by_code["ITIL-4"]) == 34 and len(by_code["COBIT-2019"]) == 40
    assert {"GV.OC", "ID.AM", "PR.DS", "DE.CM", "RS.MA", "RC.RP"} <= set(by_code["NIST-CSF"])
    from app.services.profiles.safe import KINDS

    assert len(by_code["SAFE"]) == len(KINDS["Epic"]["properties"]["kanban_state"]["values"])


def test_the_default_canvases_follow_the_company_kind_and_first_goal():
    assert onboarding.suggested_canvases("startup", None)[:3] == ["lean_canvas", "business_model_canvas", "okr"]
    assert "balanced_scorecard" in onboarding.suggested_canvases("enterprise", "cost")
    assert "ansoff_matrix" in onboarding.suggested_canvases("scaleup", "strategy")


def test_a_canvas_not_on_the_list_is_refused_and_nothing_is_kept(client, login_as, two):
    login_as(client, two["one_owner"])
    reply = client.post("/account/welcome/choices", json={"frameworks": [], "canvases": ["not_a_canvas"]})
    assert reply.status_code == 400
    assert onboarding.chosen_canvases(two["one"]) is None


def test_the_first_screen_lists_what_was_loaded_with_links_that_open_content(client, login_as, two):
    _element(two["one"], "BusinessActor", "business", "Small retailers")
    _choose(client, login_as, two["one_owner"], ["lean_canvas"], ["soc2"])
    login_as(client, two["one_owner"])
    page = client.get("/account/welcome?step=4").get_data(as_text=True)
    assert 'data-testid="signup-loaded-canvas"' in page and 'data-testid="signup-loaded-framework"' in page
    assert "built-in method" not in page and re.search(r"\d+ controls", page)
    (canvas,) = _canvases(two["one"])
    assert "/business-model/%d" % canvas.id in page
    opened = client.get("/business-model/%d" % canvas.id).get_data(as_text=True)
    assert "Small retailers" in opened
    login_as(client, two["two_owner"])
    assert 'data-testid="signup-loaded-card"' not in client.get("/account/welcome?step=4").get_data(as_text=True)


def test_the_dashboard_overview_does_not_carry_the_sign_up_card(client, login_as, two):
    _choose(client, login_as, two["one_owner"], ["lean_canvas"], ["soc2"])
    login_as(client, two["one_owner"])
    assert "Loaded at sign-up" not in client.get("/dashboard/overview").get_data(as_text=True)
    assert "Loaded at sign-up" in client.get("/account/welcome?step=4").get_data(as_text=True)


def test_a_canvas_the_person_deleted_stays_deleted_when_settings_are_saved_unchanged(client, login_as, two):
    _choose(client, login_as, two["two_owner"], ["lean_canvas"], [])
    _choose(client, login_as, two["one_owner"], ["lean_canvas", "okr"], [])
    (lean,) = [c for c in _canvases(two["one"]) if c.canvas_type == "lean_canvas"]
    db.session.delete(lean)
    db.session.commit()
    made = _choose(client, login_as, two["one_owner"], ["lean_canvas", "okr"], [])
    assert made["canvases_created"] == 0
    assert [c.canvas_type for c in _canvases(two["one"])] == ["okr"]
    login_as(client, two["two_owner"])
    assert [c.canvas_type for c in _canvases(two["two"])] == ["lean_canvas"]


def test_unticking_and_ticking_a_canvas_again_loads_it_again(client, login_as, two):
    _choose(client, login_as, two["one_owner"], ["lean_canvas"], [])
    db.session.delete(_canvases(two["one"])[0])
    db.session.commit()
    _choose(client, login_as, two["one_owner"], [], [])
    assert _canvases(two["one"]) == []
    _choose(client, login_as, two["one_owner"], ["lean_canvas"], [])
    assert [c.canvas_type for c in _canvases(two["one"])] == ["lean_canvas"]


def test_a_newly_ticked_canvas_is_added_beside_one_that_was_deleted(client, login_as, two):
    _choose(client, login_as, two["one_owner"], ["lean_canvas"], [])
    db.session.delete(_canvases(two["one"])[0])
    db.session.commit()
    _choose(client, login_as, two["one_owner"], ["lean_canvas", "okr"], [])
    assert [c.canvas_type for c in _canvases(two["one"])] == ["okr"]


def test_the_togaf_items_are_the_architecture_journey_phases():
    from app.models.adm_kanban import ADM_PHASES
    from app.services.compliance.method_frameworks import METHOD_FRAMEWORKS

    togaf = next(f for f in METHOD_FRAMEWORKS if f["code"] == "TOGAF")
    assert [c["control_code"].upper() for c in togaf["controls"]] == [p["code"] for p in sorted(ADM_PHASES, key=lambda p: p["order"])]


def test_a_method_framework_counts_items_and_a_compliance_framework_counts_controls(client, login_as, two):
    _choose(client, login_as, two["one_owner"], [], ["togaf", "soc2"])
    login_as(client, two["one_owner"])
    welcome = client.get("/account/welcome?step=4").get_data(as_text=True)
    assert re.search(r"\d+ items", welcome) and re.search(r"\d+ controls", welcome)
    gallery = client.get("/frameworks/").get_data(as_text=True)

    def card(code):
        return gallery.split('data-framework-card="%s"' % code)[1].split("</a>")[0]

    assert re.search(r"\d+ items", card("TOGAF")) and "control" not in card("TOGAF").split("Adopted")[-1]
    assert re.search(r"\d+ controls", card("SOC-2"))


def _starter_capability(org, name):
    element = _element(org, "Capability", "strategy", name)
    element.source_kind = "starter"
    return element


def _lift(org_id, elements):
    onboarding._lift_from_signup_canvases(org_id, [element.id for element in elements])
    db.session.flush()


def _placed(canvas, element):
    from app.models.archimate_core import SavedDiagramElement

    return SavedDiagramElement.query.filter_by(diagram_id=canvas.saved_diagram_id, element_id=element.id).count()


def test_lifting_starter_elements_leaves_canvases_the_person_made_or_changed_and_other_organisations(client, login_as, two):
    mine, theirs = two["one"], two["two"]
    ours = _starter_capability(mine, "Order handling")
    foreign = _starter_capability(theirs, "Order handling")
    _choose(client, login_as, two["two_owner"], ["lean_canvas"], [])
    theirs_canvas = _canvases(theirs)[0]
    model_service.place(theirs_canvas, next(iter(model_service.element_boxes(theirs_canvas))), foreign)
    db.session.flush()
    _choose(client, login_as, two["one_owner"], ["lean_canvas", "swot_analysis", "okr"], [])
    by_name = {c.name: c for c in _canvases(mine)}
    renamed, redescribed = by_name["SWOT"], by_name["OKR"]
    renamed.name = "Our SWOT"
    redescribed.description = "Reviewed by the team."
    own = BusinessModelCanvas(organization_id=mine.id, name="Our board", canvas_type="pestle_analysis", description="Made by hand")
    db.session.add(own)
    db.session.flush()
    for canvas in (renamed, redescribed, own):
        model_service.place(canvas, next(iter(model_service.element_boxes(canvas))), ours)
    signup_canvas = by_name["Lean Canvas"]
    model_service.place(signup_canvas, next(iter(model_service.element_boxes(signup_canvas))), ours)

    _lift(mine.id, [ours])

    assert _placed(signup_canvas, ours) == 0
    assert _placed(renamed, ours) == 1 and _placed(redescribed, ours) == 1 and _placed(own, ours) == 1
    login_as(client, two["two_owner"])
    assert _placed(theirs_canvas, foreign) == 1


def test_a_persons_canvas_named_like_a_template_keeps_its_element_without_the_sign_up_note(client, login_as, two):
    mine = two["one"]
    ours = _starter_capability(mine, "Order handling")
    _choose(client, login_as, two["one_owner"], ["lean_canvas"], [])
    swot = BusinessModelCanvas(organization_id=mine.id, name="SWOT", canvas_type="swot_analysis", description="Our own SWOT")
    bare = BusinessModelCanvas(organization_id=mine.id, name="Lean Canvas", canvas_type="lean_canvas")
    db.session.add_all([swot, bare])
    db.session.flush()
    for canvas in (swot, bare):
        model_service.place(canvas, next(iter(model_service.element_boxes(canvas))), ours)

    _lift(mine.id, [ours])

    assert _placed(swot, ours) == 1 and _placed(bare, ours) == 1


def test_a_canvas_of_an_unknown_type_is_skipped_and_the_removal_finishes(client, login_as, two, caplog):
    mine = two["one"]
    ours = _starter_capability(mine, "Order handling")
    _choose(client, login_as, two["one_owner"], ["lean_canvas"], [])
    signup_canvas = _canvases(mine)[0]
    model_service.place(signup_canvas, next(iter(model_service.element_boxes(signup_canvas))), ours)
    stray = BusinessModelCanvas(organization_id=mine.id, name="Stray", canvas_type="not_a_framework",
                                description=onboarding.SIGNUP_CANVAS_NOTE, saved_diagram_id=signup_canvas.saved_diagram_id)
    db.session.add(stray)
    db.session.flush()

    with caplog.at_level("WARNING"):
        _lift(mine.id, [ours])

    assert _placed(signup_canvas, ours) == 0
    assert "unknown type" in caplog.text and str(stray.id) in caplog.text


def _viewer(db_session, org):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="viewer.%s@example.test" % uuid.uuid4().hex[:8], first_name="View", last_name="Er",
        organization_id=org.id, enterprise_role="procurement", confirmed=True,
    )
    user.role = Role.query.filter_by(name="User").first() or Role.query.first()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    return user


def _remove_unchanged(org_id, elements):
    onboarding._remove_unchanged_elements(org_id, [(e.id, e.name, e.description) for e in elements])
    db.session.flush()


def test_a_sign_up_canvas_is_unchanged_until_a_person_changes_an_entry_the_name_the_description_or_the_operating_model(client, login_as, two):
    mine = two["one"]
    element = _element(mine, "Assessment", "motivation", "Slow payments")
    _choose(client, login_as, two["one_owner"], ["lean_canvas", "swot_analysis", "okr", "pestle_analysis", "wardley_mapping", "empathy_map"], [])
    by_name = {c.name: c for c in _canvases(mine)}
    assert all(model_service.is_unchanged_signup(c) for c in by_name.values())

    model_service.add_entry(by_name["Lean Canvas"], "problem", name="Customers wait")
    model_service.rename_entry(by_name["Lean Canvas"], "problem", model_service.entries(by_name["Lean Canvas"], "problem")[0].id, "Customers wait a day")
    assert not model_service.is_unchanged_signup(by_name["Lean Canvas"])

    swot = by_name["SWOT"]
    swot_box = next(iter(model_service.element_boxes(swot)))
    model_service.add_entry(swot, swot_box, name="A strength")
    model_service.remove_entry(swot, swot_box, model_service.entries(swot, swot_box)[0].id)
    assert not model_service.is_unchanged_signup(swot) and swot.touched_at is not None

    from app.modules.business_model_canvas import service

    service.update_canvas_meta(by_name["OKR"], operating_model_type="coordination")
    service.update_canvas_meta(by_name["PESTLE"], name="Our PESTLE")
    service.update_canvas_meta(by_name["Wardley Map"], description="Reviewed.")
    assert not any(model_service.is_unchanged_signup(by_name[n]) for n in ("OKR", "PESTLE", "Wardley Map"))
    # saving the details as they already are is not a change
    service.update_canvas_meta(by_name["Empathy Map"], name="Empathy Map", description=onboarding.SIGNUP_CANVAS_NOTE, operating_model_type="")
    assert model_service.is_unchanged_signup(by_name["Empathy Map"]) and element.id


def test_removing_the_unchanged_starter_set_keeps_every_entry_of_a_canvas_a_person_worked_on(client, login_as, two):
    mine = two["one"]
    starters = [_starter_capability(mine, n) for n in ("Order handling", "Billing", "Settlement")]
    _choose(client, login_as, two["one_owner"], ["lean_canvas", "business_model_canvas"], [])
    by_name = {c.name: c for c in _canvases(mine)}
    lean, model = by_name["Lean Canvas"], by_name["Business Model Canvas"]
    boxes = list(model_service.element_boxes(lean))
    for element in starters:
        assert model_service.place(lean, boxes[0], element) or True
    model_service.place(model, next(iter(model_service.element_boxes(model))), starters[0])
    model_service.add_entry(lean, "problem", name="Customers wait")
    db.session.flush()
    on_lean = {r.element_id for r in _view_rows(lean)}

    _remove_unchanged(mine.id, starters)

    assert {r.element_id for r in _view_rows(lean)} == on_lean
    assert all(_placed(lean, e) == 1 for e in starters)
    assert _placed(model, starters[0]) == 0


def _view_rows(canvas):
    from app.models.archimate_core import SavedDiagramElement

    return SavedDiagramElement.query.filter_by(diagram_id=canvas.saved_diagram_id).all()


def test_the_line_under_a_sign_up_canvas_follows_what_it_holds(client, login_as, two):
    mine = two["one"]
    _element(mine, "Assessment", "motivation", "Slow payments")
    _choose(client, login_as, two["one_owner"], ["lean_canvas", "okr"], [])
    by_name = {c.name: c for c in _canvases(mine)}
    assert model_service.shown_description(by_name["Lean Canvas"]) == onboarding.SIGNUP_CANVAS_NOTE
    assert model_service.entry_count(by_name["OKR"]) == 0
    assert model_service.shown_description(by_name["OKR"]) == "Created when you signed up."
    by_name["OKR"].description = "Our own words"
    assert model_service.shown_description(by_name["OKR"]) == "Our own words"


def test_only_an_owner_or_editor_edits_canvas_details_and_never_another_organisations_canvas(client, login_as, two, db_session):
    mine, theirs = two["one"], two["two"]
    _choose(client, login_as, two["one_owner"], ["lean_canvas"], [])
    mine_id = _canvases(mine)[0].id
    _choose(client, login_as, two["two_owner"], ["lean_canvas"], [])
    theirs_id = _canvases(theirs)[0].id
    viewer = _viewer(db_session, mine)

    login_as(client, viewer)
    assert client.post("/business-model/%d/update" % mine_id, data={"name": "Sneaky"}).status_code == 403

    login_as(client, two["one_owner"])
    assert client.post("/business-model/%d/update" % theirs_id, data={"name": "Not yours"}).status_code == 404
    assert client.post("/business-model/%d/update" % mine_id, data={"name": ""}).status_code in (302, 303)
    assert client.post("/business-model/%d/update" % mine_id, data={"name": "Our lean plan", "description": "Why"}).status_code in (302, 303)
    db.session.expire_all()
    assert BusinessModelCanvas.query.filter_by(id=mine_id).one().name == "Our lean plan"
    login_as(client, two["two_owner"])
    assert BusinessModelCanvas.query.filter_by(id=theirs_id).one().name == "Lean Canvas"


def _put_view(client, canvas, placements):
    reply = client.put(
        "/archimate/api/saved-viewpoints/%d" % canvas.saved_diagram_id,
        json={"elements": [{"element_id": e.id, "x": x, "y": y} for e, x, y in placements]},
    )
    assert reply.status_code == 200, reply.get_data(as_text=True)


def test_moving_adding_or_deleting_on_the_composer_view_of_a_sign_up_canvas_marks_it_worked_on(client, login_as, two):
    mine = two["one"]
    starters = [_starter_capability(mine, n) for n in ("Order handling", "Billing")]
    extra = _element(mine, "BusinessActor", "business", "Small retailers")
    other = _starter_capability(two["two"], "Order handling")
    _choose(client, login_as, two["two_owner"], ["lean_canvas"], [])
    foreign = _canvases(two["two"])[0]
    model_service.place(foreign, next(iter(model_service.element_boxes(foreign))), other)
    db.session.commit()
    _choose(client, login_as, two["one_owner"], ["lean_canvas", "swot_analysis", "okr", "pestle_analysis"], [])
    by_name = {c.name: c for c in _canvases(mine)}
    lean, swot, okr, pestle = by_name["Lean Canvas"], by_name["SWOT"], by_name["OKR"], by_name["PESTLE"]
    for canvas in (lean, swot, okr, pestle):
        model_service.place(canvas, next(iter(model_service.element_boxes(canvas))), starters[0])
        model_service.place(canvas, next(iter(model_service.element_boxes(canvas))), starters[1])
    db.session.commit()
    login_as(client, two["one_owner"])
    rows = {r.element_id: (r.position_x, r.position_y) for r in _view_rows(lean)}
    held = [(e, rows[e.id][0], rows[e.id][1]) for e in starters]

    _put_view(client, lean, [(e, x + 80, y + 40) if e is starters[0] else (e, x, y) for e, x, y in held])
    _put_view(client, swot, held + [(extra, 5, 5)])
    _put_view(client, okr, held[:1])
    _put_view(client, pestle, held)
    db.session.expire_all()

    assert not model_service.is_unchanged_signup(lean), "a move left the canvas untouched"
    assert not model_service.is_unchanged_signup(swot), "an added element left the canvas untouched"
    assert not model_service.is_unchanged_signup(okr), "a delete left the canvas untouched"
    assert model_service.is_unchanged_signup(pestle), "saving the view as it was marked the canvas worked on"
    assert db.session.execute(
        db.text("SELECT touched_at FROM business_model_canvases WHERE id = :c"), {"c": foreign.id}
    ).scalar() is None
    _remove_unchanged(mine.id, starters)
    assert _placed(lean, starters[0]) == 1 and _placed(swot, starters[1]) == 1 and _placed(okr, starters[0]) == 1
    assert _placed(pestle, starters[0]) == 0


def test_the_canvas_page_and_its_list_card_describe_an_empty_sign_up_canvas_in_the_same_words(client, login_as, two):
    _choose(client, login_as, two["one_owner"], ["okr"], [])
    (canvas,) = _canvases(two["one"])
    assert canvas.description == onboarding.SIGNUP_CANVAS_NOTE and model_service.entry_count(canvas) == 0
    login_as(client, two["one_owner"])
    shown = model_service.shown_description(canvas)
    assert shown != onboarding.SIGNUP_CANVAS_NOTE
    page = client.get("/business-model/%d" % canvas.id).get_data(as_text=True)
    listing = client.get("/business-model/").get_data(as_text=True)
    assert shown in page and shown in listing
    assert "filled from your model" not in page.split('data-testid="bmc-edit-dialog"')[0]
    assert "filled from your model" not in listing
