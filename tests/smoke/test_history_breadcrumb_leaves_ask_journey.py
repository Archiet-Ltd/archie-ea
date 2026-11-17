"""The history pages' "Intelligence" crumb opens a page with content, not the retired Ask screen.

As an enterprise architect who has loaded the sample company: opens the model-as-of and the
changes pages, follows the "Intelligence" crumb, and lands on the stored-answers page (which has
its heading and a way to ask) rather than being bounced through the old Ask address.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_ask_in_the_chat_journey import _load_sample, _role_of
from .test_try_sample_company_journey import _empty_organisation_user, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]
WALK = os.environ.get("ASKOUT_WALK_DIR")


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("path", ["/intelligence/history/as-of", "/intelligence/history/changes"])
def test_the_intelligence_crumb_lands_on_the_answers_page(browser, live_server, seeded, width, path):
    email, _org = _empty_organisation_user(live_server)
    assert _role_of(live_server, email) == "enterprise_architect"  # the persona, not an administrator
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        _load_sample(page, live_server)
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        crumb = page.locator("nav[aria-label*='readcrumb'] a", has_text="Intelligence").first
        assert crumb.get_attribute("href").rstrip("/").endswith("/intelligence/answers")
        if WALK:
            page.screenshot(path=os.path.join(WALK, "crumb-%s-%d.png" % (path.rsplit("/", 1)[1], width)))
        crumb.click()
        page.wait_for_url(re.compile(r"/intelligence/answers$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading").first).to_be_visible()
        assert page.locator("main, #main-content").first.inner_text().strip()
        if WALK:
            page.screenshot(path=os.path.join(WALK, "answers-%s-%d.png" % (path.rsplit("/", 1)[1], width)))
    finally:
        context.close()
