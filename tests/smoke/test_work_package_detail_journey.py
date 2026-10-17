"""Work package journey: open the list, click a package, see its gap, owners and progress.

The enterprise architect opens Work Packages, sees the gap named beside the package,
clicks the package name and lands on its own page showing the gap, the owner area
and the recorded progress; a reload shows them again.
"""
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def linked_package(seeded):
    from app import create_app, db
    from app.models.implementation_migration import Gap
    from app.services import work_package_service

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        tag = uuid.uuid4().hex[:6]
        gap = Gap(name="Journey gap %s" % tag, organization_id=org_id)
        db.session.add(gap)
        db.session.flush()
        wp = work_package_service.create_work_package(
            organization_id=org_id, name="Journey package %s" % tag,
            gap_id=gap.id, progress_percentage=65.0)
        db.session.commit()
        return {"wp": wp.id, "name": wp.name, "gap": gap.name}


def _login(page, base, email):
    page.goto(base + "/account/logout", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.context.clear_cookies()
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.click("#submit", no_wait_after=True)
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _assert_detail(page, package):
    body = page.locator("main, body").first
    assert package["name"] in page.locator("h1").first.inner_text()
    assert package["gap"] in page.locator('[data-testid="work-package-gaps"]').inner_text()
    assert page.locator('[data-testid="owner-panel"]').is_visible()
    assert "65%" in page.locator('[data-testid="work-package-progress"]').inner_text()
    assert body is not None


def test_click_package_shows_gap_owner_area_and_progress(page, live_server, seeded, linked_package):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/enterprise/implementation/work-packages",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill('input[type="search"]', linked_package["name"])
    row = page.locator("tr", has_text=linked_package["name"])
    row.wait_for(timeout=PAGE_TIMEOUT)
    assert linked_package["gap"] in row.locator('[data-testid="work-package-gap"]').inner_text()
    row.locator('[data-testid="work-package-link"]').click()
    page.wait_for_url("**/work-packages/%d" % linked_package["wp"], timeout=PAGE_TIMEOUT)
    _assert_detail(page, linked_package)
    page.reload(wait_until="domcontentloaded")
    _assert_detail(page, linked_package)
