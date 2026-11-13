"""A work package page that a screen reader and the accessibility checker accept.

A person opens the work packages list, follows a package to its page and reads
its facts. The page must carry no serious or critical axe-core failure: the
progress bar has a name that says what it measures, and the list of facts holds
only label and value groups. Checked at 1440 and 390 wide, light and dark.
"""

import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [
    ("1440 light", 1440, 900, False),
    ("1440 dark", 1440, 900, True),
    ("390 light", 390, 800, False),
    ("390 dark", 390, 800, True),
]
NAME = "Accessible rollout package"


@pytest.fixture(scope="module")
def package(seeded):
    from app import create_app, db
    from app.services import work_package_service

    app = create_app("testing")
    with app.app_context():
        wp = work_package_service.create_work_package(
            organization_id=seeded["ids"]["org"], name=NAME,
            status="in_progress", progress_percentage=40,
            start_date=date.today() - timedelta(days=10),
            end_date=date.today() + timedelta(days=20),
        )
        db.session.commit()
        wp_id = wp.id
        db.session.remove()
    return wp_id


def _shot(page, tmp_path, label):
    folder = Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / ("work-package-%s.png" % label.replace(" ", "-"))))


def _serious(page):
    from axe_playwright_python.sync_playwright import Axe

    report = Axe().run(page, options={"runOnly": {"type": "tag", "values": [
        "wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]}})
    data = report.response if hasattr(report, "response") else report
    return [v for v in data.get("violations", []) if v.get("impact") in ("serious", "critical")]


@pytest.mark.parametrize("label,width,height,dark", VIEWPORTS)
def test_work_package_page_has_no_serious_axe_failure(
    browser, live_server, seeded, package, tmp_path, label, width, height, dark,
):
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

        # What a person needs: the figures agree between the text and the bar.
        expect(page.locator("[data-testid='wp-progress']")).to_contain_text("40%")
        facts = page.locator("[data-component='fact-list'][data-testid='wp-facts']")
        expect(facts).to_have_count(1)
        assert facts.locator("dt").all_inner_texts() == [
            "Changes capability", "Status", "Owner", "Progress", "Dates"]
        expect(facts.locator("[data-testid='property-status']")).to_have_text("In progress")
        expect(facts.locator("[data-testid='property-dates']")).to_contain_text(" to ")
        # the bar stays inside its cell and no value is cut at any width
        cell = facts.locator("[data-testid='property-progress']").bounding_box()
        barbox = page.locator("[role=progressbar]").locator("xpath=..").bounding_box()
        assert barbox["x"] >= cell["x"] - 1 and barbox["x"] + barbox["width"] <= cell["x"] + cell["width"] + 1
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        bar = page.locator("[role=progressbar]")
        expect(bar).to_have_count(1)
        assert bar.get_attribute("aria-valuenow") == "40"
        assert (bar.get_attribute("aria-label") or "").strip(), "the progress bar has no name"
        _shot(page, tmp_path, label)

        violations = _serious(page)
        assert not violations, "%s: %s" % (label, "; ".join(
            "%s [%s] (%d)" % (v["id"], v["impact"], len(v.get("nodes") or [])) for v in violations))
    finally:
        ctx.close()
