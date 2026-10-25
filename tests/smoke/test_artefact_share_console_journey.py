"""mg3-artefact-share-b-097: artefact_share/console.html rebuilt from shadcn
macros (table, badge, button, dropdown-menu, card, empty-state,
confirmation-dialog). A solution architect creates a share link, sees it
appear with an Active badge, then revokes it through the row actions menu
and a destructive-confirm dialog -- not an administrator.
"""
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


def test_solution_architect_creates_and_revokes_a_share_link(browser, live_server, seeded, db_session):
    from app.models.user import User

    email = seeded["emails"]["solution_architect"]
    user = User.query.filter_by(email=email).first()
    assert user.enterprise_role == "solution_architect", (
        "journey must run as the solution_architect persona, not %r" % user.enterprise_role
    )

    page = browser.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/share/artefacts", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("h3[data-slot='card-title']", timeout=PAGE_TIMEOUT)

        first_card = page.locator("div[data-slot='card']").first
        assert first_card.locator("[data-slot='card-title']").is_visible()

        # Red before: no table yet, the empty state is showing for the first artefact.
        assert first_card.get_by_text("No share links yet").is_visible()

        first_card.get_by_role("button", name="Create share link").click()
        page.wait_for_selector("table tbody tr", timeout=PAGE_TIMEOUT)

        row = first_card.locator("table tbody tr").first
        assert row.get_by_text("Active").is_visible()
        link_text = row.locator("td").first.inner_text()
        assert link_text.strip(), "share link row has no URL text"

        # Green after: revoke through the row's actions menu and the destructive-confirm dialog.
        row.get_by_role("button").last.click()
        page.get_by_role("menuitem", name="Revoke").click()
        page.get_by_role("button", name="Revoke", exact=True).last.click()
        page.wait_for_selector("table tbody tr >> text=Revoked", timeout=PAGE_TIMEOUT)
        assert row.get_by_text("Revoked").is_visible()
    finally:
        page.close()
