"""An enterprise architect puts the risk "Billing vendor outage" in money with the Open FAIR
method. The risk shows a yearly loss with its range, the billing application's page shows the
same figure, and the chat's answer to "what does the billing vendor risk cost us" quotes it
with its source and date. Every link lands on a page with content, a risk nobody has costed
says so, and a second organisation never sees the figure."""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as
from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

FACTORS = {
    "frequency": ("1", "2", "4"),
    "vulnerability": ("0.2", "0.4", "0.6"),
    "direct_loss": ("10000", "50000", "200000"),
    "knock_on_loss": ("5000", "20000", "100000"),
}
MONEY = r"[£$€][\d,]+"


def _seed(org_id, suffix):
    """Billing application, the risk linked to it, and a second risk nobody has costed."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink

    app = create_app("testing")
    with app.app_context():
        billing = ApplicationComponent(name="Billing %s" % suffix, organization_id=org_id)
        db.session.add(billing)
        db.session.commit()
        risk = Risk(title="Billing vendor outage", likelihood=3, impact=4, organization_id=org_id)
        other = Risk(title="Unmapped supplier lapse %s" % suffix, likelihood=2, impact=2, organization_id=org_id)
        db.session.add_all([risk, other])
        db.session.commit()
        db.session.add(RiskEntityLink(organization_id=org_id, risk_id=risk.id, entity_type="application",
                                      entity_id=billing.id))
        db.session.commit()
        ids = {"billing": billing.id, "risk": risk.id, "other": other.id, "billing_name": billing.name}
        db.session.remove()
    return ids


def _figure(text):
    """The most likely figure and the range, as the screen words them."""
    likely = re.search(r"(%s)\s+most likely" % MONEY, text).group(1)
    low, high = re.search(r"(%s) to (%s)" % (MONEY, MONEY), text).groups()
    return likely, "%s to %s" % (low, high)


def test_risk_yearly_loss_journey(live_server, browser):
    suffix = uuid.uuid4().hex[:6]
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    ids = _seed(mine["org_id"], suffix)
    theirs_ids = _seed(theirs["org_id"], suffix)
    base = live_server
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, base, mine["emails"]["enterprise_architect"])

    # Before anything is entered the risk says so and offers the form.
    page.goto("%s/risks/%d" % (base, ids["risk"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("risk-loss-empty")).to_contain_text("No yearly loss has been worked out")
    expect(page.get_by_test_id("risk-loss")).to_contain_text("Open FAIR™")
    assert "certified" not in page.locator("body").inner_text().lower()

    # A range out of order is refused in plain words and nothing is saved.
    for key, (low, likely, high) in FACTORS.items():
        for part, value in (("min", low), ("likely", likely), ("max", high)):
            page.fill("#%s_%s" % (key, part), value)
    page.fill("#vulnerability_likely", "0.9")
    page.get_by_role("button", name="Work out the yearly loss").click()
    expect(page.get_by_test_id("risk-loss-empty")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator("body")).to_contain_text("most likely value must sit between")

    # The architect enters the four ranges and saves.
    for key, (low, likely, high) in FACTORS.items():
        for part, value in (("min", low), ("likely", likely), ("max", high)):
            page.fill("#%s_%s" % (key, part), value)
    page.get_by_role("button", name="Work out the yearly loss").click()
    result = page.get_by_test_id("risk-loss-result")
    expect(result).to_be_visible(timeout=PAGE_TIMEOUT)
    likely, spread = _figure(result.inner_text())
    source = page.get_by_test_id("risk-loss-source").inner_text()
    assert "Open FAIR™" in source and "Launch" in source, source

    # After a reload the figure and the entered ranges are still there.
    page.reload(wait_until="domcontentloaded")
    assert _figure(page.get_by_test_id("risk-loss-result").inner_text()) == (likely, spread)
    assert page.input_value("#direct_loss_likely") == "50000"

    # Saving the same ranges again gives the identical figure.
    page.get_by_role("button", name="Work it out again").click()
    expect(page.get_by_test_id("risk-loss-result")).to_be_visible(timeout=PAGE_TIMEOUT)
    assert _figure(page.get_by_test_id("risk-loss-result").inner_text()) == (likely, spread)

    # The billing application's page, reached by the risk's own link, shows the same figure.
    page.get_by_role("link", name=ids["billing_name"]).click()
    page.wait_for_url(re.compile(r"/applications/%d" % ids["billing"]), timeout=PAGE_TIMEOUT)
    line = page.get_by_test_id("risk-loss-line")
    expect(line).to_be_visible(timeout=PAGE_TIMEOUT)
    assert (line.get_by_test_id("risk-loss-likely").inner_text(),
            line.get_by_test_id("risk-loss-range").inner_text()) == (likely, spread)
    # Its risk link lands back on the risk page with the figure.
    page.get_by_role("link", name="Billing vendor outage").first.click()
    expect(page.get_by_test_id("risk-loss-likely")).to_have_text(likely, timeout=PAGE_TIMEOUT)

    # The chat's answer quotes the same figure with its source and date, and its link opens the risk.
    sign_in_as(page, base, mine, "enterprise_architect")
    answer = ask(page, base, "What does the billing vendor risk cost us?")
    text = answer.inner_text()
    assert likely in text and spread.split(" to ")[0] in text and spread.split(" to ")[1] in text, text
    source_line = answer.locator("[data-catalogue-source]")
    expect(source_line).to_contain_text("Open FAIR™")
    expect(source_line).to_contain_text("Launch")
    assert re.search(r"\d{1,2} \w+ \d{4}", source_line.inner_text()), source_line.inner_text()
    answer.get_by_test_id("catalogue-open").first.click()
    expect(page.get_by_test_id("risk-loss-likely")).to_have_text(likely, timeout=PAGE_TIMEOUT)

    # A risk nobody has costed says why and where to go.
    answer = ask(page, base, "What does the unmapped supplier lapse %s risk cost us?" % suffix)
    expect(answer).to_contain_text("No yearly loss has been worked out")
    answer.get_by_role("link", name="Open the risk").click()
    expect(page.get_by_test_id("risk-loss-empty")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator("h1").first).to_contain_text("Unmapped supplier lapse")

    # Narrow screen: the form and the result are still operable.
    page.set_viewport_size({"width": 390, "height": 900})
    page.goto("%s/risks/%d" % (base, ids["risk"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("risk-loss-likely")).to_have_text(likely)
    expect(page.get_by_role("button", name="Work it out again")).to_be_visible()
    context.close()

    # The other organisation sees none of it.
    other = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    other.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    stranger = other.new_page()
    sign_in(stranger, base, theirs["emails"]["enterprise_architect"])
    response = stranger.goto("%s/risks/%d" % (base, ids["risk"]), wait_until="domcontentloaded")
    assert response.status == 404
    stranger.goto("%s/risks/%d" % (base, theirs_ids["risk"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(stranger.get_by_test_id("risk-loss-empty")).to_be_visible()
    answer = ask(stranger, base, "What does the billing vendor risk cost us?")
    assert likely not in answer.inner_text()
    expect(answer).to_contain_text("No yearly loss has been worked out")
    other.close()
