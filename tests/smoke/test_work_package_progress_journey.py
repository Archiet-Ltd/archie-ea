"""A person records a work package's progress and sees it with its source.

An architect opens a work package whose dates say it is well under way but whose progress
nobody recorded, and reads "Not recorded" rather than a share of the calendar. They open the
edit form, enter a progress value, save, land back on the record with a "Saved" notice and
read the value with who entered it. Checked at 1440 light and 390 dark.
"""

import os
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [("1440 light", 1440, 900, False), ("390 dark", 390, 800, True)]
NAME = "Progress journey rollout"


@pytest.fixture(scope="module")
def package(seeded):
    from app import create_app, db
    from app.services import work_package_service

    app = create_app("testing")
    with app.app_context():
        wp = work_package_service.create_work_package(
            organization_id=seeded["ids"]["org"], name=NAME, status="in_progress",
            start_date=date.today() - timedelta(days=25), end_date=date.today() + timedelta(days=5),
        )
        db.session.commit()
        wp_id = wp.id
        db.session.remove()
    return wp_id


def _shot(page, tmp_path, label, step):
    folder = Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / ("work-package-progress-%s-%s.png" % (step, label.replace(" ", "-")))))


@pytest.mark.parametrize("label,width,height,dark", VIEWPORTS)
def test_recording_progress_shows_it_with_its_source(
    browser, live_server, seeded, package, tmp_path, label, width, height, dark,
):
    from app import create_app, db
    from app.services import work_package_service

    # each viewport starts from nothing recorded
    app = create_app("testing")
    with app.app_context():
        wp = work_package_service.get_work_package(package, seeded["ids"]["org"])
        wp.progress_percentage = 0
        db.session.commit()
        db.session.remove()

    ctx = browser.new_context(viewport={"width": width, "height": height})
    if dark:
        ctx.add_init_script("document.documentElement.classList.add('dark');")
    page = ctx.new_page()
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", seeded["emails"]["enterprise_architect"])
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)

        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("[data-testid='work-package-link']", has_text=NAME).first.click()
        page.wait_for_url("**/implementation/work-packages/%d" % package, timeout=PAGE_TIMEOUT)

        # the calendar is not progress: the share of days gone is its own line
        progress = page.locator("[data-testid='wp-progress']")
        expect(progress).to_contain_text("Not recorded")
        expect(progress).not_to_contain_text("%")
        expect(page.locator("[data-testid='wp-days-gone']")).to_contain_text("25 of 30 days gone")
        _shot(page, tmp_path, label, "before")

        page.get_by_role("link", name=re.compile("^\\s*Edit")).first.click()
        page.wait_for_url("**/work-packages/%d/edit" % package, timeout=PAGE_TIMEOUT)
        page.fill("#progress_percentage", "35")
        page.get_by_role("button", name="Edit Work Package").click()

        # back on the record, with the notice and the figure and where it came from
        page.wait_for_url(re.compile(r"/work-packages/%d$" % package), timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Saved.").first).to_be_attached()
        progress = page.locator("[data-testid='wp-progress']")
        expect(progress).to_contain_text("35%")
        expect(page.locator("[data-testid='wp-progress-source']")).to_have_text("Entered by the team")
        expect(page.locator("[data-testid='wp-facts']")).to_contain_text("Progress: 35%".replace("Progress: ", ""))
        _shot(page, tmp_path, label, "after")

        page.reload(wait_until="domcontentloaded")
        expect(page.locator("[data-testid='wp-progress']")).to_contain_text("35%")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        ctx.close()
