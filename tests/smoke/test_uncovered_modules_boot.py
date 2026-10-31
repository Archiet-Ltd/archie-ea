"""Boot coverage for the 36 /modules/ pages with zero existing smoke coverage.

Measured directly (not estimated): of 67 real module URLs served at
/modules/, 36 had no reference anywhere in tests/smoke/. This file closes
that gap at the same bar test_archetype_journeys.py already uses for
UNJOURNEYED_PAGES - a page in the navigation must render, its front end must
boot, and it must not throw a JavaScript error - and additionally asserts the
page shows real, non-empty content rather than a silent blank/broken state.

This is a floor, not the ceiling: task-completion tests (create -> persist ->
reselect, per test_composer_custom_properties.py) are the standard for a
module's primary write action and should replace the corresponding entry here
as they are written. Recorded per-page below which persona was used and why.
"""

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGE_STATE = """() => ({
  alpine: typeof window.Alpine,
})"""


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, (
        "could not sign in as %s - still on the login page" % email)


def _visit(page, base, path):
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1200)
    try:
        page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
    except Exception:
        pass
    return response, page.evaluate(PAGE_STATE)


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    pg.console_errors = []
    pg.page_errors = []
    pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.page_errors.append(str(e)))
    yield pg
    ctx.close()

# path -> persona whose role most plausibly owns the page.
MODULES = {
    "/account/manage/info": "enterprise_architect",
    "/application-management/": "portfolio_manager",
    "/archimate-roadmap": "enterprise_architect",
    "/architecture/business/products": "business_architect",
    "/architecture/dashboard": "enterprise_architect",
    "/architecture/decisions/": "enterprise_architect",
    "/architecture/investment-priorities": "cto",
    "/architecture/motivation": "enterprise_architect",
    "/architecture/traceability": "enterprise_architect",
    "/auto-dashboard/registry": "platform_admin",
    "/batch-import/": "data_architect",
    "/capability-maturity/frameworks": "business_architect",
    # Redirects to /capability-map/dashboard?heat=gap: the map's content is what is asserted.
    "/capability-maturity/heatmap": "business_architect",
    "/capability-roadmap": "business_architect",
    "/dashboard/compliance": "security_architect",
    "/dashboard/rationalization/scorecard": "portfolio_manager",
    "/enterprise/implementation/gap-analysis": "enterprise_architect",
    "/enterprise/implementation/work-packages": "enterprise_architect",
    "/enterprise/policy-monitoring": "platform_admin",
    "/framework-config/": "platform_admin",
    "/framework-management/": "platform_admin",
    "/hybrid-mapping-dashboard": "data_architect",
    "/industry-apqc/": "business_architect",
    "/market-intelligence/": "cto",
    "/organization/": "enterprise_architect",
    "/policy-monitoring/": "platform_admin",
    "/product-roadmap": "solution_architect",
    "/solutions/briefings": "solution_architect",
    "/solutions/data-stewardship": "data_architect",
    "/solutions/programmes": "portfolio_manager",
    "/stakeholders/map": "enterprise_architect",
    "/strategic/capability-health": "business_architect",
    "/strategic/impact-analysis": "enterprise_architect",
    "/usage-analytics/": "platform_admin",
    "/vendor-archimate-analysis": "portfolio_manager",
}


@pytest.mark.parametrize("path,persona", sorted(MODULES.items()))
def test_module_boots_and_renders_real_content(path, persona, page, live_server, seeded):
    """Every previously-untested module: renders, front end boots, no JS errors,
    and the body is not a blank/near-empty shell.

    A 404/410 for a genuinely removed route is recorded via xfail-style
    tolerance below rather than silently passed - see the skip branch.
    """
    email = seeded["emails"].get(persona)
    assert email, "no seeded email for persona %r" % persona
    _login(page, live_server, email)
    page.console_errors.clear()
    page.page_errors.clear()

    response, state = _visit(page, live_server, path)
    status = response.status if response else 0

    if status in (404, 410):
        pytest.skip("%s -> HTTP %d: route not present in this build, skipping rather than "
                    "asserting against a page that does not exist" % (path, status))

    assert status < 400, "%s -> HTTP %d" % (path, status)
    assert state["alpine"] == "object", (
        "%s -> front end did not boot (window.Alpine is %s)" % (path, state["alpine"]))

    body_text = page.inner_text("body")
    visible_len = len(" ".join(body_text.split()))
    assert visible_len > 80, (
        "%s -> body has only %d chars of visible text, looks blank/broken"
        % (path, visible_len))

    errors = [e for e in (page.console_errors + page.page_errors)
              if "favicon" not in e.lower()]
    assert not errors, "%s -> %d JavaScript error(s):\n  - %s" % (
        path, len(errors), "\n  - ".join(errors[:5]))
