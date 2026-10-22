"""Every link to a work package lands on its page, on a database with no flag row.

A new organisation has a gap, a plateau and a work package that closes the gap
and has an owner. The database has no implementation planning flag row (a fresh
database, and production). The architect opens gap analysis, clicks the work
package against the gap and lands on its page, which shows the owner and the gap
it closes. They do the same from the work packages list.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

FLAG = "architecture_implementation_planning"


@pytest.fixture
def no_flag_row(live_server):
    """The database as a fresh one is: no row for the implementation planning flag.
    Put back afterwards so the other journeys in the session are unchanged."""
    from app import create_app, db
    from app.models.feature_flags import FeatureFlag
    from tests.conftest import seed_implementation_planning_flag

    app = create_app("testing")
    with app.app_context():
        FeatureFlag.query.filter_by(key=FLAG).delete()
        db.session.commit()
        assert FeatureFlag.query.filter_by(key=FLAG).count() == 0
        db.session.remove()
    yield
    with app.app_context():
        seed_implementation_planning_flag()
        db.session.remove()


def _estate(page, org):
    """A gap, a plateau and an owned work package that closes the gap."""
    suffix = org["suffix"]
    names = {
        "gap": "Billing has no owner %s" % suffix,
        "wp": "Appoint billing owner %s" % suffix,
        "plateau": "Target state %s" % suffix,
        "owner": "Launch enterprise_a",
    }
    page.goto(page.url, wait_until="domcontentloaded")
    status, plateau = api(page, "POST", "/capability-map/api/roadmap/plateaus",
                          {"name": names["plateau"], "sequence_order": 1})
    assert status == 201, plateau
    status, gap = api(page, "POST", "/capability-map/api/roadmap/gaps",
                      {"name": names["gap"], "gap_type": "coverage", "priority": "high"})
    assert status in (200, 201), gap
    status, wp = api(page, "POST", "/capability-map/api/roadmap/work-packages", {
        "name": names["wp"], "plateau_id": plateau["plateau"]["id"], "gap_id": gap["gap"]["id"],
        "start_date": "2026-11-01", "target_date": "2027-01-31",
    })
    assert status == 201, wp
    return names, wp["work_package"]["id"]


def _set_owner(org, wp_id):
    from app import create_app, db
    from app.models.unified_work_package import UnifiedWorkPackage

    app = create_app("testing")
    with app.app_context():
        row = db.session.get(UnifiedWorkPackage, wp_id)
        row.owner_id = org["user_ids"]["enterprise_architect"]
        db.session.commit()
        db.session.remove()


def _assert_work_package_page(page, names, wp_id):
    page.wait_for_url(re.compile(r"/implementation/work-packages/%d$" % wp_id), timeout=PAGE_TIMEOUT)
    body = page.locator("body")
    expect(body).not_to_contain_text("We couldn't find that page")
    expect(page.get_by_role("heading", name=names["wp"]).first).to_be_visible()
    expect(page.get_by_test_id("wp-owner")).to_have_text(names["owner"])
    expect(page.get_by_test_id("wp-gaps")).to_contain_text(names["gap"])
    expect(page.get_by_test_id("wp-plateaus")).to_contain_text(names["plateau"])


def _shoot(page, label):
    out = os.environ.get("WP01_SHOTS")
    if not out:
        return
    Path(out).mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(Path(out) / label), full_page=True)


def test_work_package_links_land_on_the_page_with_no_flag_row(browser, live_server, no_flag_row):
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        names, wp_id = _estate(page, org)
        _set_owner(org, wp_id)

        # From gap analysis: the work package listed against the gap.
        page.goto(live_server + "/enterprise/implementation/gap-analysis",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        gap_row = page.locator("tr").filter(has_text=names["gap"])
        link = gap_row.get_by_test_id("gap-work-package-link").filter(has_text=names["wp"])
        expect(link).to_have_count(1)
        link.click()
        _assert_work_package_page(page, names, wp_id)
        _shoot(page, "work-package-from-gap-analysis-1440-light.png")

        # From the work packages list.
        page.goto(live_server + "/enterprise/implementation/work-packages",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("work-package-link").filter(has_text=names["wp"]).click()
        _assert_work_package_page(page, names, wp_id)
    finally:
        context.close()

    # The same page in the dark theme (screenshots only when asked for).
    if os.environ.get("WP01_SHOTS"):
        dark = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000},
                                   color_scheme="dark")
        dark.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
                             "localStorage.setItem('theme', 'dark');")
        dpage = dark.new_page()
        try:
            sign_in(dpage, live_server, org["emails"]["enterprise_architect"])
            dpage.goto(live_server + "/enterprise/implementation/gap-analysis",
                       wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            dpage.locator("tr").filter(has_text=names["gap"]).get_by_test_id(
                "gap-work-package-link").filter(has_text=names["wp"]).click()
            _assert_work_package_page(dpage, names, wp_id)
            _shoot(dpage, "work-package-from-gap-analysis-1440-dark.png")
        finally:
            dark.close()
