"""A startup CTO opens the demo organisation and finds it linked end to end.

The demo company is seeded through the real command (twice). The person signs
in as the demo architect and opens the risk register: every risk but one lists
the applications it is linked to, the one deliberately unlinked risk says so,
and each link lands on a page with content that links back to the risk. An
application shows an owner and a cost that agree with the applications list's
owner line and the overview's application count, and a gap shows the work
package that closes it, which opens on a page naming the plateau it delivers.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
UNLINKED = "Supplier price rise not yet assessed"


def _seed_demo_company():
    """Run the real seed command logic twice and make the demo login usable."""
    import os

    from app import create_app, db
    from app.commands.seed_demo_company import _RISK_LINKS, seed_demo_company
    from app.models.user import User
    from tests.conftest import seed_implementation_planning_flag

    app = create_app("testing")
    with app.app_context():
        os.environ["DEMO_USER_PASSWORD"] = PASSWORD
        try:
            seed_demo_company()
            seed_demo_company()
        finally:
            del os.environ["DEMO_USER_PASSWORD"]
        user = User.query.filter_by(email=DEMO_EMAIL).first()
        user.password = PASSWORD
        db.session.commit()
        seed_implementation_planning_flag()
        db.session.remove()
    return _RISK_LINKS


def test_demo_organisation_is_linked_end_to_end(live_server, browser):
    risk_links = _seed_demo_company()
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    base = live_server
    from .fresh_org import sign_in

    sign_in(page, base, DEMO_EMAIL)

    # 1. The risk register: every risk but one lists its applications.
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    rows = page.locator("[data-risk-row]")
    expect(rows).to_have_count(len(risk_links) + 1)
    for title, apps in risk_links.items():
        row = rows.filter(has_text=title)
        expect(row).to_have_count(1)
        touches = row.locator("p", has_text=re.compile(r"Linked to|Touches:"))
        expect(touches.first).to_be_visible()
        listed = touches.first.locator("a").all_inner_texts()
        for app_name in apps:
            assert app_name in listed, (title, app_name, listed)
        assert 1 <= len(apps) <= 3
    unlinked = rows.filter(has_text=UNLINKED)
    expect(unlinked.locator("[data-risk-unlinked]")).to_contain_text(
        "Not linked to any application, solution or programme yet")
    expect(unlinked).not_to_contain_text("Touches:")
    expect(unlinked).not_to_contain_text("Linked to")

    # 2. A link lands on the application, which shows an owner, a cost, and the risk back.
    title = "Calibration Ledger data corruption"
    rows.filter(has_text=title).get_by_role("link", name="Calibration Ledger", exact=True).first.click()
    page.wait_for_load_state("domcontentloaded")
    body = page.locator("body")
    expect(body).not_to_contain_text("We couldn't find that page")
    expect(page.locator("main")).to_contain_text("Calibration Ledger", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text("Calibration Specialist")  # owner
    app_url = page.url
    assert app_url.rstrip("/").endswith("/fact-sheet"), app_url  # the register links to the fact sheet
    page.reload(wait_until="domcontentloaded")
    sheet = page.locator("main")
    expect(sheet).to_contain_text("Total cost of ownership")
    expect(sheet).to_contain_text("42,000")
    expect(sheet).to_contain_text("Morgan Calibrator")
    expect(sheet).to_contain_text(title)  # the risk lists back on the application

    # 3. The applications list and the overview agree on the portfolio and its owners.
    page.goto(base + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text("20 of 20 applications in the portfolio have a named owner")
    expect(page.locator("main")).to_contain_text("of 20")
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text(re.compile(r"APPLICATIONS\s*20", re.I))

    # 4. A gap shows its work package; the work package opens on its plateau.
    page.goto(base + "/enterprise/implementation/gap-analysis",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    gap_row = page.locator("tr", has_text="Calibration throughput gap").first
    expect(gap_row).to_contain_text("Deploy automated calibration rigs")
    gap_row.get_by_role("link", name="Deploy automated calibration rigs").click()
    page.wait_for_url(re.compile(r"/implementation/work-packages/\d+$"), timeout=PAGE_TIMEOUT)
    expect(page.locator("body")).not_to_contain_text("We couldn't find that page")
    expect(page.get_by_test_id("wp-gaps")).to_contain_text("Calibration throughput gap")
    expect(page.get_by_test_id("wp-plateaus")).to_contain_text("Transition State 1")
    context.close()
