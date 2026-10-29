"""The seven dialogs and panels on the four strategic pages (capability health,
investment matrix, risk assessment, impact analysis), rebuilt from the
shared modal shell and the library's card, table, button and native-select
parts. One journey per component: each opens it the way a person on that page
does, performs its action, and asserts the outcome a person needs -- not
just that an element is visible.
"""
import json

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD
from tests.smoke.test_strategic_modals_fix05f import linked_portfolio  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


# ---------------------------------------------------------------------------
# 1. priority_actions -- the prioritised action list, rebuilt on ui.card/ui.badge.
# ---------------------------------------------------------------------------


def test_priority_actions_panel_is_a_real_card_with_working_links(browser, live_server, linked_portfolio):
    """An architect on capability health reads the prioritised action panel (now
    a library card) and the severity badge; the empty state names why nothing
    needs doing when the organisation has no open gap, work package or risk."""
    page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()
    _login(page, live_server, linked_portfolio["emails"]["enterprise_architect"])
    page.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    panel = page.get_by_test_id("priority-actions")
    expect(panel).to_be_visible()
    # Built on the library's card part (basecoat/card.html emits data-slot="card").
    expect(panel.locator("[data-slot='card']").first).to_be_visible()
    expect(panel.get_by_test_id("priority-actions-rule")).to_be_visible()

    rows = panel.get_by_test_id("priority-action-row")
    if rows.count() == 0:
        empty = panel.get_by_test_id("priority-actions-empty")
        expect(empty).to_be_visible()
        expect(empty).to_contain_text("no open gaps, late or blocked work packages, or open risks")
    else:
        link = rows.first.get_by_test_id("priority-action-link")
        href = link.get_attribute("href")
        assert href, "a priority action row must link somewhere a person can act on it"
        link.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("body").inner_text().strip() != "", "the link must land on a page with content"
    page.context.close()


# ---------------------------------------------------------------------------
# 2. llm_recommendations -- the generate panel and rating dialog, now the
#    shared modal shell.
# ---------------------------------------------------------------------------


def test_llm_recommendations_generate_rate_and_persist(browser, live_server, linked_portfolio):
    """An architect generates recommendations, rates one, and the rating sticks
    (the card no longer offers "Rate this" for that recommendation)."""
    page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()

    def generate(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "success": True,
            "recommendations": [{
                "id": 9001, "title": "Consolidate the billing capability",
                "priority": "HIGH", "confidence_score": 0.82,
                "provider_used": "stub", "model_used": "stub-1",
                "description": "Two teams maintain overlapping billing logic.",
                "rationale": "Coverage data shows duplicate ownership.",
                "estimated_effort_weeks": 6, "expected_impact": "Lower support load",
                "dependencies": [], "user_rating": None,
            }],
        }))

    def rate(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"success": True}))

    page.route("**/strategic/api/recommendations/capability_health", generate)
    page.route("**/strategic/api/recommendations/9001/rate", rate)

    _login(page, live_server, linked_portfolio["emails"]["enterprise_architect"])
    page.goto(live_server + "/strategic/capability-health", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    page.get_by_test_id("llm-generate").click()
    card = page.get_by_test_id("llm-recommendation-card")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Consolidate the billing capability")
    expect(card).to_contain_text("82%")

    page.get_by_test_id("llm-rate-open").click()
    dialog = page.get_by_role("heading", name="Rate Recommendation")
    expect(dialog).to_be_visible()

    page.get_by_role("button", name="Rate 4 of 5").click()
    page.get_by_test_id("llm-rate-submit").click()
    expect(dialog).to_be_hidden()
    # The rating persisted onto the recommendation: no "Rate this" left for it.
    expect(card.get_by_test_id("llm-rate-open")).to_have_count(0)
    page.context.close()


# ---------------------------------------------------------------------------
# 3. risk_detail_modal -- risk score, mitigation form, save.
# ---------------------------------------------------------------------------


def test_risk_detail_modal_shows_real_risk_and_saves_mitigation(browser, live_server, linked_portfolio):
    """Opening a risk's detail shows its actual risk score and breakdown (not a
    placeholder), and saving the mitigation plan round-trips through the API."""
    page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()

    risk_payload = {
        "capability_id": 42, "capability_name": "Payments orchestration",
        "capability_domain": "Finance", "strategic_importance": "high",
        "mitigation": None,
        "risk_metrics": {
            "overall_risk_score": 71, "risk_level": "HIGH",
            "spof_risk": 20, "technology_risk": 15, "compliance_risk": 10,
            "dependency_risk": 14, "skill_risk": 12,
            "risk_factors": ["single_vendor"],
        },
    }
    saved = {"count": 0}

    def details(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(risk_payload))

    def mitigation(route):
        saved["count"] += 1
        body = dict(risk_payload)
        body["mitigation"] = {"risk_owner": "Dana Lee", "status": "planning"}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/strategic/api/risks/42/details", details)
    page.route("**/strategic/api/risks/42/mitigation", mitigation)

    _login(page, live_server, linked_portfolio["emails"]["enterprise_architect"])
    page.goto(live_server + "/strategic/risk-assessment", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.evaluate("() => window.dispatchEvent(new CustomEvent('open-risk-detail', { detail: 42 }))")

    content = page.get_by_test_id("risk-detail-content")
    expect(content).to_be_visible()
    expect(content).to_contain_text("71/100")
    expect(content).to_contain_text("HIGH")

    page.fill("#risk-detail-owner", "Dana Lee")
    page.get_by_test_id("risk-detail-status").select_option("planning")
    page.get_by_test_id("risk-detail-save").click()
    page.wait_for_timeout(500)
    page.context.close()
    assert saved["count"] == 1, "the mitigation save must reach the API exactly once"


# ---------------------------------------------------------------------------
# 4. arb_submission_modal -- pre-populated business case, real submission.
# ---------------------------------------------------------------------------


def test_arb_submission_modal_prefills_and_submits(browser, live_server, linked_portfolio):
    """The business-case summary shows the real capability figures passed in
    (not a dash), and submitting reaches the ARB API and reports the review number."""
    page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()

    def submit(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "success": True, "review_number": "ARB-2026-0555", "review_url": "/strategic/architecture-governance",
        }))

    page.route("**/strategic/api/arb/submit-capability/77", submit)

    _login(page, live_server, linked_portfolio["emails"]["enterprise_architect"])
    page.goto(live_server + "/strategic/investment-matrix", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.evaluate(
        "() => window.dispatchEvent(new CustomEvent('open-arb-submission-modal', { detail: "
        "{ id: 77, name: 'Payments orchestration', priority_level: 'High', strategic_score: 21, "
        "coverage_count: 6, estimated_investment: 2 } }))"
    )

    content = page.get_by_test_id("arb-submission-content")
    expect(content).to_be_visible()
    expect(content).to_contain_text("High")
    expect(content).to_contain_text("21/25")
    expect(content).to_contain_text("6 applications")

    page.fill("#arb-justification", "Escalating ahead of the renewal deadline.")
    page.get_by_test_id("arb-submit").click()
    page.wait_for_timeout(500)
    toast = page.locator("text=ARB-2026-0555")
    expect(toast).to_be_visible()
    page.context.close()


# ---------------------------------------------------------------------------
# 5. roi_calculator_modal -- computed metrics agree with the inputs.
# ---------------------------------------------------------------------------


def test_roi_calculator_recomputes_from_real_inputs(browser, live_server, linked_portfolio):
    """Changing the investment figure changes the computed ROI/NPV on screen,
    so the figures shown always agree with what was actually typed in."""
    page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()
    _login(page, live_server, linked_portfolio["emails"]["enterprise_architect"])
    page.goto(live_server + "/strategic/investment-matrix", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.evaluate(
        "() => window.dispatchEvent(new CustomEvent('open-roi-calculator', { detail: "
        "{ id: 88, name: 'Order routing', estimated_cost: 100000, timeframe: 12 } }))"
    )

    content = page.get_by_test_id("roi-calculator-content")
    expect(content).to_be_visible()

    before = content.locator(".text-2xl.font-bold").first.inner_text()
    page.fill("#roi-investment", "500000")
    page.dispatch_event("#roi-investment", "input")
    page.wait_for_timeout(300)
    after = content.locator(".text-2xl.font-bold").first.inner_text()
    assert before != after, "raising the investment must change the computed ROI shown"
    assert after != "N/A"
    page.context.close()


# ---------------------------------------------------------------------------
# 6. scenario_comparison_modal -- two scenarios compared, figures agree.
# ---------------------------------------------------------------------------


def test_scenario_comparison_table_agrees_with_selected_capabilities(browser, live_server, linked_portfolio):
    """Selecting two capabilities fills a comparison column each, with the
    same capability name shown in the picker and in the table.

    The picker's data (window.__SCENARIO_CAPS) comes from the page's own
    server-rendered capability_scores -- real rows from the seeded
    organisation, not injected test data -- so this reads whichever real
    capabilities the page actually offers rather than assuming fixed names."""
    page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()
    _login(page, live_server, linked_portfolio["emails"]["enterprise_architect"])
    page.goto(live_server + "/strategic/investment-matrix", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.evaluate("() => window.dispatchEvent(new CustomEvent('open-scenario-comparison'))")

    content = page.get_by_test_id("scenario-comparison-content")
    expect(content).to_be_visible()

    options = page.evaluate(
        "() => (window.__SCENARIO_CAPS || []).map(c => ({ id: c.id, name: c.name }))"
    )
    assert len(options) >= 1, "the investment matrix's own seeded capabilities must reach the picker"

    selects = page.get_by_test_id("scenario-capability-select")
    first = options[0]
    selects.nth(0).select_option(str(first["id"]))
    page.wait_for_timeout(300)

    table = content.locator("table")
    expect(table).to_contain_text(first["name"])
    expect(table).to_contain_text("Scenario 1")

    if len(options) >= 2:
        second = options[1]
        selects.nth(1).select_option(str(second["id"]))
        page.wait_for_timeout(300)
        expect(table).to_contain_text(second["name"])
        expect(table).to_contain_text("Recommended")
    page.context.close()


# ---------------------------------------------------------------------------
# 7. impact_scenario_comparison_modal -- moved onto the shared modal shell;
#    the 4th-scenario warning used to call Platform.toast the wrong way.
# ---------------------------------------------------------------------------


def test_impact_scenario_comparison_warns_without_a_console_error(browser, live_server, linked_portfolio):
    """Selecting a 4th scenario must warn the user, not throw: before this
    fix, toggleScenario() called Platform.toast(message, 'warning') -- but
    Platform.toast is an object of methods, not a callable, so the 4th click
    raised a console error and the warning the code intended never appeared."""
    page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    scenarios = [
        {
            "id": i, "name": "Scenario %d" % i, "change_type": "Retire",
            "element": {"name": "Service %d" % i},
            "analysis_result": {
                "severity": sev, "impacted_capabilities": [1, 2][: i % 3],
                "impacted_processes": [1], "estimated_risk_cost": 50000 * i,
                "dependencies": [1, 2, 3][: i],
            },
        }
        for i, sev in enumerate(["LOW", "MEDIUM", "HIGH", "CRITICAL"], start=1)
    ]

    _login(page, live_server, linked_portfolio["emails"]["enterprise_architect"])
    page.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.evaluate(
        "(scenarios) => { localStorage.setItem('impactAnalysisScenarios', JSON.stringify(scenarios));"
        " window.dispatchEvent(new CustomEvent('open-impact-scenario-comparison')); }",
        scenarios,
    )

    content = page.get_by_test_id("impact-scenario-comparison-content")
    expect(content).to_be_visible()

    cards = page.get_by_test_id("impact-scenario-card")
    expect(cards).to_have_count(4)
    for i in range(4):
        cards.nth(i).click()
    page.wait_for_timeout(300)

    assert not errors, "selecting a 4th scenario must not throw: %s" % errors

    table = content.locator("table")
    expect(table).to_contain_text("Scenario 1")
    expect(table).to_contain_text("Scenario 2")
    expect(table).to_contain_text("Scenario 3")

    page.get_by_test_id("impact-scenario-export").click()  # must not throw either
    page.wait_for_timeout(200)
    assert not errors

    close_buttons = page.get_by_role("button", name="Close")
    close_buttons.last.click()
    expect(content).to_be_hidden()
    page.context.close()
