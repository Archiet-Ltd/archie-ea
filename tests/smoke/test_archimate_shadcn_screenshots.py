"""Screenshots of archimate shadcn templates at 1440 and 390, light and dark.

Saves to /home/ubuntu/verify/seats/mg3am037e/walk/.
"""

import os
import pytest

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
SCREENSHOT_DIR = "/home/ubuntu/verify/seats/mg3am037e/walk"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def _assert_persona_is_enterprise_architect(seeded):
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        email = seeded["emails"]["enterprise_architect"]
        user = User.query.filter_by(email=email).first()
        assert user is not None, "enterprise_architect user missing from seed"
        assert user.enterprise_role == "enterprise_architect"
        assert user.is_platform_admin is False
        assert user.is_org_admin is False
        db.session.remove()


def _screenshot(page, name, width, dark=False):
    """Set viewport, optionally toggle dark mode, and save screenshot."""
    page.set_viewport_size({"width": width, "height": 900})

    # Toggle dark mode if needed via the theme toggle
    if dark:
        # Try clicking the theme toggle if it exists
        toggle = page.locator("[data-testid='theme-toggle'], button:has-text('Dark'), button:has-text('Light')").first
        if toggle.is_visible():
            toggle.click()
            page.wait_for_timeout(500)

    theme = "dark" if dark else "light"
    filename = f"{name}_{width}w_{theme}.png"
    filepath = os.path.join(SCREENSHOT_DIR, filename)
    page.screenshot(path=filepath, full_page=True)
    print(f"  Saved {filepath}")
    return filepath


@pytest.mark.smoke
def test_import_diagram_screenshots(browser, live_server, seeded):
    """Screenshots of the import diagram page."""
    _assert_persona_is_enterprise_architect(seeded)
    email = seeded["emails"]["enterprise_architect"]

    for width, dark in [(1440, False), (1440, True), (390, False), (390, True)]:
        context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900})
        page = context.new_page()
        _login(page, live_server, email)
        page.goto(live_server + "/archimate/import/diagram", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)
        _screenshot(page, "import_diagram", width, dark)
        context.close()


@pytest.mark.smoke
def test_review_queue_screenshots(browser, live_server, seeded):
    """Screenshots of the review queue page."""
    _assert_persona_is_enterprise_architect(seeded)
    email = seeded["emails"]["enterprise_architect"]

    for width, dark in [(1440, False), (1440, True), (390, False), (390, True)]:
        context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900})
        page = context.new_page()
        _login(page, live_server, email)
        page.goto(live_server + "/capability-map/archimate/review-queue", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)
        _screenshot(page, "review_queue", width, dark)
        context.close()


@pytest.mark.smoke
def test_diagrams_library_screenshots(browser, live_server, seeded):
    """Screenshots of the diagrams library page."""
    _assert_persona_is_enterprise_architect(seeded)
    email = seeded["emails"]["enterprise_architect"]

    for width, dark in [(1440, False), (1440, True), (390, False), (390, True)]:
        context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900})
        page = context.new_page()
        _login(page, live_server, email)
        page.goto(live_server + "/archimate/diagrams", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)
        _screenshot(page, "diagrams_library", width, dark)
        context.close()