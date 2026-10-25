"""mg3-enterprise-genome-b-101: enterprise_genome/ai_systems.html rebuilt from
shadcn macros (page-header, button, empty-state, input). A risk persona --
never an administrator -- opens the AI Systems Register and generates it.
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


def test_risk_persona_generates_ai_systems_register(browser, live_server, seeded, db_session):
    from app.models.user import User

    email = seeded["emails"]["risk"]
    user = User.query.filter_by(email=email).first()
    assert user.enterprise_role == "risk", (
        "journey must run as the risk persona, not %r" % user.enterprise_role
    )

    page = browser.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/genome/ai-systems", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("h1", timeout=PAGE_TIMEOUT)
        assert "AI Systems Register" in page.locator("h1").inner_text()

        page.get_by_role("button", name="Generate / refresh register").click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "AI Systems Register" in page.locator("h1").inner_text(), (
            "generating the register must still land on the register page, not an error page"
        )
    finally:
        page.close()
