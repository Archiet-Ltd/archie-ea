"""A financial benefit's amounts reach only a person the cost rule allows; the heatmap and initiatives speak plainly.

Written from two goals: an architect checks what an initiative is meant to deliver, and the chief technology
officer checks what it is worth. The architect reads every non-financial benefit as before and no amount of a
financial one; the chief technology officer reads every amount. A second organisation holds a financial
benefit of its own so a leak across organisations shows as a number. The heatmap offers an investment view
only to the person who has one and says what a zero means; an empty initiatives list does not point at an API.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org
from .test_portfolio_costs_withheld_journey import VIEWS, WITHHELD, _main_text, _open, _shot

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

FINANCIAL_AMOUNTS = ("412,000", "412000", "380,000", "150,000", "881,000")
SAVING = "Licence saving"
NPS = "Customer score"


@pytest.fixture(scope="module")
def benefits(seeded, live_server):
    from app import create_app, db
    from app.models.benefit import Benefit
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    app = create_app("testing")
    tag = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    with app.app_context():
        other = Organization(name="Other Org %s" % tag, slug="other-%s" % tag)
        db.session.add(other)
        db.session.flush()
        mine = EnterpriseInitiative(name="Licence cut %s" % tag, organization_id=org_id, status="active")
        theirs = EnterpriseInitiative(name="Foreign cut %s" % tag, organization_id=other.id, status="active")
        db.session.add_all([mine, theirs])
        db.session.flush()
        db.session.add_all([
            Benefit(name=SAVING, benefit_type="cost_saving", organization_id=org_id,
                    legacy_enterprise_initiative_id=mine.id, baseline_value=412000, target_value=380000,
                    actual_value=150000, measure="Annual licence spend", unit="GBP"),
            Benefit(name=NPS, benefit_type="customer", organization_id=org_id,
                    legacy_enterprise_initiative_id=mine.id, baseline_value=31, target_value=47,
                    actual_value=38, measure="Net promoter score", unit="points"),
            Benefit(name="Foreign saving", benefit_type="cost_saving", organization_id=other.id,
                    legacy_enterprise_initiative_id=theirs.id, baseline_value=881000, target_value=700000),
        ])
        db.session.commit()
        return {"initiative_id": mine.id, "name": mine.name}


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_an_architect_reads_non_financial_values_and_no_financial_amount(browser, live_server, seeded, benefits, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["enterprise_architect"], width, height, dark)
    try:
        page.goto(live_server + "/portfolio/%d" % benefits["initiative_id"], wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name=benefits["name"]).first).to_be_visible()
        text = _main_text(page)
        assert text.count(WITHHELD) == 1, "the sentence is said once, not %d times" % text.count(WITHHELD)
        for figure in FINANCIAL_AMOUNTS:
            assert figure not in text, "a financial benefit amount %s reached a withheld person" % figure
        row = page.locator("tr", has_text=SAVING)
        expect(row).to_be_visible()
        expect(row).not_to_contain_text("%")
        nps = page.locator("tr", has_text=NPS)
        for value in ("31", "47", "38"):
            expect(nps).to_contain_text(value)
        assert "Foreign saving" not in text
        _shot(page, "benefits-withheld-" + label)
    finally:
        ctx.close()


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_the_chief_technology_officer_reads_every_benefit_amount(browser, live_server, seeded, benefits, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["cto"], width, height, dark)
    try:
        page.goto(live_server + "/portfolio/%d" % benefits["initiative_id"], wait_until="domcontentloaded")
        row = page.locator("tr", has_text=SAVING)
        for value in ("412,000", "380,000", "150,000"):
            expect(row).to_contain_text(value)
        expect(page.locator("form", has_text=SAVING)).to_contain_text("baseline 412000")
        assert WITHHELD not in _main_text(page)
        assert "881,000" not in _main_text(page)
        _shot(page, "benefits-allowed-" + label)
    finally:
        ctx.close()


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_the_heatmap_and_initiatives_speak_plainly(browser, live_server, label, width, height, dark):
    withheld = create_fresh_org("enterprise_architect")
    allowed = create_fresh_org("cto")

    ctx, page = _open(browser, live_server, withheld["emails"]["enterprise_architect"], width, height, dark)
    try:
        page.goto(live_server + "/dashboard/capability-heatmap", wait_until="domcontentloaded")
        expect(page.get_by_text("Maturity levels by domain").first).to_be_visible()
        text = _main_text(page)
        assert "investment" not in text.lower().replace(WITHHELD.lower(), ""), "an investment view was offered to a withheld person"
        _shot(page, "heatmap-withheld-" + label)
    finally:
        ctx.close()

    ctx, page = _open(browser, live_server, allowed["emails"]["cto"], width, height, dark)
    try:
        page.goto(live_server + "/dashboard/capability-heatmap", wait_until="domcontentloaded")
        expect(page.get_by_text("with an investment view").first).to_be_visible()
        page.get_by_label("View").select_option("investment")
        zero = page.get_by_test_id("heatmap-zero-investment")
        expect(zero).to_be_visible()
        expect(zero).to_contain_text("no solution cost is recorded")
        _shot(page, "heatmap-allowed-" + label)

        page.goto(live_server + "/enterprise/strategic/initiatives", wait_until="domcontentloaded")
        expect(page.get_by_text("No strategic initiatives created yet")).to_be_visible()
        text = _main_text(page)
        assert "API" not in text, "the empty initiatives page tells a person to use the API"
        expect(page.get_by_text("investment matrix")).to_be_visible()
        _shot(page, "initiatives-empty-" + label)
    finally:
        ctx.close()
