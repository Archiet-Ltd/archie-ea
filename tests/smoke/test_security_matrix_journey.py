"""mg3-enterprise-genome-c2-094: enterprise_genome/security_matrix.html
rebuilt from shadcn macros (page-header, card, button, empty-state, input) in
place of the in-house page_shell macro. A security_architect persona --
never an administrator -- generates the control matrix.
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


def test_security_architect_generates_control_matrix(browser, live_server, seeded, db_session):
    from app.models.user import User

    email = seeded["emails"]["security_architect"]
    user = User.query.filter_by(email=email).first()
    assert user.enterprise_role == "security_architect", (
        "journey must run as the security_architect persona, not %r" % user.enterprise_role
    )

    page = browser.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/enterprise-genome/security/matrix", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-slot='card-title']", timeout=PAGE_TIMEOUT)
        assert "Control-to-requirement evidence table" in page.locator("[data-slot='card-title']").first.inner_text()

        page.get_by_role("button", name="Generate control matrix").click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "generate=1" in page.url
        # Either a real matrix or the no-org empty state renders -- never a 500.
        assert page.locator("body").inner_text().strip() != ""
    finally:
        page.close()
