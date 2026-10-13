"""A person can clear a capability's maturity level from the edit screen.

The edit form offers an explicit "Clear level" choice next to "Leave
unchanged". This drives the real select and the real Save button as a platform
administrator, then reloads the page and checks the level is gone and the
assessment history shows the clearing.
"""

import uuid

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
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def test_platform_admin_clears_a_current_level_and_it_persists(
    live_server, seeded, browser, app
):
    from app import db
    from app.models.business_capabilities import BusinessCapability
    from app.models.capability_models import CapabilityMaturityAssessment
    from app.models.user import User

    email = seeded["emails"]["platform_admin"]
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        org_id = user.organization_id
        cap = BusinessCapability(
            name=f"Clearable {uuid.uuid4().hex[:6]}",
            organization_id=org_id,
            current_maturity_level=3,
            target_maturity_level=4,
        )
        db.session.add(cap)
        db.session.commit()
        cap_id = cap.id

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, email)
        page.goto(
            f"{live_server}/capability-maturity/edit/{cap_id}",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        page.select_option("#current_maturity_level", "clear")
        page.locator("form button[type=submit]", has_text="Save Changes").click()
        page.wait_for_url(lambda url: "/edit/" not in url, timeout=PAGE_TIMEOUT)

        page.goto(
            f"{live_server}/capability-maturity/edit/{cap_id}",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        assert page.locator("#current_maturity_level").input_value() == ""
        assert page.locator("#target_maturity_level").input_value() == "4"
    finally:
        ctx.close()

    with app.app_context():
        history = CapabilityMaturityAssessment.query.filter_by(
            business_capability_id=cap_id
        ).all()
        assert len(history) == 1
        assert "cleared" in (history[0].notes or "").lower()
