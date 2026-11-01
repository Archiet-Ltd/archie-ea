"""The Business Model Canvas reads and writes the organisation's model.

Each entry on a box is an element; Revenue Streams reads price plans and Cost
Structure reads cost facts. Nothing is kept as block text, and a canvas only
ever touches its own organisation's elements.
"""

import re
import uuid
from decimal import Decimal

import pytest

from app.models.archimate_core import ArchiMateElement, SavedDiagramElement
from app.models.business_model import BusinessModelCanvas
from app.models.price_plan import PricePlan
from app.modules.business_model_canvas import model_service as ms


def _canvas(db_session, org):
    canvas = BusinessModelCanvas(name="Test canvas", organization_id=org.id)
    db_session.add(canvas)
    db_session.flush()
    return canvas


def _user(db_session, org, role_name="Architect", enterprise_role="business_architect"):
    from app.models.user import Role, User

    user = User(
        email=f"bmc-{uuid.uuid4().hex[:8]}@example.com", first_name="Canvas", last_name="Tester",
        organization_id=org.id, confirmed=True, enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name=role_name).first() or Role(name=role_name)
    db_session.add(role)
    db_session.flush()
    user.role = role
    db_session.commit()
    return user


def test_adding_an_entry_creates_an_element_on_the_canvas_view(db_session, make_org, tenant_ctx):
    org = make_org("bmc-add")
    canvas = _canvas(db_session, org)
    with tenant_ctx(org.id):
        el = ms.add_entry(canvas, "customer_segments", name="Online retailers")
        assert el.type == "BusinessActor"
        assert el.organization_id == org.id
        row = SavedDiagramElement.query.filter_by(diagram_id=canvas.saved_diagram_id, element_id=el.id).one()
        assert row.canvas_box == "customer_segments"
        assert [e.id for e in ms.entries(canvas, "customer_segments")] == [el.id]
        assert ms.entries(canvas, "channels") == []
        assert SavedDiagramElement.query.filter_by(diagram_id=canvas.saved_diagram_id, element_id=el.id).count() == 1


def test_adding_a_name_that_exists_reuses_the_element(db_session, make_org, tenant_ctx):
    org = make_org("bmc-resolve")
    canvas = _canvas(db_session, org)
    existing = ArchiMateElement(name="Courier partners", type="BusinessActor", layer="business", organization_id=org.id)
    db_session.add(existing)
    db_session.flush()
    with tenant_ctx(org.id):
        el = ms.add_entry(canvas, "key_partners", name="  courier   PARTNERS ")
        assert el.id == existing.id
        assert ArchiMateElement.query.filter_by(organization_id=org.id).count() == 1


def test_picker_offers_existing_elements_of_the_boxes_types_first(db_session, make_org, tenant_ctx):
    org = make_org("bmc-pick")
    canvas = _canvas(db_session, org)
    actor = ArchiMateElement(name="Dock operators", type="BusinessActor", layer="business", organization_id=org.id)
    goal = ArchiMateElement(name="Grow", type="Goal", layer="motivation", organization_id=org.id)
    db_session.add_all([actor, goal])
    db_session.flush()
    with tenant_ctx(org.id):
        names = [e.name for e in ms.candidates(canvas, "customer_segments")]
        assert names == ["Dock operators"]
        ms.add_entry(canvas, "customer_segments", element_id=actor.id)
        assert ms.candidates(canvas, "customer_segments") == []
        with pytest.raises(ms.CanvasError):
            ms.add_entry(canvas, "customer_segments", element_id=goal.id)


def test_an_element_has_one_box(db_session, make_org, tenant_ctx):
    org = make_org("bmc-onebox")
    canvas = _canvas(db_session, org)
    with tenant_ctx(org.id):
        el = ms.add_entry(canvas, "customer_segments", name="Shared name")
        with pytest.raises(ms.CanvasError):
            ms.add_entry(canvas, "key_partners", element_id=el.id)


def test_removing_an_entry_keeps_the_element_in_the_model(db_session, make_org, tenant_ctx):
    org = make_org("bmc-remove")
    canvas = _canvas(db_session, org)
    with tenant_ctx(org.id):
        el = ms.add_entry(canvas, "channels", name="Partner portal")
        ms.remove_entry(canvas, "channels", el.id)
        assert ms.entries(canvas, "channels") == []
        assert ArchiMateElement.query.filter_by(id=el.id).count() == 1


def test_renaming_an_entry_renames_the_element(db_session, make_org, tenant_ctx):
    org = make_org("bmc-rename")
    canvas = _canvas(db_session, org)
    with tenant_ctx(org.id):
        el = ms.add_entry(canvas, "key_resources", name="Vans")
        ms.rename_entry(canvas, "key_resources", el.id, "Electric vans")
        assert ArchiMateElement.query.get(el.id).name == "Electric vans"


def test_a_price_is_a_price_plan_not_a_property(db_session, make_org, tenant_ctx):
    org = make_org("bmc-price")
    canvas = _canvas(db_session, org)
    with tenant_ctx(org.id):
        service = ms.add_entry(canvas, "value_propositions", name="Live tracking")
        plan = ms.add_price_plan(canvas, service.id, "Pro", "149", "gbp", "month", "subscription")
        assert plan.price == Decimal("149") and plan.currency == "GBP"
        assert [p.id for p in ms.price_plans(canvas)] == [plan.id]
        assert "price" not in (ArchiMateElement.query.get(service.id).acm_properties or {})
        with pytest.raises(ms.CanvasError):
            ms.add_price_plan(canvas, service.id, "Bad", "free", "GBP", "month")
        ms.remove_price_plan(canvas, plan.id)
        assert PricePlan.query.filter_by(element_id=service.id).count() == 0


def test_cost_structure_reads_the_cost_facts(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.services import cost_fact_store

    org = make_org("bmc-cost")
    canvas = _canvas(db_session, org)
    app_row = ApplicationComponent(name="Billing platform", organization_id=org.id)
    db_session.add(app_row)
    db_session.flush()
    cost_fact_store.upsert_fact(org.id, "application", app_row.id, 120000, "GBP", "test")
    with tenant_ctx(org.id):
        lines = ms.cost_lines(canvas)
    assert [(row["name"], row["amount"], row["currency"]) for row in lines] == [("Billing platform", Decimal("120000"), "GBP")]


def test_two_organisations_never_share_entries(db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("bmc-iso-a"), make_org("bmc-iso-b")
    canvas_a, canvas_b = _canvas(db_session, org_a), _canvas(db_session, org_b)
    foreign = ArchiMateElement(name="Secret segment", type="BusinessActor", layer="business", organization_id=org_a.id)
    db_session.add(foreign)
    db_session.flush()
    with tenant_ctx(org_a.id):
        ms.add_entry(canvas_a, "customer_segments", name="Shared name")
    with tenant_ctx(org_b.id):
        assert [e.name for e in ms.candidates(canvas_b, "customer_segments")] == []
        with pytest.raises(ms.CanvasError):
            ms.add_entry(canvas_b, "customer_segments", element_id=foreign.id)
        mine = ms.add_entry(canvas_b, "customer_segments", name="Shared name")
        assert mine.organization_id == org_b.id
        assert [e.organization_id for e in ms.entries(canvas_b, "customer_segments")] == [org_b.id]
    with tenant_ctx(org_a.id):
        assert [e.organization_id for e in ms.entries(canvas_a, "customer_segments")] == [org_a.id]


def test_a_signed_in_user_of_another_organisation_gets_not_found(db_session, make_org, client, login_as):
    org_a, org_b = make_org("bmc-http-a"), make_org("bmc-http-b")
    canvas = _canvas(db_session, org_a)
    owner, outsider = _user(db_session, org_a), _user(db_session, org_b)
    login_as(client, outsider)
    assert client.get(f"/business-model/{canvas.id}").status_code == 404
    assert client.get(f"/business-model/{canvas.id}/value-proposition").status_code == 404
    resp = client.post(f"/business-model/{canvas.id}/entries/add", data={"box": "key_partners", "name": "Intruder"})
    assert resp.status_code == 404
    assert ArchiMateElement.query.filter_by(name="Intruder").count() == 0
    login_as(client, owner)
    assert client.get(f"/business-model/{canvas.id}").status_code == 200


def test_the_old_value_proposition_address_goes_permanently_to_the_canvas_for_the_segment(db_session, make_org, client, login_as, tenant_ctx):
    org = make_org("bmc-vpc")
    canvas = _canvas(db_session, org)
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        ms.add_entry(canvas, "customer_segments", name="Grocery chains")
        ms.add_entry(canvas, "value_propositions", name="Live tracking")
        vpc = ms.create_value_proposition_canvas(canvas)
        db_session.commit()
    login_as(client, user)
    resp = client.get(f"/business-model/{canvas.id}/value-proposition")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith(f"/business-model/{vpc.id}")
    login_as(client, user)
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Grocery chains" in page and "Live tracking" in page


def test_the_old_address_with_no_canvas_for_the_segment_returns_to_the_canvas_with_a_message(db_session, make_org, client, login_as):
    org = make_org("bmc-vpc-none")
    canvas = _canvas(db_session, org)
    login_as(client, _user(db_session, org))
    resp = client.get(f"/business-model/{canvas.id}/value-proposition")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/business-model/{canvas.id}")
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Create a Value Proposition Canvas from this canvas." in page


def test_creating_the_value_proposition_canvas_fills_it_from_the_model_and_a_second_press_opens_the_same_one(db_session, make_org, client, login_as, tenant_ctx):
    org = make_org("bmc-vpc-create")
    canvas = _canvas(db_session, org)
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        ms.add_entry(canvas, "customer_segments", name="Grocery chains")
        ms.add_entry(canvas, "value_propositions", name="Live tracking")
        db_session.commit()
    login_as(client, user)
    first = client.post(f"/business-model/{canvas.id}/value-proposition/create")
    assert first.status_code == 302
    login_as(client, user)
    second = client.post(f"/business-model/{canvas.id}/value-proposition/create")
    assert second.headers["Location"] == first.headers["Location"]
    vpc_id = int(first.headers["Location"].rsplit("/", 1)[1])
    assert BusinessModelCanvas.query.filter_by(organization_id=org.id, canvas_type="value_proposition_canvas").count() == 1
    with tenant_ctx(org.id):
        vpc = BusinessModelCanvas.query.get(vpc_id)
        assert [e.name for e in ms.entries(vpc, "customer_segments")] == ["Grocery chains"]
        assert [e.name for e in ms.entries(vpc, "value_propositions")] == ["Live tracking"]
        assert ArchiMateElement.query.filter_by(organization_id=org.id, name="Grocery chains").count() == 1
    login_as(client, user)
    page = client.get(f"/business-model/{canvas.id}").get_data(as_text=True)
    assert f'href="/business-model/{vpc_id}"' in page
    assert "Create Value Proposition Canvas" not in page


def test_another_organisation_cannot_open_or_create_a_value_proposition_canvas_from_the_canvas(db_session, make_org, client, login_as):
    org_a, org_b = make_org("bmc-vpc-a"), make_org("bmc-vpc-b")
    canvas = _canvas(db_session, org_a)
    outsider = _user(db_session, org_b)
    login_as(client, outsider)
    assert client.get(f"/business-model/{canvas.id}/value-proposition").status_code == 404
    assert client.post(f"/business-model/{canvas.id}/value-proposition/create").status_code == 404
    assert BusinessModelCanvas.query.filter_by(canvas_type="value_proposition_canvas", organization_id=org_a.id).count() == 0
    assert BusinessModelCanvas.query.filter_by(canvas_type="value_proposition_canvas", organization_id=org_b.id).count() == 0


def test_the_old_block_text_write_is_gone(db_session, make_org, client, login_as):
    org = make_org("bmc-gone")
    canvas = _canvas(db_session, org)
    login_as(client, _user(db_session, org))
    resp = client.post(f"/business-model/{canvas.id}/api/block", json={"block": "key_partners", "content": "text"})
    assert resp.status_code in (404, 405)
    assert BusinessModelCanvas.query.get(canvas.id).key_partners in (None, "")


def test_a_canvas_with_no_customer_segment_cannot_make_a_value_proposition_canvas(db_session, make_org, client, login_as, tenant_ctx):
    org = make_org("bmc-vpc-nosegment")
    canvas = _canvas(db_session, org)
    user = _user(db_session, org)
    login_as(client, user)
    page = client.get(f"/business-model/{canvas.id}").get_data(as_text=True)
    assert "Create Value Proposition Canvas" in page
    assert "Add a customer segment to this canvas first" in page
    button = page[page.index('data-testid="vpc-create"') - 400:page.index('data-testid="vpc-create"') + 100]
    assert re.search(r"\sdisabled[\s>]", button)
    for _ in range(2):
        login_as(client, user)
        resp = client.post(f"/business-model/{canvas.id}/value-proposition/create")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/business-model/{canvas.id}")
    assert BusinessModelCanvas.query.filter_by(organization_id=org.id, canvas_type="value_proposition_canvas").count() == 0
    login_as(client, user)
    assert "Add a customer segment to this canvas first" in client.get(f"/business-model/{canvas.id}").get_data(as_text=True)


def test_a_canvas_with_a_customer_segment_offers_the_button_enabled_in_the_header_and_two_presses_make_one_canvas(db_session, make_org, client, login_as, tenant_ctx):
    org = make_org("bmc-vpc-segment")
    canvas = _canvas(db_session, org)
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        ms.add_entry(canvas, "customer_segments", name="Dairy cooperatives")
        db_session.commit()
    login_as(client, user)
    page = client.get(f"/business-model/{canvas.id}").get_data(as_text=True)
    assert "Add a customer segment to this canvas first" not in page
    at = page.index('data-testid="vpc-create"')
    assert not re.search(r"\sdisabled[\s>]", page[at - 400:at + 100])
    assert page.index("Delete Canvas") > at - 1500, "the button sits with the other header actions"
    ids = set()
    for _ in range(2):
        login_as(client, user)
        ids.add(client.post(f"/business-model/{canvas.id}/value-proposition/create").headers["Location"])
    assert len(ids) == 1
    assert BusinessModelCanvas.query.filter_by(organization_id=org.id, canvas_type="value_proposition_canvas").count() == 1
