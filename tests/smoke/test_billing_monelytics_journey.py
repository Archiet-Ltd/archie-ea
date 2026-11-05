"""A startup buys a plan and cancels it without talking to anyone (Monelytics).

An organisation administrator chooses Startup, pays on the hosted checkout, comes
back to the billing page on the plan, cancels in the page's own dialog, sees the
end date, and resumes. Monelytics is a loopback stub: no real host is called.
Run at 1440 light and 390 dark.
"""
import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_billing_journey import _SKIP_WELCOME, _community_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def monelytics_server(request, app):
    from .conftest import boot_live_server
    from .monelytics_stub import MonelyticsStub

    with MonelyticsStub() as stub:
        server = boot_live_server(request, None, app, extra_env=stub.child_environment({}))
        yield server, stub


def _shoot(page, name, width):
    folder = os.environ.get("BILLING01_SHOTS")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, "%s-%d.png" % (name, width)), full_page=True)


def _plan(page):
    return page.get_by_test_id("billing-current-plan").inner_text().strip()


@pytest.mark.parametrize("width,height,scheme", [(1440, 900, "light"), (390, 844, "dark")])
def test_a_startup_buys_cancels_and_resumes_without_help(browser, monelytics_server, app, width, height, scheme):
    from .test_archetype_journeys import _login

    base, stub = monelytics_server
    _org_id, admin_email = _community_org(app, members=0)
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.add_init_script(_SKIP_WELCOME)
    page = context.new_page()
    try:
        _login(page, base, admin_email)
        page.goto(base + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _plan(page) == "Community"
        _shoot(page, "no-plan", width)

        page.get_by_test_id("plan-card-startup").get_by_role("link", name="Choose Startup").click()
        page.get_by_test_id("checkout-submit").click()
        page.wait_for_url(lambda u: "/pay/" in u, timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Pay").click()
        page.wait_for_url(lambda u: "/admin/billing" in u, timeout=PAGE_TIMEOUT)
        assert _plan(page) == "Startup"
        expect(page.get_by_test_id("billing-renewal")).to_contain_text("15 Jan 2030")
        _shoot(page, "active", width)

        # Cancel in the page's own dialog; focus returns to the control that opened it.
        opener = page.get_by_role("button", name="Cancel subscription")
        opener.click()
        confirm = page.get_by_role("button", name="Cancel at the end of the period")
        expect(confirm).to_be_visible()
        page.keyboard.press("Escape")
        expect(confirm).to_be_hidden()
        assert page.evaluate("document.activeElement && document.activeElement.getAttribute('aria-label')") \
            == "Cancel subscription"
        assert not stub.subs[next(iter(stub.subs))]["cancel"], "dismissing the dialog must send nothing"
        opener.click()
        confirm.click()
        expect(page.locator("#platform-toast-container")).to_contain_text(
            "Your subscription is cancelled. The plan stays in place until 15 Jan 2030", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("billing-renewal")).to_contain_text("15 Jan 2030")
        assert _plan(page) == "Startup", "the paid period is still running"
        expect(page.get_by_text("Ends on")).to_be_visible()
        _shoot(page, "cancelled", width)

        page.get_by_role("button", name="Keep my subscription").click()
        expect(page.get_by_role("button", name="Cancel subscription")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Next renewal")).to_be_visible()
        assert _plan(page) == "Startup"
        _shoot(page, "resumed", width)

        # Monelytics unreachable: a plain sentence, no stack trace, the page still works.
        stub.down = True
        page.reload(wait_until="domcontentloaded")
        body = page.get_by_test_id("billing-page").inner_text()
        assert "Traceback" not in body and "503" not in body
        expect(page.get_by_test_id("billing-page")).to_contain_text("Billing & plan")
        _shoot(page, "unreachable", width)
        stub.down = False

        # Every call carried this organisation's tenant and nothing forbidden was called.
        paths = {p for _m, p, _t in stub.requests}
        assert not any("/internal/" in p or "consume" in p or "webhook" in p for p in paths)
        untenanted = [(m, p) for m, p, t in stub.requests if t is None]
        assert not untenanted, untenanted
        assert "entelim-org-%d" % _org_id in {t for _m, _p, t in stub.requests}
    finally:
        context.close()
