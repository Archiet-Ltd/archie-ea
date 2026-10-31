"""The canvases and method frameworks chosen at sign-up are loaded for that organisation and no other.

Two real organisations in every test. The load runs as a background job the choices queue; these tests
run that job directly, as tests/test_onboarding_starter_data.py does.
"""

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
    page = client.get("/dashboard/overview").get_data(as_text=True)
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
    page = client.get("/dashboard/overview").get_data(as_text=True)
    assert 'data-testid="signup-loaded-canvas"' in page and 'data-testid="signup-loaded-framework"' in page
    (canvas,) = _canvases(two["one"])
    assert "/business-model/%d" % canvas.id in page
    opened = client.get("/business-model/%d" % canvas.id).get_data(as_text=True)
    assert "Small retailers" in opened
    login_as(client, two["two_owner"])
    assert 'data-testid="signup-loaded-card"' not in client.get("/dashboard/overview").get_data(as_text=True)
