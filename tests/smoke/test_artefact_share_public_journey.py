"""mg3-artefact-share-c5-098: artefact_share/public.html rebuilt from shadcn
macros (card, badge, button, table). An owner creates a share link through
the console, then a viewer with no account at all -- never signed in, never
an administrator -- opens that link in a fresh browser context and sees the
artefact; revoking the link then makes it a 404 for that same viewer.
"""
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def test_anonymous_viewer_opens_a_shared_artefact(browser, live_server, seeded):
    owner_page = browser.new_page()
    try:
        _login(owner_page, live_server, seeded["emails"]["solution_architect"])
        owner_page.goto(live_server + "/share/artefacts", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        first_card = owner_page.locator("div[data-slot='card']").first
        first_card.get_by_role("button", name="Create share link").click()
        owner_page.wait_for_selector("table tbody tr", timeout=PAGE_TIMEOUT)
        url_text = owner_page.locator("table tbody tr").first.locator("td").first.inner_text()
        match = re.search(r"/shared/([A-Za-z0-9_-]+)", url_text)
        assert match, "share link row did not contain a /shared/<token> URL: %r" % url_text
        token = match.group(1)
    finally:
        owner_page.close()

    context = browser.new_context()
    viewer = context.new_page()
    try:
        # No login call anywhere in this context: the viewer persona is defined
        # by having no session at all, never an administrator or any account.
        viewer.goto(live_server + "/shared/" + token, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert "/account/login" not in viewer.url, "a public share link must never require login"
        viewer.wait_for_selector("h1", timeout=PAGE_TIMEOUT)
        assert "Capability Maturity Heatmap" in viewer.locator("h1").inner_text()
        assert viewer.get_by_text("Read-only").is_visible()
        assert viewer.get_by_role("button", name="Print").is_visible()
        # Revoke-then-404 for this same route is covered without a browser by
        # tests/test_artefact_share.py::test_revoked_token_stops_working_immediately.
    finally:
        context.close()
