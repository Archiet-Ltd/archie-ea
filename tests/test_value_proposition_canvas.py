"""The Value Proposition Canvas shows a full customer profile for each segment of the demonstration company,
and the Business Model Canvas's cost box shows the cost store's own figure for each system, in the
organisation's currency. A second organisation never sees the first one's profile.
"""

from decimal import Decimal

from app.commands import seed_demo_company as demo
from app.models.application_portfolio import ApplicationComponent
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.cost_fact import CostFact
from app.modules.business_model_canvas import model_service as ms
from app.modules.business_model_canvas import vpc_service
from app.services import cost_fact_store
from app.services.application_cost_accessor import sync_cost_fact
from tests.test_business_model_canvas_model import _canvas, _user

PROFILE_BOXES = ("customer-jobs", "pains", "gains", "pain-relievers")


def _seeded_canvas(db_session, make_org, label):
    org = make_org(label)
    canvas = _canvas(db_session, org)
    from flask import g

    g.current_org_id = org.id
    demo._seed_canvas_model(org.id, canvas)
    db_session.flush()
    return org, canvas


def test_every_segment_has_jobs_pains_gains_and_relievers_that_meet_a_pain(db_session, make_org, tenant_ctx):
    org, canvas = _seeded_canvas(db_session, make_org, "vpc-full")
    with tenant_ctx(org.id):
        found = vpc_service.segments(canvas)
        assert len(found) == 3
        relievers = 0
        for segment in found:
            boxes, fits = vpc_service.profile(canvas, segment)
            for box in ("products-services", "gain-creators", *PROFILE_BOXES):
                assert boxes[box], f"{box} is empty for {segment.name}"
            for box in ("customer-jobs", "pains", "gains"):
                assert len(boxes[box]) >= 3, (segment.name, box)
            pains = {n["text"] for n in boxes["pains"]}
            relieved = [f for f in fits if f["kind"] == "Pain relievers meet pains"]
            assert relieved, f"no pain reliever meets a pain for {segment.name}"
            assert all(f["to"]["text"] in pains for f in relieved)
            relievers += len(boxes["pain-relievers"])
        assert relievers >= 2


def test_a_segments_profile_is_its_own(db_session, make_org, tenant_ctx):
    org, canvas = _seeded_canvas(db_session, make_org, "vpc-own")
    with tenant_ctx(org.id):
        first, second = vpc_service.segments(canvas)[:2]
        a, _ = vpc_service.profile(canvas, first)
        b, _ = vpc_service.profile(canvas, second)
        assert not {n["text"] for n in a["pains"]} & {n["text"] for n in b["pains"]}


def test_seeding_twice_adds_nothing(db_session, make_org, tenant_ctx):
    org, canvas = _seeded_canvas(db_session, make_org, "vpc-twice")
    before = (ArchiMateElement.query.filter_by(organization_id=org.id).count(),
              ArchiMateRelationship.query.filter_by(organization_id=org.id).count())
    demo._seed_canvas_model(org.id, canvas)
    db_session.flush()
    after = (ArchiMateElement.query.filter_by(organization_id=org.id).count(),
             ArchiMateRelationship.query.filter_by(organization_id=org.id).count())
    assert before == after


def test_a_reliever_added_on_the_page_is_tied_to_its_segment_and_pain(db_session, make_org, client, login_as, tenant_ctx):
    org, canvas = _seeded_canvas(db_session, make_org, "vpc-add")
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        segment = vpc_service.segments(canvas)[0]
        pain = vpc_service.profile(canvas, segment)[0]["pains"][0]["text"]
    pain_id = ArchiMateElement.query.filter_by(name=pain, organization_id=org.id).one().id
    login_as(client, user)
    resp = client.post(f"/business-model/{canvas.id}/entries/add", data={
        "box": "pain_relievers", "name": "Spare sensor kept on site", "segment": segment.id, "fits": pain_id})
    assert resp.status_code == 302 and f"/value-proposition?segment={segment.id}" in resp.headers["Location"]
    login_as(client, user)
    page = client.get(f"/business-model/{canvas.id}/value-proposition?segment={segment.id}").get_data(as_text=True)
    assert "Spare sensor kept on site" in page
    assert "relieves" in page and pain in page


def test_the_page_offers_the_next_step_in_an_empty_profile_box(db_session, make_org, client, login_as):
    org = make_org("vpc-empty")
    canvas = _canvas(db_session, org)
    user = _user(db_session, org)
    login_as(client, user)
    client.post(f"/business-model/{canvas.id}/entries/add", data={"box": "customer_segments", "name": "Grocery chains"})
    login_as(client, user)
    page = client.get(f"/business-model/{canvas.id}/value-proposition").get_data(as_text=True)
    for box in PROFILE_BOXES:
        assert f"Platform.modal.open(&#39;add-{box}&#39;)" in page or f"Platform.modal.open('add-{box}')" in page, box
    assert 'data-testid="vpc-add-form-pains"' in page


def test_costs_in_the_demo_differ_and_are_in_the_organisations_currency():
    totals = [demo._app_total_cost(*cost) for cost in demo._APP_COSTS.values()]
    assert len(set(totals)) == len(totals), "two systems share a cost"
    assert demo._ORG_CURRENCY == "GBP"
    assert {price[3] for price in demo._CANVAS_PRICES} == {demo._ORG_CURRENCY}


def test_the_cost_box_equals_the_cost_store_for_every_system(db_session, make_org, tenant_ctx):
    org = make_org("vpc-cost")
    canvas = _canvas(db_session, org)
    org.reporting_currency = "GBP"
    apps = []
    for name, (licence, infrastructure) in list(demo._APP_COSTS.items())[:5]:
        row = ApplicationComponent(
            name=name, organization_id=org.id, license_cost_annual=licence,
            infrastructure_cost_monthly=infrastructure,
            total_cost_of_ownership=demo._app_total_cost(licence, infrastructure),
        )
        db_session.add(row)
        apps.append(row)
    db_session.flush()
    for row in apps:
        for column in ("total_cost_of_ownership", "license_cost_annual", "infrastructure_cost_monthly"):
            sync_cost_fact(row, column, "GBP")
    db_session.flush()
    with tenant_ctx(org.id):
        lines = {line["id"]: line for line in ms.cost_lines(canvas)}
        for row in apps:
            stored = cost_fact_store.list_facts(org.id, element_type="application", element_ids=[row.id], category="total")
            assert len(stored) == 1
            assert lines[row.id]["amount"] == stored[0].amount == Decimal(row.total_cost_of_ownership)
            assert lines[row.id]["currency"] == stored[0].currency == "GBP"
    assert len({line["amount"] for line in lines.values()}) == len(apps)


def test_two_organisations_never_share_a_profile(db_session, make_org, client, login_as, tenant_ctx):
    org_a, canvas_a = _seeded_canvas(db_session, make_org, "vpc-iso-a")
    org_b = make_org("vpc-iso-b")
    canvas_b = _canvas(db_session, org_b)
    user_b = _user(db_session, org_b)
    with tenant_ctx(org_b.id):
        assert vpc_service.segments(canvas_b) == []
        boxes, fits = vpc_service.profile(canvas_b, None)
        assert fits == [] and all(not boxes[b] for b in PROFILE_BOXES)
    with tenant_ctx(org_a.id):
        segment_a = vpc_service.segments(canvas_a)[0]
    # B cannot open A's canvas, and cannot put an entry in A's segment on its own canvas.
    login_as(client, user_b)
    assert client.get(f"/business-model/{canvas_a.id}/value-proposition?segment={segment_a.id}").status_code == 404
    resp = client.post(f"/business-model/{canvas_a.id}/entries/add", data={
        "box": "pains", "name": "Intruder pain", "segment": segment_a.id})
    assert resp.status_code == 404
    login_as(client, user_b)
    client.post(f"/business-model/{canvas_b.id}/entries/add", data={"box": "pains", "name": "Intruder pain", "segment": segment_a.id})
    assert ArchiMateRelationship.query.filter_by(source_id=segment_a.id, organization_id=org_b.id).count() == 0
    assert CostFact.query.filter_by(organization_id=org_b.id).count() == 0
