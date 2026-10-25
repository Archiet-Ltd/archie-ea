"""Take implementation planning dashboard screenshots at 1440 and 390, light and dark.

Saves to /home/ubuntu/verify/seats/mg3ip056e/walk/ and extends the journey
test to capture screenshots as the portfolio manager persona.
"""

import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROLE = "portfolio_manager"
SCREENSHOT_DIR = "/home/ubuntu/verify/seats/mg3ip056e/walk"


def _seed_work_package(app, org_id, user_id, name):
    """Record one work package through the canonical service."""
    from app.services import work_package_service

    with app.app_context():
        work_package_service.create_work_package(
            organization_id=org_id,
            user_id=user_id,
            name=name,
            status="in_progress",
            priority="high",
            description="Serves the migration roadmap.",
        )
        from app import db
        db.session.commit()


def _assert_persona(app, email):
    """Assert the signed-in user is the portfolio manager, not an administrator."""
    from app.middleware.tenant_decorators import is_platform_admin
    from app.models.user import User
    from app.services.rbac_service import rbac_service

    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == ROLE, f"expected {ROLE}, got {user.enterprise_role}"
        assert not is_platform_admin(user), "user is a platform admin"
        assert not rbac_service.is_org_admin(user, user.organization_id), "user is an org admin"


def test_implementation_dashboard_screenshots(browser, live_server):
    """Sign in as portfolio manager, navigate to dashboard, take screenshots."""
    from app import create_app

    app = create_app("testing")
    org = create_fresh_org(ROLE)
    _assert_persona(app, org["emails"][ROLE])
    _seed_work_package(app, org["org_id"], org["user_ids"][ROLE], "Cloud Foundations")

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    for width, label in [(1440, "1440"), (390, "390")]:
        for scheme, scheme_label in [("light", "light"), ("dark", "dark")]:
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": width, "height": 1000},
                color_scheme=scheme,
            )
            context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
            page = context.new_page()
            try:
                sign_in(page, live_server, org["emails"][ROLE])
                page.goto(live_server + "/implementation/",
                          wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

                # Wait for heading to confirm page loaded
                heading = page.get_by_role("heading", name="Implementation Planning")
                expect(heading).to_be_visible()

                # Wait for the timeline widget to load
                c3_region = page.locator("[data-entelim-form='C3']")
                expect(c3_region).to_be_visible()
                expect(c3_region.get_by_text("Cloud Foundations")).to_be_visible(timeout=PAGE_TIMEOUT)

                # Full page screenshot
                page.screenshot(
                    path=os.path.join(SCREENSHOT_DIR, f"dashboard-{label}-{scheme_label}.png"),
                    full_page=True,
                )
            finally:
                context.close()