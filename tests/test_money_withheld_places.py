"""Business cases, the capability map, the timeline, the application feeds and element exports hold back money.

A person the one cost rule withholds from (an architect without spend authority) receives no amount from these
places and is told once why; a person it allows (the chief technology officer) receives every figure as before.
Two organisations are seeded, so a figure from the other one would show as well.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

SENTENCE = "Cost figures are withheld for your role."
TCO, CAPEX, BENEFIT, ESTIMATE, APP_COST, INVEST = 731519, 268431, 905163, 374821, 482917, 619247
OTHER_TCO, OTHER_ESTIMATE = 558833, 884422


def _user(db_session, org, enterprise_role, label):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    user = User(email="places-%s-%s@example.com" % (label, uuid.uuid4().hex[:10]), first_name="Places", last_name=label,
                confirmed=True, organization_id=org.id, enterprise_role=enterprise_role,
                password_hash=generate_password_hash("x"))
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_case import BusinessCase
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services.application_cost_accessor import set_annual_cost

    org, other = make_org("places-a"), make_org("places-b")
    application = ApplicationComponent(name="Places app %s" % uuid.uuid4().hex[:6], organization_id=org.id)
    db_session.add(application)
    db_session.flush()
    set_annual_cost(application, APP_COST)
    case = BusinessCase(title="Places case", organization_id=org.id, tco_3yr=TCO, capex=CAPEX,
                        financial_benefit_annual=BENEFIT, roi_percentage=41, payback_months=19)
    foreign_case = BusinessCase(title="Foreign case", organization_id=other.id, tco_3yr=OTHER_TCO)
    capability = UnifiedCapability(name="Places capability", organization_id=org.id, level=1,
                                   annual_cost=INVEST, investment_required=INVEST + 1)
    package = UnifiedWorkPackage(name="Places package", organization_id=org.id, estimated_cost=ESTIMATE,
                                 start_date=datetime(2026, 3, 1), end_date=datetime(2026, 9, 1), status="planned")
    foreign_package = UnifiedWorkPackage(name="Foreign package", organization_id=other.id, estimated_cost=OTHER_ESTIMATE,
                                         start_date=datetime(2026, 3, 1), end_date=datetime(2026, 9, 1), status="planned")
    db_session.add_all([case, foreign_case, capability, package, foreign_package])
    db_session.flush()
    return {"case": case, "architect": _user(db_session, org, "solution_architect", "architect"),
            "cto": _user(db_session, org, "cto", "cto"), "application": application}


def _get(client, login_as, user, path):
    login_as(client, user)
    return client.get(path)


def _numbers(node, keys, found=None):
    found = [] if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and isinstance(value, (int, float)) and not isinstance(value, bool):
                found.append((key, value))
            else:
                _numbers(value, keys, found)
    elif isinstance(node, list):
        for item in node:
            _numbers(item, keys, found)
    return found


# ---- business case detail

def test_the_business_case_page_shows_the_architect_no_amount_and_no_money_field(client, login_as, world):
    text = _get(client, login_as, world["architect"], "/business-case/%d" % world["case"].id).get_data(as_text=True)
    assert text.count(SENTENCE) == 1
    for figure in (TCO, CAPEX, BENEFIT, "731,519", "268,431"):
        assert str(figure) not in text
    for field in ("bc-input-capex", "bc-input-tco_3yr", "bc-input-financial_benefit_annual", "bc-input-roi_percentage",
                  "bc-input-payback_months", "bc-display-npv"):
        assert field not in text


def test_the_business_case_page_shows_the_chief_technology_officer_every_figure(client, login_as, world):
    text = _get(client, login_as, world["cto"], "/business-case/%d" % world["case"].id).get_data(as_text=True)
    assert SENTENCE not in text
    for figure in (TCO, CAPEX, BENEFIT):
        assert str(figure) in text
    assert 'data-testid="bc-input-tco_3yr"' in text


def test_the_business_case_feed_holds_no_amount_for_the_architect_and_every_one_for_the_cto(client, login_as, world):
    path = "/business-case/api/%d" % world["case"].id
    money = {"tco_3yr", "capex", "opex_annual", "financial_benefit_annual", "roi_percentage", "payback_months", "npv"}
    held = _get(client, login_as, world["architect"], path).get_json()["data"]
    assert _numbers(held, money) == []
    assert held["title"] == "Places case"
    shown = dict(_numbers(_get(client, login_as, world["cto"], path).get_json()["data"], money))
    assert shown["tco_3yr"] == TCO and shown["capex"] == CAPEX


def test_the_architect_cannot_save_a_money_field_and_the_cto_can(client, login_as, world, db_session):
    path = "/business-case/%d/api/field" % world["case"].id
    login_as(client, world["architect"])
    refused = client.post(path, json={"field": "tco_3yr", "value": "1"})
    assert refused.status_code == 403
    assert SENTENCE in refused.get_data(as_text=True)
    db_session.refresh(world["case"])
    assert float(world["case"].tco_3yr) == TCO
    login_as(client, world["architect"])
    assert client.post(path, json={"field": "problem_statement", "value": "Words only"}).status_code == 200
    login_as(client, world["cto"])
    assert client.post(path, json={"field": "tco_3yr", "value": "812345"}).status_code == 200
    db_session.refresh(world["case"])
    assert float(world["case"].tco_3yr) == 812345


def test_the_architect_cannot_pull_financials_into_a_business_case(client, login_as, world):
    login_as(client, world["architect"])
    reply = client.post("/business-case/%d/pull-financials" % world["case"].id)
    assert reply.status_code == 403 and SENTENCE in reply.get_data(as_text=True)


# ---- the capability map

def test_the_capability_map_reply_holds_no_investment_for_the_architect(client, login_as, world):
    path = "/capability-map/api/capabilities/hierarchy"
    held = _get(client, login_as, world["architect"], path).get_json()
    assert held["costs_withheld"] is True and held["costs_withheld_sentence"] == SENTENCE
    assert _numbers(held, {"investmentNow", "investmentPlanned"}) == []
    shown = _numbers(_get(client, login_as, world["cto"], path).get_json(), {"investmentNow", "investmentPlanned"})
    assert (("investmentNow", INVEST) in shown) and ("investmentPlanned", INVEST + 1) in shown


def test_the_capability_map_page_offers_the_architect_no_investment_lens_and_says_why_once(client, login_as, world):
    text = _get(client, login_as, world["architect"], "/capability-map/").get_data(as_text=True)
    assert text.count(SENTENCE) == 1
    assert "lens = 'investment'" not in text
    allowed = _get(client, login_as, world["cto"], "/capability-map/").get_data(as_text=True)
    assert "lens = 'investment'" in allowed and SENTENCE not in allowed


# ---- the work-package timeline

def test_the_timeline_feed_holds_no_estimate_for_the_architect(client, login_as, world):
    path = "/enterprise/api/work-packages/gantt"
    held = _get(client, login_as, world["architect"], path).get_json()
    assert held["costs_withheld"] is True and held["costs_withheld_sentence"] == SENTENCE
    assert [item["name"] for item in held["work_packages"]] == ["Places package"]
    assert _numbers(held, {"estimated_cost"}) == []
    shown = _get(client, login_as, world["cto"], path).get_json()
    assert [item["estimated_cost"] for item in shown["work_packages"]] == [ESTIMATE]
    assert "costs_withheld" not in shown


# ---- the applications list and the Add Applications modal feed

def test_the_applications_list_says_once_that_the_architect_reads_no_run_cost(client, login_as, world):
    text = _get(client, login_as, world["architect"], "/applications/").get_data(as_text=True)
    assert text.count(SENTENCE) == 1
    assert "run-cost-line" not in text
    allowed = _get(client, login_as, world["cto"], "/applications/").get_data(as_text=True)
    assert SENTENCE not in allowed


def test_the_application_feed_holds_no_cost_for_the_architect(client, login_as, world):
    path = "/capability-map/api/applications"
    held = _get(client, login_as, world["architect"], path).get_json()
    assert held["costs_withheld"] is True and held["costs_withheld_sentence"] == SENTENCE
    assert _numbers(held, {"total_cost_of_ownership"}) == []
    shown = _get(client, login_as, world["cto"], path).get_json()
    assert (("total_cost_of_ownership", APP_COST) in _numbers(shown, {"total_cost_of_ownership"}))


def test_the_modal_hides_the_cost_filter_when_the_feed_withholds_cost(client, login_as, world):
    text = _get(client, login_as, world["cto"], "/consolidation-list/").get_data(as_text=True)
    assert "app-filter-cost-field" in text and "data.costs_withheld" in text


# ---- element export

def test_the_element_export_holds_no_amount_for_the_architect(client, login_as, world):
    path = "/architecture/application/ApplicationComponent/export?format=json"
    held = _get(client, login_as, world["architect"], path)
    assert held.status_code == 200, held.get_data(as_text=True)[:200]
    rows = [row for row in held.get_json() if row.get("id") == world["application"].id]
    assert rows and not re.search(r"\b%d\b" % APP_COST, held.get_data(as_text=True))
    shown = _get(client, login_as, world["cto"], path).get_data(as_text=True)
    assert re.search(r"\b%d(\.0+)?\b" % APP_COST, shown)
