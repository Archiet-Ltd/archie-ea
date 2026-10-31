"""fix05f / QA-017: strategic dashboards load with no console or page errors, and
the initiative modal opens from its visible button and closes with Escape and Cancel.
"""
import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000

PAGES = [
    ("/strategic/capability-health", "Create Plan", "health-remediation-modal"),
    ("/strategic/impact-analysis", "Create Change Request", "change-request-modal"),
    ("/strategic/investment-matrix", "Create Proposal", "investment-proposal-modal"),
    ("/strategic/risk-assessment", "Mitigate Risk", "risk-mitigation-modal"),
]
VIEWPORTS = [
    ({"width": 1440, "height": 900}, "light"),
    ({"width": 390, "height": 844}, "dark"),
]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture(scope="module")
def linked_portfolio(seeded):
    """Capabilities with applications behind them, so the investment matrix has rows."""
    import uuid

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import ApplicationCapabilityCoverage, BusinessCapability

    org_id = seeded["ids"]["org"]
    app = create_app("testing")
    with app.app_context():
        storefront = ApplicationComponent(name="Storefront %s" % uuid.uuid4().hex[:4], organization_id=org_id)
        db.session.add(storefront)
        db.session.flush()
        for i in range(4):
            cap = BusinessCapability(
                name="Fix05f capability %d" % i, code="F05F-%d-%s" % (i, uuid.uuid4().hex[:4]),
                organization_id=org_id, level=1, current_maturity_level=2, target_maturity_level=4,
                strategic_importance=("high" if i % 2 else None),
            )
            db.session.add(cap)
            db.session.flush()
            if i < 2:
                db.session.add(ApplicationCapabilityCoverage(
                    application_component_id=storefront.id, capability_id=cap.id, support_level="full"))
        db.session.commit()
    return seeded


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
@pytest.mark.parametrize("path,button,modal_id", PAGES, ids=[p[0].split("/")[-1] for p in PAGES])
def test_strategic_page_no_errors_and_modal_behaviour(browser, live_server, linked_portfolio, path, button, modal_id, viewport, scheme):
    ctx = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))

    _login(page, live_server, linked_portfolio["emails"]["enterprise_architect"])
    page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(2500)

    assert not errors, "Errors on %s at %s %s: %s" % (path, viewport["width"], scheme, errors)

    dialog = page.locator("#initiative-" + modal_id)
    trigger = page.locator("button", has_text=button).first
    expect(trigger).to_be_visible()

    trigger.click()
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()

    trigger.click()
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()

    # The recommendation rating dialog (opened from a generated recommendation).
    page.evaluate(
        "() => { const el = document.querySelector('[x-data^=\"llmRecommendationsComponent\"]');"
        " Alpine.$data(el).ratingRecommendation = {id: 1}; }"
    )
    rating = page.get_by_role("heading", name="Rate Recommendation")
    expect(rating).to_be_visible()
    page.keyboard.press("Escape")
    expect(rating).to_be_hidden()
    page.evaluate(
        "() => { const el = document.querySelector('[x-data^=\"llmRecommendationsComponent\"]');"
        " Alpine.$data(el).ratingRecommendation = {id: 1}; }"
    )
    expect(rating).to_be_visible()
    page.get_by_role("button", name="Cancel rating").click()
    expect(rating).to_be_hidden()

    # The recommendation rating dialog (opened from a generated recommendation).
    page.evaluate(
        "() => { const el = document.querySelector('[x-data^=\"llmRecommendationsComponent\"]');"
        " Alpine.$data(el).ratingRecommendation = {id: 1}; }"
    )
    rating = page.get_by_role("heading", name="Rate Recommendation")
    expect(rating).to_be_visible()
    page.keyboard.press("Escape")
    expect(rating).to_be_hidden()
    page.evaluate(
        "() => { const el = document.querySelector('[x-data^=\"llmRecommendationsComponent\"]');"
        " Alpine.$data(el).ratingRecommendation = {id: 1}; }"
    )
    expect(rating).to_be_visible()
    page.get_by_role("button", name="Cancel rating").click()
    expect(rating).to_be_hidden()

    assert not errors, "Errors on %s at %s %s: %s" % (path, viewport["width"], scheme, errors)
    ctx.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_risk_assessment_and_vision_stay_fixed(browser, live_server, linked_portfolio):
    """Risk assessment shows no raw None; the vision deliverable loads with one breadcrumb."""
    page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()
    _login(page, live_server, linked_portfolio["emails"]["enterprise_architect"])

    page.goto(live_server + "/strategic/risk-assessment", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    assert "None" not in page.locator("main").inner_text()

    response = page.goto(live_server + "/ea-workflows/deliverables/vision", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    assert page.locator("nav[aria-label='breadcrumb'], nav[aria-label='Breadcrumb']").count() == 1
