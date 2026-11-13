"""An initiative says plainly whether its forecast overruns the approved budget,
and its benefits header explains an empty total instead of printing a dash.

A person who may see costs reads on each portfolio initiative, in the list and on
the detail page: whether the forecast overruns the approved budget, by how much
and by what percentage, coloured by that answer; how much of the budget is spent
so far, labelled as spend so far; and a benefits header that gives the total, or
says in plain words why there is none. A person the cost rule withholds from sees
none of it.
"""

import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWS = (("1440-light", 1440, 900, False), ("390-dark", 390, 844, True))


def _open(browser, live_server, email, width, height, dark):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    if dark:
        ctx.add_init_script("document.documentElement.classList.add('dark');")
    page = ctx.new_page()
    sign_in(page, live_server, email)
    return ctx, page


def _main_text(page):
    return page.locator("main").inner_text()


def _shot(page, name):
    walk = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if walk:
        Path(walk).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(walk, name + ".png"), full_page=True)


@pytest.fixture(scope="module")
def initiatives(seeded, live_server):
    """Three initiatives in the seeded organisation: one over budget, one under,
    one with no forecast, plus one in a second organisation for isolation."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    app = create_app("testing")
    tag = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    with app.app_context():
        other = Organization(name="Other Org %s" % tag, slug="other-%s" % tag)
        db.session.add(other)
        db.session.flush()
        over = EnterpriseInitiative(name="Over run %s" % tag, organization_id=org_id,
                                    approved_budget=750000, spent_to_date=485000,
                                    forecast_cost=790000, status="active")
        under = EnterpriseInitiative(name="Under run %s" % tag, organization_id=org_id,
                                     approved_budget=750000, spent_to_date=300000,
                                     forecast_cost=700000, status="active")
        none_forecast = EnterpriseInitiative(name="No forecast %s" % tag, organization_id=org_id,
                                             approved_budget=500000, spent_to_date=200000,
                                             forecast_cost=None, status="active")
        foreign = EnterpriseInitiative(name="Foreign %s" % tag, organization_id=other.id,
                                       approved_budget=999999, spent_to_date=555555,
                                       forecast_cost=888888, status="active")
        db.session.add_all([over, under, none_forecast, foreign])
        db.session.commit()
        return {
            "over_id": over.id, "over_name": over.name,
            "under_id": under.id, "under_name": under.name,
            "none_id": none_forecast.id, "none_name": none_forecast.name,
        }


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_detail_says_whether_forecast_overruns_budget(browser, live_server, seeded, initiatives, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["cto"], width, height, dark)
    try:
        # Over budget: forecast 790,000 vs budget 750,000 -> 40,000 (5.3%) over
        page.goto(live_server + "/portfolio/%d" % initiatives["over_id"], wait_until="domcontentloaded")
        text = _main_text(page)
        assert "over the approved budget" in text, "overrun statement missing: %s" % text[:1500]
        assert "790,000" in text or "790000" in text, "forecast figure missing: %s" % text[:1500]
        assert "5.3%" in text, "variance percentage missing: %s" % text[:1500]

        # Under budget: forecast 700,000 vs budget 750,000 -> 50,000 (6.7%) under
        page.goto(live_server + "/portfolio/%d" % initiatives["under_id"], wait_until="domcontentloaded")
        text = _main_text(page)
        assert "under the approved budget" in text, "under statement missing: %s" % text[:1500]
        assert "700,000" in text or "700000" in text, "forecast figure missing: %s" % text[:1500]

        # No forecast: no budget position figure
        page.goto(live_server + "/portfolio/%d" % initiatives["none_id"], wait_until="domcontentloaded")
        text = _main_text(page)
        assert "No budget or forecast recorded" in text, "no-forecast message missing: %s" % text[:1500]

        _shot(page, "overrun-detail-" + label)
    finally:
        ctx.close()


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_spent_so_far_label_on_detail(browser, live_server, seeded, initiatives, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["cto"], width, height, dark)
    try:
        page.goto(live_server + "/portfolio/%d" % initiatives["over_id"], wait_until="domcontentloaded")
        text = _main_text(page)
        assert "Spent so far" in text, "label not 'Spent so far': %s" % text[:1500]
        assert "Spent to date" not in text, "old label still present: %s" % text[:1500]
        _shot(page, "spent-so-far-" + label)
    finally:
        ctx.close()


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_benefits_header_explains_empty_total(browser, live_server, seeded, initiatives, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["cto"], width, height, dark)
    try:
        # The "Over run" initiative has no benefits -> "No financial benefit recorded yet"
        page.goto(live_server + "/portfolio/%d" % initiatives["over_id"], wait_until="domcontentloaded")
        text = _main_text(page)
        assert "No financial benefit recorded yet" in text, "empty benefit header not explained: %s" % text[:1500]
        _shot(page, "benefits-empty-" + label)
    finally:
        ctx.close()


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_withheld_person_sees_no_amount(browser, live_server, seeded, initiatives, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["enterprise_architect"], width, height, dark)
    try:
        page.goto(live_server + "/portfolio/%d" % initiatives["over_id"], wait_until="domcontentloaded")
        text = _main_text(page)
        assert "Cost figures are withheld for your role." in text, "withheld sentence missing: %s" % text[:1500]
        for figure in ("750,000", "750000", "485,000", "485000", "790,000", "790000"):
            assert figure not in text, "figure %s reached a withheld person" % figure
        assert "Foreign" not in text
        _shot(page, "overrun-withheld-" + label)
    finally:
        ctx.close()
