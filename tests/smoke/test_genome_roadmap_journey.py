"""mg3-genome-c3-096: app/modules/genome/templates/genome/roadmap.html
rebuilt from shadcn macros (page-header, button, empty-state, input) in
place of the in-house page_shell macro. An enterprise_architect persona --
never an administrator -- opens the roadmap and generates it.
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


def test_enterprise_architect_opens_and_generates_roadmap(browser, live_server, seeded, db_session):
    from app.models.user import User

    email = seeded["emails"]["enterprise_architect"]
    user = User.query.filter_by(email=email).first()
    assert user.enterprise_role == "enterprise_architect", (
        "journey must run as the enterprise_architect persona, not %r" % user.enterprise_role
    )

    page = browser.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/genome/roadmap/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("h1", timeout=PAGE_TIMEOUT)
        assert "Transformation Roadmap" in page.locator("h1").inner_text()

        page.get_by_role("button", name="Generate roadmap").click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "Transformation Roadmap" in page.locator("h1").inner_text(), (
            "generating the roadmap must still land on the roadmap page, not an error page"
        )
    finally:
        page.close()
