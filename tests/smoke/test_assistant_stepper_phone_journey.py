"""Journey, in a browser, on a phone: a person on a late step of the architecture assistant can see which step
they are on.

The nine-step row is wider than a phone, so it scrolls sideways inside its own box. Before the fix it stayed at
its start when the design moved on to the execution steps, so the current step sat out of sight to the right.
The organisation is the demonstration company, seeded by the product's own seed.
"""

import os

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_USER = "demo@lantern-quay.example.com"

WIZARD = "document.querySelector('[x-data=\"architectureWizard()\"]')"

# Where the current step's button and the row's box sit, in viewport pixels.
CURRENT = """() => {
  const box = document.querySelector('[data-testid="wizard-steps"]').getBoundingClientRect();
  const here = document.querySelector('[data-testid="wizard-steps"] button.bg-primary');
  if (!here) return null;
  const r = here.getBoundingClientRect();
  return {left: r.left, right: r.right, boxLeft: box.left, boxRight: box.right, width: innerWidth,
          label: here.textContent.trim()};
}"""

# The design has been through the board: the execution steps are open, as they are once a submission is approved.
# No screen opens them today: the assistant page is never given the solution it would read the board's decision
# from, so even a seeded approved solution leaves steps 7 to 9 locked. The decision is therefore the one thing set
# in the page's state; the person's own move, tapping the step button, is made with a real click.
APPROVE = "() => { const d = window.Alpine.$data(%s); d.governanceStatus = 'arb_approved'; d.maxStep = 9; }" % WIZARD

# Scrolled back to its start, the row shows the first step inside its box.
FIRST_REACHABLE = """() => {
  const row = document.querySelector('[data-testid="wizard-steps"]');
  row.scrollLeft = 0;
  return row.querySelector('button').getBoundingClientRect().left >= row.getBoundingClientRect().left;
}"""


@pytest.fixture(scope="module")
def demo_org(live_server):
    from app import create_app
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with create_app("testing").app_context():
            seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return DEMO_USER


@pytest.mark.parametrize("step", [7, 9])
def test_the_current_step_is_in_view_on_a_phone_in_dark(browser, live_server, demo_org, step):
    context = browser.new_context(viewport={"width": 390, "height": 800}, color_scheme="dark",
                                  is_mobile=True, has_touch=True)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", demo_org)
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, wait_until="commit", timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/architecture-assistant/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        page.evaluate(APPROVE)
        # The person taps the step's own button; the row has to scroll to it on its own, so the browser is not asked to.
        button = page.locator('[data-testid="wizard-steps"] button', has_text=str(step)).first
        assert button.is_enabled(), "step %d is still locked once the design is approved" % step
        button.dispatch_event("click")
        page.wait_for_function(
            "(n) => document.querySelector('[data-testid=\"wizard-steps\"] button.bg-primary')"
            "?.textContent.trim() === String(n)", arg=step)
        page.wait_for_timeout(300)
        spot = page.evaluate(CURRENT)
        page.screenshot(path=os.path.join(os.environ.get("WALK_DIR", "/tmp"), "assistant-step-%d-390-dark.png" % step))
        assert spot, "no step is marked as the current one"
        assert spot["label"] == str(step)
        assert spot["left"] >= spot["boxLeft"] - 1 and spot["right"] <= spot["boxRight"] + 1, \
            "step %d sits outside the row's box: %s" % (step, spot)
        assert spot["left"] >= 0 and spot["right"] <= spot["width"], \
            "step %d is off the screen: %s" % (step, spot)
        assert page.evaluate(FIRST_REACHABLE), "the first step cannot be scrolled back to"
    finally:
        context.close()
